"""Adversarial workspace-boundary tests; no synthetic EDA acceptance claims."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from chipflow.api import create_app
from chipflow.importing import discover_sources
from chipflow.reports import render_report
from chipflow.storage import save
from chipflow.verification import simulation_result


def test_import_rejects_fifo_without_opening_it(tmp_path):
    # The production reader must reject it rather than waiting for a pipe writer.
    os.mkfifo(tmp_path / "trap.v")
    with pytest.raises(ValueError, match="regular"):
        discover_sources(tmp_path)


def test_import_symlink_does_not_read_external_design(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    secret = tmp_path / "secret.v"
    secret.write_text("module proprietary; endmodule\n")
    (project / "linked.v").symlink_to(secret)
    result = discover_sources(project)
    assert result["files"] == []
    assert result["modules"] == []
    assert any("Symlink" in warning for warning in result["warnings"])


def test_import_unterminated_comments_do_not_invent_modules(tmp_path):
    (tmp_path / "comment.v").write_text("/*" * 10000 + "\nmodule hidden; endmodule\n")
    assert discover_sources(tmp_path)["modules"] == []


def test_waveform_api_rejects_symlink_escape_and_hostile_origin(tmp_path):
    run_id = "a" * 32
    root = tmp_path / "runs"
    directory = root / run_id
    directory.mkdir(parents=True)
    save(
        directory / "run.json",
        {
            "schema_version": 1,
            "id": run_id,
            "name": "test",
            "target": "sim",
            "status": "failed",
            "created": time.time(),
            "stages": [],
            "checks": {},
        },
    )
    secret = tmp_path / "secret.vcd"
    secret.write_text("$comment proprietary $end\n")
    (directory / "waveform.vcd").symlink_to(secret)
    with TestClient(create_app(root), base_url="http://localhost") as client:
        response = client.get(f"/api/runs/{run_id}/waveform")
        assert response.status_code == 404
        assert "proprietary" not in response.text
        assert (
            client.get(
                f"/api/runs/{run_id}/report", headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/import", params={"directory": str(tmp_path)}, headers={"host": "evil.example"}
            ).status_code
            == 403
        )


def test_report_escapes_markup_and_preserves_explicit_config_values():
    secret = "explicit-value-is-part-of-project-configuration"
    report = {
        "name": "</title><script>alert(1)</script>",
        "configuration": {"execution": {"environment": {"NONSECRET_SETTING": secret}}},
        "error": "<img src=x onerror=alert(1)>",
    }
    output = render_report(report, "html")
    assert "<script>alert(1)</script>" not in output
    assert "<img src=x" not in output
    assert "&lt;script&gt;" in output
    assert secret in output  # Export is transparent, not a secret-redaction service.
    assert json.loads(render_report(report, "json")) == report


@pytest.mark.parametrize(
    "text", ["FAIL: expected PASS but got 0\n", "PASS\nERROR: test failed\n", "NOT PASS\n"]
)
def test_failure_text_cannot_be_accepted_as_default_pass_marker(text):
    assert simulation_result(text, 0, "PASS")["status"] != "passed"


def test_invalid_waveform_is_actionable_client_error(tmp_path):
    run_id = "b" * 32
    root = tmp_path / "runs"
    directory = root / run_id
    directory.mkdir(parents=True)
    save(
        directory / "run.json",
        {
            "schema_version": 1,
            "id": run_id,
            "name": "bad-waveform",
            "target": "sim",
            "status": "failed",
            "created": time.time(),
            "stages": [],
            "checks": {},
        },
    )
    (directory / "waveform.vcd").write_text("not a VCD\n")
    with TestClient(
        create_app(root), base_url="http://localhost", raise_server_exceptions=False
    ) as client:
        response = client.get(f"/api/runs/{run_id}/waveform")
        assert response.status_code == 400
        assert "waveform" in response.json()["detail"].lower()


def test_container_cancel_allows_guardian_cleanup_budget(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from chipflow.engine import Cancelled, Worker
    from chipflow.execution import ExecutionConfig

    # This runtime double verifies actual guardian/process supervision only.
    # rm's seven seconds are inside cleanup_container's real fifteen-second budget.
    runtime = tmp_path / "docker"
    completed = tmp_path / "cleanup-completed"
    runtime.write_text(
        "#!/usr/bin/env python3\nimport sys,time\nfrom pathlib import Path\n"
        'if len(sys.argv)>1 and sys.argv[1]=="rm":\n'
        " time.sleep(7)\n"
        f" Path({str(completed)!r}).touch()\n"
        "else:\n time.sleep(30)\n"
    )
    runtime.chmod(0o700)
    monkeypatch.setattr("chipflow.execution.runtime_path", lambda config: str(runtime))
    worker = Worker.__new__(Worker)
    worker.d = tmp_path
    worker.state = {}
    worker.flow_lock = None
    worker.machine_lock = None
    worker.cfg = SimpleNamespace(
        execution=ExecutionConfig(backend="container", image="test@sha256:" + "0" * 64),
        tcl=None,
        orfs="/missing",
    )
    cancel = threading.Timer(0.3, lambda: (tmp_path / "cancel").touch())
    cancel.start()
    try:
        with pytest.raises(Cancelled):
            worker.command(["sleep", "30"], tmp_path, 60)
    finally:
        cancel.join()
    assert completed.exists(), "Guardian cleanup was killed before its allowed timeout"


def test_real_simulation_strict_retry_preserves_generated_constraint_identity(tmp_path):
    import shutil

    import yaml

    from chipflow.engine import Store

    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Native Icarus binaries unavailable")
    (tmp_path / "dut.v").write_text("module dut; endmodule\n")
    (tmp_path / "tb.v").write_text(
        'module tb; initial begin $display("PASS"); $finish; end endmodule\n'
    )
    config = {
        "name": "strict_retry",
        "top": "dut",
        "rtl": ["dut.v"],
        "clocks": [{"port": "clk", "period_ns": 10}],
        "simulation": {"top": "tb", "testbench": ["tb.v"]},
        "reproducibility": {"retry_mismatch": "strict"},
    }
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(config))
    store = Store(tmp_path / "runs")

    def completed(state):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = store.get(state["id"])
            if state["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                assert state["status"] == "completed", state.get("error")
                return state
            time.sleep(0.05)
        store.cancel(state["id"])
        pytest.fail("Real simulation exceeded integration-test deadline")

    first = completed(store.start(path))
    retry = completed(store.retry(first["id"]))
    assert retry["reproducibility"]["compatible"]
    assert retry["reproducibility"]["differences"] == []
    for state in (first, retry):
        directory = store.directory(state["id"])
        assert json.loads((directory / "constraints.json").read_text())["generated_simplified"]
        assert (
            "inputs/clocks.sdc"
            in json.loads((directory / "manifest.json").read_text())["input_sha256"]
        )
