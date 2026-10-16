import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from chipflow.api import create_app
from chipflow.config import Project
from chipflow.engine import Cancelled, Store, Worker


@pytest.fixture
def project(tmp_path):
    (tmp_path / "design.v").write_text("module dut; endmodule\n")
    (tmp_path / "tb.v").write_text(
        'module tb; initial begin $display("PASS"); $finish; end endmodule\n'
    )
    config = dict(
        name="test",
        top="dut",
        rtl=["design.v"],
        orfs="/missing/orfs",
        clocks=[dict(port="clk", period_ns=10)],
        simulation=dict(top="tb", testbench=["tb.v"]),
    )
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(config))
    return path, config


@pytest.fixture
def prepare(monkeypatch, tmp_path):
    # Unit tests run workers in process; the real detached CLI is tested separately.
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)

    def create(path, target="sim"):
        store = Store(tmp_path / "runs")
        state = store.start(path, target)
        return store, state, Worker(store.directory(state["id"]))

    return create


def test_input_snapshot(project, prepare):
    path, _ = project
    store, state, worker = prepare(path)
    original = path.parent / "design.v"
    original.write_text("modified")
    assert Path(worker.cfg.rtl[0]).read_text() == "module dut; endmodule\n"
    manifest = json.loads((worker.d / "manifest.json").read_text())
    assert "inputs/clocks.sdc" in manifest["input_sha256"]
    assert len(manifest["input_sha256"]) == 3
    assert Store(store.root).history()[0]["id"] == state["id"]


def test_validation(project):
    _, config = project
    for override in [
        dict(top="bad; touch /tmp/file"),
        dict(physical={"CORE_AREA": "$(shell id)"}),
        dict(physical={"SHELL": "bash"}),
        dict(clocks=[]),
    ]:
        with pytest.raises(ValueError):
            Project.model_validate({**config, **override})


def test_traversal_and_symlink(project, prepare, tmp_path):
    store, state, worker = prepare(project[0])
    outside = tmp_path / "secret"
    outside.write_text("secret")
    (worker.d / "escape").symlink_to(outside)
    for relative in ["../../secret", str(outside), "escape"]:
        with pytest.raises(FileNotFoundError):
            store.artifact(state["id"], relative)
    with pytest.raises(ValueError):
        store.get("../runs")


def test_failure_stops_pipeline(project, prepare, monkeypatch):
    store, state, worker = prepare(project[0])
    worker.state["target"] = "finish"
    monkeypatch.setattr("chipflow.engine.doctor", lambda *a: {})

    def fail(*args):
        raise RuntimeError("deliberate tool failure")

    monkeypatch.setattr(worker, "command", fail)
    monkeypatch.setattr(worker, "compatibility_args", lambda: [])
    worker.run()
    run = store.get(state["id"])
    assert run["status"] == "failed"
    assert [s["name"] for s in run["stages"]] == ["synth"]
    assert run["checks"] == {"timing": "unknown", "physical": "unknown", "routing_drc": "unknown"}


def test_final_metrics_are_separate_from_exit(project, prepare, monkeypatch):
    store, state, worker = prepare(project[0])
    log = worker.d / "logs"
    log.mkdir()
    (log / "3_place.json").write_text(
        json.dumps({"timing__setup__ws": 100, "timing__hold__ws": 100})
    )
    worker.collect_metrics()
    assert worker.state["checks"]["timing"] == "unknown"
    (log / "6_report.json").write_text(
        json.dumps({"timing__setup__ws": -0.2, "timing__hold__ws": 0.1, "route__drc_errors": 2})
    )
    worker.collect_metrics()
    assert worker.state["checks"] == {
        "timing": "failed",
        "physical": "failed",
        "routing_drc": "failed",
    }


