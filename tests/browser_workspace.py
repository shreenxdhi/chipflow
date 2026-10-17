"""Real local-tool browser regression for workspace additions; server required."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

url = os.environ.get("CHIPFLOW_URL", "http://127.0.0.1:8088")
with sync_playwright() as p:
    executable = os.environ.get("CHIPFLOW_BROWSER") or shutil.which("google-chrome")
    browser = p.chromium.launch(
        **({"executable_path": executable} if executable else {}),
        headless=True,
        args=["--no-sandbox"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, base_url=url)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    expect(page.locator("#connection")).to_have_text("● Local workspace")
    page.locator("#experience").select_option("standard")
    expect(page.locator("#learning")).not_to_be_visible()
    expect(page.locator("#edit-yaml")).not_to_be_visible()
    page.locator("#theme").select_option("dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(page.locator("#experience")).to_have_value("standard")
    expect(page.locator("#theme")).to_have_value("dark")
    page.locator("#theme").select_option("light")
    example = Path(page.request.get("/api/workspace").json()["example"])
    page.locator("#import-project").click()
    page.locator("#import-directory").fill(str(example.parent))
    page.get_by_role("button", name="Discover sources", exact=True).click()
    expect(page.locator("#import-results")).to_contain_text("counter")
    page.get_by_role("button", name="Review selected sources", exact=True).click()
    expect(page.locator("#import-error")).to_contain_text("Choose at least one source")
    page.locator('#import-results input[type="checkbox"]').evaluate_all(
        "items => items.filter(item => item.dataset.path.endsWith('/counter.v')).forEach(item => item.click())"
    )
    page.get_by_role("combobox", name="Imported top module").select_option("counter")
    page.get_by_role("button", name="Review selected sources", exact=True).click()
    expect(page.locator("#p-top")).to_have_value("counter")
    expect(page.locator("#p-rtl")).to_have_value(str(example.parent / "counter.v"))
    page.locator("#p-name").fill("standard_imported_counter")
    page.locator("#p-tb").fill(str(example.parent / "tb.v"))
    page.locator("#p-simtop").fill("tb")
    page.locator("#p-marker").fill("PASS: counter self-check")
    page.locator("#save-project").click()
    expect(page.locator("#project-dialog")).not_to_be_visible()
    saved_path = page.locator("#config").input_value()
    import yaml

    assert yaml.safe_load(Path(saved_path).read_text())["quality"]["policy"] == "standard"
    page.locator("#target").select_option("sim")
    with page.expect_response(
        lambda r: r.url.endswith("/api/runs") and r.request.method == "POST"
    ) as response:
        page.locator("#start-button").click()
    run_id = response.value.json()["id"]
    deadline = time.monotonic() + 60
    while True:
        run = page.request.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"completed", "failed", "interrupted", "cancelled"}:
            break
        assert time.monotonic() < deadline
        page.wait_for_timeout(100)
    assert run["status"] == "completed", run
    expect(page.locator(".result-banner")).to_contain_text("simulation passed", timeout=30000)
    expect(page.get_by_role("region", name="Run quality gates")).to_contain_text(
        "Execution: Completed"
    )
    page.get_by_role("button", name="Inspect RTL simulation stage", exact=True).click()
    expect(page.locator("#stage-dialog")).to_be_visible()
    expect(page.locator("#stage-content")).to_contain_text("completed")
    expect(page.locator("#stage-content")).to_contain_text(".vcd")
    page.get_by_role("button", name="Open full run log", exact=True).click()
    expect(page.locator("#log-output")).to_contain_text("PASS")
    page.locator("#log-severity").select_option("errors")
    expect(page.locator("#log-output")).to_have_text("No matching log lines.")
    page.locator("#log-severity").select_option("all")
    expect(page.locator("#log-output")).to_contain_text("PASS")
    page.get_by_role("tab", name="Waveform", exact=True).click()
    expect(page.locator("#waveform-drawing svg")).to_be_visible()
    expect(page.locator("#waveform-content")).to_contain_text("Timescale")
    page.locator("#waveform-search").fill("clk")
    expect(page.locator(".waveform-signals")).to_contain_text("clk")
    page.locator("#waveform-cursor-a").fill("10")
    page.locator("#waveform-cursor-b").fill("30")
    expect(page.locator("#waveform-measurement")).to_contain_text("20 ticks")
    before = float(page.locator("#waveform-end").input_value()) - float(
        page.locator("#waveform-start").input_value()
    )
    page.get_by_role("button", name="Zoom in", exact=True).click()
    after = float(page.locator("#waveform-end").input_value()) - float(
        page.locator("#waveform-start").input_value()
    )
    assert after == before / 2
    page.get_by_role("button", name="Fit recorded time", exact=True).click()
    for format in ["html", "markdown", "json"]:
        report = page.request.get(f"/api/runs/{run_id}/report?format={format}")
        assert report.status == 200, report.text()
        assert run_id in report.text()
    # Native rendering of a geometric GDS fixture tests the viewer only. It is
    # explicitly not physical-flow acceptance for the simulated counter.
    preview_evidence = "not executed: optional KLayout Python package unavailable"
    try:
        import klayout.db as db
    except ImportError:
        pass
    else:
        from chipflow.inspection import preview_gds_plan

        snapshot = page.request.get(f"/api/runs/{run_id}/artifacts/project.json").json()
        run_folder = next(
            parent for parent in Path(snapshot["rtl"][0]).parents if parent.name == run_id
        )
        layout = db.Layout()
        layout.dbu = 0.001
        top = layout.create_cell("viewer_fixture")
        top.shapes(layout.layer(1, 0)).insert(db.Box(0, 0, 100000, 80000))
        fixture = run_folder / "viewer-fixture.gds"
        layout.write(str(fixture))
        plan = preview_gds_plan(fixture, run_folder, "viewer_fixture")
        result = subprocess.run(
            plan.commands[0], cwd=plan.cwd, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        metadata = json.loads((run_folder / "layout-preview.json").read_text())
        assert metadata["status"] == "passed", metadata
        page.reload()
        page.get_by_role("tab", name="Files", exact=True).click()
        image = page.locator(".layout-preview img")
        expect(image).to_be_visible()
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("image => image.naturalWidth") > 0
        expect(page.locator(".layout-preview")).to_contain_text("does not establish")
        preview_evidence = "passed with real KLayout geometric fixture; no physical flow claimed"
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#theme").select_option("dark")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    print(
        json.dumps(
            {
                "real_icarus_simulation": "passed",
                "import_review": "passed",
                "stage_artifacts": "passed",
                "waveform_inspection": "passed",
                "reports": "passed",
                "native_layout_preview": preview_evidence,
                "mode_theme_persistence": "passed",
                "log_filter": "passed",
                "mobile_overflow": False,
                "browser_errors": errors,
            }
        )
    )
    browser.close()
