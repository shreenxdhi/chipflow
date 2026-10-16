"""Optional Chrome smoke test; run with the local dashboard listening on 8087."""

import json
import os
import re
from pathlib import Path

import yaml
from playwright.sync_api import expect, sync_playwright

ARTIFACTS = Path(os.environ.get("CHIPFLOW_TEST_ARTIFACTS", ".chipflow/browser-test-artifacts"))
ARTIFACTS.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get("CHIPFLOW_BROWSER", "/usr/bin/google-chrome"),
        headless=True,
        args=["--no-sandbox"],
    )
    page = browser.new_page(
        viewport={"width": 1440, "height": 1100},
        base_url=os.environ.get("CHIPFLOW_URL", "http://127.0.0.1:8087"),
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(os.environ.get("CHIPFLOW_URL", "http://127.0.0.1:8087"))
    expect(page.locator("#connection")).to_have_text("● Local workspace")
    page.get_by_role("button", name="Getting started").click()
    expect(page.locator("#help-dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#help-dialog")).not_to_be_visible()
    page.get_by_role("button", name="Installed tools", exact=True).click()
    expect(page.locator("#tools-content")).to_contain_text("iverilog")
    page.keyboard.press("Escape")

    # Missing configuration gets a useful message and creates no run.
    before = len(page.request.get("/api/runs").json())
    page.locator("#config").fill("/missing/chipflow-project.yaml")
    page.get_by_role("button", name="Check project", exact=True).click()
    expect(page.locator("#error")).to_contain_text("Project file not found")
    assert len(page.request.get("/api/runs").json()) == before
    page.get_by_role("button", name="Try the counter example").click()
    page.get_by_role("button", name="Check project", exact=True).click()
    expect(page.locator("#preflight")).to_contain_text("is ready to start")

    # Create a working design entirely through the guided form.
    page.get_by_role("button", name="Create project", exact=False).click()
    page.locator("#p-name").fill("friendly_counter")
    page.locator("#p-top").fill("counter")
    page.locator("#p-rtl").fill("examples/counter/counter.v")
    page.locator("#p-tb").fill("examples/counter/tb.v")
    page.locator("#p-simtop").fill("tb")
    page.locator("#p-marker").fill("PASS: counter self-check")
    page.get_by_role("button", name="Save project", exact=True).click()
    expect(page.locator("#project-dialog")).not_to_be_visible()
    expect(page.locator("#preflight")).to_contain_text("friendly_counter is ready")
    saved_path = page.locator("#config").input_value()
    saved = yaml.safe_load(Path(saved_path).read_text())
    assert Path(saved["rtl"][0]).is_absolute()

    with page.expect_response(
        lambda r: r.url.endswith("/api/runs") and r.request.method == "POST"
    ) as started:
        page.get_by_role("button", name="Start run").click()
    run_id = started.value.json()["id"]
    expect(page.locator(".detail .meta")).to_have_text(run_id, timeout=30000)
    expect(page.locator(".result-banner")).to_contain_text("Your simulation passed.", timeout=60000)
    with page.expect_download() as downloaded:
        page.get_by_role("link", name=re.compile("Waveform")).click()
    assert "$enddefinitions" in Path(downloaded.value.path()).read_text()

    page.get_by_role("tab", name="Live logs", exact=True).click()
    expect(page.locator("#log-output")).to_contain_text("PASS: counter self-check")
    page.get_by_role("searchbox", name="Find in logs").fill("PASS")
    expect(page.locator("#log-output")).to_contain_text("PASS: counter self-check")
    assert "$ iverilog" not in page.locator("#log-output").inner_text()
    page.get_by_role("tab", name="Files", exact=True).click()
    page.get_by_role("searchbox", name="Search output files").fill(".vcd")
    expect(page.locator(".artifacts")).to_contain_text("Waveform")
    assert "project.json" not in page.locator(".artifacts").inner_text()

    page.locator(".run input").nth(0).check()
    page.locator(".run input").nth(1).check()
    page.get_by_role("button", name="Compare selected").click()
    expect(page.locator("#comparison")).to_be_visible()
    expect(page.locator("#comparison-content table")).to_contain_text("Status")
    page.get_by_role("button", name="Close", exact=True).click()
    page.get_by_role("searchbox", name="Search runs", exact=True).fill("not-a-design-123")
    expect(page.locator("#runs")).to_contain_text("No matching runs")
    page.get_by_role("button", name="Clear filters", exact=True).click()
    page.get_by_role("tab", name="Overview", exact=True).click()

    # Run again snapshots saved inputs as an independent new run.
    with page.expect_response(
        lambda r: r.url.endswith("/retry") and r.request.method == "POST"
    ) as retried:
        page.get_by_role("button", name="Run again", exact=True).click()
    retry_id = retried.value.json()["id"]
    assert retry_id != run_id
    expect(page.locator(".detail .meta")).to_have_text(retry_id, timeout=30000)
    expect(page.locator(".result-banner")).to_contain_text("Your simulation passed.", timeout=60000)

    # Cancel a real simulation through the rendered UI.
    slow = Path(".chipflow/browser-smoke").resolve()
    slow.mkdir(parents=True, exist_ok=True)
    (slow / "dut.v").write_text("module dut; endmodule")
    (slow / "tb.v").write_text("module tb; initial forever #1; endmodule")
    (slow / "project.yaml").write_text(
        yaml.safe_dump(
            dict(
                name="browser_cancel",
                top="dut",
                rtl=["dut.v"],
                orfs=page.request.get("/api/workspace").json()["orfs"],
                clocks=[dict(port="clk", period_ns=10)],
                simulation=dict(top="tb", testbench=["tb.v"], timeout_seconds=60),
            )
        )
    )
    page.locator("#config").fill(str(slow / "project.yaml"))
    page.get_by_role("button", name="Start run").click()
    expect(page.locator(".detail-title h2")).to_have_text("browser_cancel", timeout=30000)
    page.get_by_role("button", name="Cancel run", exact=True).click()
    expect(page.locator(".detail-title .status")).to_have_text("Cancelled", timeout=30000)

    completed = [
        run
        for run in page.request.get("/api/runs").json()
        if run["target"] == "finish" and run["status"] == "completed"
    ]
    if completed:
        run_id = completed[0]["id"]
        page.locator(f'.run-select[data-run="{run_id}"]').click()
        expect(page.locator(".detail .meta")).to_have_text(run_id)
        expect(page.locator(".download-grid")).to_contain_text("Final layout")
    page.get_by_role("button", name="Try the counter example").click()
    page.locator("#target").select_option("finish")
    page.get_by_role("button", name="Check project", exact=True).click()
    expect(page.locator("#preflight")).to_contain_text("counter_demo is ready")
    page.get_by_role("button", name="Dismiss notification").click()
    page.evaluate("scrollTo(0, 0)")
    page.screenshot(path=str(ARTIFACTS / "dashboard.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("scrollTo(0, 0)")
    page.screenshot(path=str(ARTIFACTS / "dashboard-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="Create project", exact=False).click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(ARTIFACTS / "project-form-mobile.png"), full_page=False)
    page.keyboard.press("Escape")
    assert not errors, errors
    print(
        json.dumps(
            {
                "browser_errors": errors,
                "guided_project_creation": "passed",
                "preflight_errors": "passed",
                "simulation": "passed",
                "waveform_download": "passed",
                "log_search": "passed",
                "file_search": "passed",
                "comparison": "passed",
                "history_search": "passed",
                "retry": "passed",
                "cancellation": "passed",
                "mobile_overflow": False,
            }
        )
    )
    browser.close()