def test_api_boundary(project, prepare, tmp_path):
    store, state, worker = prepare(project[0])
    (worker.d / "console.log").write_text("hello\nworld\n")
    client = TestClient(create_app(store.root), base_url="http://127.0.0.1")
    assert client.get("/").status_code == 200
    assert client.get("/api/runs").json()[0]["id"] == state["id"]
    logs = client.get(f"/api/runs/{state['id']}/logs?offset=6").json()
    assert logs == {"text": "world\n", "offset": 12}
    assert client.get(f"/api/runs/{state['id']}/logs?offset=-1").status_code == 422
    assert client.get("/api/runs/invalid").status_code == 400
    assert client.get("/api/runs/" + "0" * 32).status_code == 404
    assert (
        client.post(
            f"/api/runs/{state['id']}/cancel", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert not (worker.d / "cancel").exists()
    assert client.get("/api/runs", headers={"host": "evil.example"}).status_code == 403
    assert client.post(f"/api/runs/{state['id']}/cancel").status_code == 200
    assert (worker.d / "cancel").exists()
    assert client.get(f"/api/runs/{state['id']}/artifacts/project.json").status_code == 200
    assert client.get(f"/api/compare?left={state['id']}&right={state['id']}").json()["deltas"] == {}


# Native subprocess tests below exercise Icarus and cancellation, without mocking tools.
def actual_worker(project, tmp_path):
    path, _ = project
    d = tmp_path / "actual"
    d.mkdir()
    config = yaml.safe_load(path.read_text())
    config["rtl"] = [str(path.parent / "design.v")]
    config["simulation"]["testbench"] = [str(path.parent / "tb.v")]
    (d / "project.json").write_text(json.dumps(config))
    (d / "run.json").write_text(
        json.dumps(
            {
                "id": "0" * 32,
                "created": time.time(),
                "status": "queued",
                "stages": [],
                "checks": {"timing": "unknown", "physical": "unknown"},
            }
        )
    )
    return Worker(d)


@pytest.mark.parametrize(
    "testbench,success",
    [
        ('initial begin $display("PASS"); $finish; end', True),
        ('initial begin $display("no result"); $finish; end', False),
        ('initial begin $display("PASS"); $fatal(1,"test failure"); end', False),
        ("this is invalid verilog", False),
    ],
)
def test_real_simulation(project, tmp_path, testbench, success):
    worker = actual_worker(project, tmp_path)
    Path(worker.cfg.simulation.testbench[0]).write_text("module tb; " + testbench + " endmodule")
    if success:
        worker.simulation()
    else:
        with pytest.raises(RuntimeError):
            worker.simulation()


def test_timeout_kills_process(project, tmp_path):
    worker = actual_worker(project, tmp_path)
    with pytest.raises(TimeoutError):
        worker.command([sys.executable, "-c", "import time; time.sleep(30)"], worker.d, 0.15)


def test_cancellation_kills_descendants(project, tmp_path):
    worker = actual_worker(project, tmp_path)
    script = 'import subprocess,time; p=subprocess.Popen(["sleep","30"]);open("child.pid","w").write(str(p.pid));time.sleep(30)'

    def cancel():
        until = time.monotonic() + 5
        while not (worker.d / "child.pid").exists() and time.monotonic() < until:
            time.sleep(0.02)
        (worker.d / "cancel").touch()

    thread = threading.Thread(target=cancel)
    thread.start()
    with pytest.raises(Cancelled):
        worker.command([sys.executable, "-c", script], worker.d, 10)
    thread.join()
    pid = int((worker.d / "child.pid").read_text())
    proc = Path(f"/proc/{pid}/stat")
    assert not proc.exists() or proc.read_text().split()[2] == "Z"


def test_dead_worker_history(project, prepare):
    store, state, worker = prepare(project[0])
    worker.state.update(
        status="running",
        created=time.time() - 10,
        pid=999999999,
        stages=[{"name": "sim", "status": "running"}],
    )
    worker.persist()
    recovered = Store(store.root).history()[0]
    assert recovered["status"] == "interrupted"
    assert recovered["stages"][0]["status"] == "interrupted"


def test_prefixed_native_metrics(project, prepare):
    store, state, worker = prepare(project[0])
    log = worker.d / "logs"
    log.mkdir()
    (log / "6_report.json").write_text(
        json.dumps({"finish__timing__setup__ws": 9.7, "finish__timing__hold__ws": 0.08})
    )
    (log / "5_2_route.json").write_text(json.dumps({"detailedroute__route__drc_errors": 0}))
    worker.collect_metrics()
    assert worker.state["checks"] == {
        "timing": "passed",
        "routing_drc": "passed",
        "physical": "unknown",
    }
