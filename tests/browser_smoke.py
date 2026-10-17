"""Optional Chrome smoke test; run with the local dashboard listening on 8087."""

import json
import os
import re
import shutil
import time
from pathlib import Path

import yaml
from playwright.sync_api import expect, sync_playwright

ARTIFACTS = Path(os.environ.get("CHIPFLOW_TEST_ARTIFACTS", ".chipflow/browser-test-artifacts"))
ARTIFACTS.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    executable = os.environ.get("CHIPFLOW_BROWSER") or shutil.which("google-chrome")
    browser = p.chromium.launch(
        **({"executable_path": executable} if executable else {}),
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
    example = Path(page.request.get("/api/workspace").json()["example"])
    # Seed independent history so the smoke test also works in a fresh workspace.
    seeded = page.request.post("/api/runs", data={"config": str(example), "target": "sim"})
    assert seeded.status == 202, seeded.text()
    seed_id = seeded.json()["id"]
    deadline = time.monotonic() + 60
    while page.request.get(f"/api/runs/{seed_id}").json()["status"] not in {
        "completed",
        "failed",
        "interrupted",
    }:
        assert time.monotonic() < deadline, "Example simulation did not finish"
        time.sleep(0.1)
    assert page.request.get(f"/api/runs/{seed_id}").json()["status"] == "completed"
    page.get_by_role("button", name="Getting started").click()
    expect(page.locator("#help-dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#help-dialog")).not_to_be_visible()
    page.get_by_role("button", name="Installed tools", exact=True).click()
    expect(page.locator("#tools-content")).to_contain_text("iverilog")
    expect(page.locator("#install-option")).to_be_visible()
    page.locator("#install-option").select_option("orfs-checkout")
    expect(page.locator("#install-command")).to_contain_text("--install --option orfs-checkout")
    expect(page.locator("#install-description")).to_contain_text("pinned flow")
    previous_config = page.locator("#config").input_value()
    page.keyboard.press("Escape")
    page.locator("#config").fill("my-custom-project.yaml")
    page.get_by_role("button", name="Installed tools", exact=True).click()
    with page.expect_response(lambda response: response.url.endswith("/api/projects")):
        page.get_by_role("button", name="Recheck installed tools").click()
    expect(page.locator("#config")).to_have_value("my-custom-project.yaml")
    page.keyboard.press("Escape")
    page.locator("#config").fill(previous_config)

    # A first-time learner creates a counter and executes real Icarus simulation.
    expect(page.locator("#lessons .lesson")).to_have_count(7)
    with page.expect_response(
        lambda r: r.url.endswith("/api/runs") and r.request.method == "POST"
    ) as learning:
        page.locator("#learn-start").click()
    learning_id = learning.value.json()["id"]
    expect(page.locator(".detail .meta")).to_have_text(learning_id, timeout=30000)
    expect(page.locator("#learning-progress")).to_contain_text("1 of 7", timeout=60000)
    expect(page.locator("#learn-next")).to_contain_text("Turn RTL into gates")
    learner_path = page.locator("#config").input_value()
    page.reload()
    expect(page.locator("#learning-progress")).to_contain_text("1 of 7")

    # Gate simulation contributes synthesis once; retries follow their original project.
    history = page.request.get("/api/runs").json()
    retry_example = next(run for run in history if run["id"] == seed_id)
    retry_example["source_config"] = f"/runs/{learning_id}/project.json"
    retry_example["target"] = "gate_sim"
    retry_example["stages"] = [
        {"name": "synth", "status": "completed"},
        {"name": "gate_sim", "status": "completed"},
    ]
    page.route("**/api/runs", lambda route: route.fulfill(json=history))
    page.reload()
    expect(page.locator("#learning-progress")).to_contain_text("2 of 7")
    expect(page.locator("#learn-next")).to_contain_text("Plan the chip")
    page.unroute("**/api/runs")
    page.reload()
    expect(page.locator("#learning-progress")).to_contain_text("1 of 7")

    # Expert mode exposes a complete YAML copy, validates errors, and persists.
    page.locator("#experience").select_option("expert")
    expect(page.locator("#learning")).not_to_be_visible()
    page.locator("#config").fill(learner_path)
    page.locator("#edit-yaml").click()
    expect(page.locator("#yaml-dialog")).to_be_visible()
    full = yaml.safe_load(page.locator("#yaml-editor").input_value())
    full["timeout_seconds"] = 1234
    full["clocks"].append({"port": "aux_clk", "period_ns": 20})
    full["tcl"] = {
        "directory": str(Path(learner_path).parent / "tcl"),
        "hooks": {"POST_CTS_TCL": "after_cts.tcl"},
        "parameters": {"LABEL": "browser customization"},
    }
    full["orfs_variables"] = {"RECOVER_POWER": 0}
    page.locator("#yaml-editor").fill("rtl: [")
    page.locator("#yaml-save").click()
    expect(page.locator("#yaml-error")).to_contain_text("indentation and syntax")
    page.locator("#yaml-editor").fill(yaml.safe_dump(full))
    page.locator("#yaml-save").click()
    expect(page.locator("#yaml-dialog")).not_to_be_visible()
    full_copy = page.locator("#config").input_value()
    assert full_copy != learner_path
    assert yaml.safe_load(Path(full_copy).read_text()) == full
    page.reload()
    expect(page.locator("#experience")).to_have_value("expert")
    page.locator("#config").fill(full_copy)
    page.locator("#edit-project").click()
    expect(page.locator("#p-hooks")).to_have_value("POST_CTS_TCL=after_cts.tcl")
    page.locator("#save-project").click()
    expect(page.locator("#project-dialog")).not_to_be_visible()
    form_copy = yaml.safe_load(Path(page.locator("#config").input_value()).read_text())
    assert form_copy["timeout_seconds"] == 1234
    assert form_copy["clocks"] == full["clocks"]
    assert form_copy["tcl"] == full["tcl"]
    assert form_copy["orfs_variables"] == full["orfs_variables"]
    page.locator("#experience").select_option("learn")
    page.locator("#target").select_option("sim")

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
    page.locator("#p-rtl").fill(str(example.parent / "counter.v"))
    page.locator("#p-tb").fill(str(example.parent / "tb.v"))
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
    expect(page.locator("#log-output")).not_to_contain_text("$ iverilog")
    page.get_by_role("checkbox", name="Follow output").uncheck()
    frozen = page.locator("#log-output").inner_text()
    config_snapshot = page.request.get(f"/api/runs/{run_id}/artifacts/project.json").json()
    run_folder = next(
        parent for parent in Path(config_snapshot["rtl"][0]).parents if parent.name == run_id
    )
    with (run_folder / "console.log").open("a") as stream:
        stream.write("PASS: live output appended during pause\n")
    page.wait_for_timeout(1500)
    assert page.locator("#log-output").inner_text() == frozen
    page.get_by_role("checkbox", name="Follow output").check()
    expect(page.locator("#log-output")).to_contain_text("live output appended during pause")
    with (run_folder / "console.log").open("a") as stream:
        stream.write(("PASS bounded log output " + "x" * 80 + "\n") * 25000)
    expect(page.locator("#log-count")).to_contain_text("Showing recent output", timeout=30000)
    assert len(page.locator("#log-output").inner_text()) <= 2 * 1024 * 1024
    assert (run_folder / "console.log").stat().st_size > 2 * 1024 * 1024
    page.get_by_role("tab", name="Files", exact=True).click()
    page.get_by_role("searchbox", name="Search output files").fill(".vcd")
    expect(page.locator(".artifacts")).to_contain_text("Waveform")
    expect(page.locator(".artifacts")).not_to_contain_text("project.json")

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
    physical = page.request.post(
        "/api/projects/inspect", data={"config": str(example), "target": "finish"}
    ).json()
    page.get_by_role("button", name="Check project", exact=True).click()
    if physical["ready"]:
        expect(page.locator("#preflight")).to_contain_text("counter_demo is ready")
    else:
        expect(page.locator("#preflight")).to_contain_text("A few things need fixing first")
        expect(page.locator("#preflight")).to_contain_text(physical["issues"][0])
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
                "learning_simulation": "passed",
                "expert_yaml_and_preservation": "passed",
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
