import fcntl
import json
import signal
import subprocess
import sys
import time

import pytest
import yaml

from chipflow.engine import Store, Worker, save


def queued_worker(tmp_path, monkeypatch):
    (tmp_path / "rtl.v").write_text("module dut; endmodule")
    (tmp_path / "tb.v").write_text(
        'module tb; initial begin $display("PASS"); $finish; end endmodule'
    )
    path = tmp_path / "project.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                name="queue_test",
                top="dut",
                rtl=["rtl.v"],
                orfs="/missing/orfs",
                clocks=[dict(port="clk", period_ns=10)],
                simulation=dict(top="tb", testbench=["tb.v"]),
            )
        )
    )
    with monkeypatch.context() as patch:
        patch.setattr("chipflow.engine.subprocess.Popen", lambda *a, **k: None)
        store = Store(tmp_path / "runs")
        state = store.start(path)
    directory = store.directory(state["id"])
    state["target"] = "synth"
    save(directory / "run.json", state)
    return store, state, directory


def wait_until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for worker state")


@pytest.mark.parametrize("use_signal", [False, True])
def test_queued_physical_run_cancels_without_launching_tools(tmp_path, monkeypatch, use_signal):
    store, state, directory = queued_worker(tmp_path, monkeypatch)
    with (store.root / ".physical-flow.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        process = subprocess.Popen([sys.executable, "-m", "chipflow.worker", str(directory)])
        try:
            wait_until(lambda: store.get(state["id"]).get("queue_reason"))
            assert store.get(state["id"])["status"] == "queued"
            assert not (directory / "console.log").exists()
            if use_signal:
                process.send_signal(signal.SIGTERM)
            else:
                store.cancel(state["id"])
            assert process.wait(timeout=5) == 0
            assert store.get(state["id"])["status"] == "cancelled"
            assert not (directory / "console.log").exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_corrupt_history_does_not_hide_healthy_runs(tmp_path, monkeypatch, caplog):
    store, state, directory = queued_worker(tmp_path, monkeypatch)
    corrupt = store.root / ("a" * 32)
    corrupt.mkdir()
    (corrupt / "run.json").write_text("{broken")
    assert store.history()[0]["id"] == state["id"]
    assert "Cannot read run" in caplog.text


def test_run_directory_symlink_cannot_escape_store(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "run.json").write_text("{}")
    store = Store(tmp_path / "runs")
    (store.root / ("a" * 32)).symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        store.get("a" * 32)


def test_nonfinite_tool_metrics_do_not_pass_or_break_json(tmp_path, monkeypatch):
    store, state, directory = queued_worker(tmp_path, monkeypatch)
    logs = directory / "logs"
    logs.mkdir()
    (logs / "6_report.json").write_text(
        '{"finish__timing__setup__ws":Infinity,"finish__timing__hold__ws":0.1}'
    )
    worker = Worker(directory)
    worker.collect_metrics()
    assert worker.state["checks"]["timing"] == "unknown"
    assert worker.state["metrics"]["finish__timing__setup__ws"] is None
    json.dumps(worker.state, allow_nan=False)
