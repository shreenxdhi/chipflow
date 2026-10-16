import concurrent.futures
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from chipflow.api import create_app
from chipflow.backup import backup, restore
from chipflow.engine import Store, Worker
from chipflow.storage import process_alive, process_identity, save


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for process state")


def stopped(pid):
    identity = process_identity(pid)
    return identity is None or identity["state"] == "Z"


@pytest.fixture
def completed(tmp_path, monkeypatch):
    (tmp_path / "dut.v").write_text("module dut; endmodule")
    (tmp_path / "tb.v").write_text(
        'module tb; initial begin $display("PASS"); $finish; end endmodule'
    )
    include = tmp_path / "includes"
    include.mkdir()
    (include / ".defs.vh").write_text("`define EXAMPLE 1")
    path = tmp_path / "project.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                name="test",
                top="dut",
                rtl=["dut.v"],
                include_dirs=[str(include)],
                orfs="/missing/orfs",
                clocks=[dict(port="clk", period_ns=10)],
                simulation=dict(top="tb", testbench=["tb.v"]),
            )
        )
    )
    store = Store(tmp_path / "runs")
    with monkeypatch.context() as patch:
        patch.setattr("chipflow.engine.subprocess.Popen", lambda *a, **k: None)
        state = store.start(path)
    state["status"] = "completed"
    save(store.directory(state["id"]) / "run.json", state)
    return store, state


def test_atomic_metadata_concurrent_writers_and_private_files(tmp_path):
    path = tmp_path / "state.json"

    def writer(number):
        for _ in range(10):
            save(path, {"writer": number, "data": "x" * 4096})
            assert json.loads(path.read_text())["data"] == "x" * 4096

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer, range(4)))
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".state.json-*"))
    with pytest.raises(ValueError):
        save(path, {"bad": float("inf")})
    assert "writer" in json.loads(path.read_text())


def test_pid_reuse_and_interruption_are_recorded_durably(completed):
    store, state = completed
    identity = process_identity(os.getpid())
    assert process_alive(identity)
    identity["started_ticks"] = "0"
    assert not process_alive(identity)
    state.update(
        status="running",
        pid=os.getpid(),
        worker_identity=identity,
        stages=[dict(name="sim", status="running")],
    )
    path = store.directory(state["id"]) / "run.json"
    save(path, state)
    assert store.get(state["id"])["status"] == "interrupted"
    persisted = json.loads(path.read_text())
    assert persisted["status"] == "interrupted"
    assert persisted["stages"][0]["finished"] == persisted["finished"]


def test_prior_history_schema_is_read_and_future_schema_is_rejected(completed):
    store, state = completed
    path = store.directory(state["id"]) / "run.json"
    state.pop("schema_version")
    save(path, state)
    assert store.get(state["id"])["status"] == "completed"
    save(path, {**state, "schema_version": 999})
    with pytest.raises(ValueError, match="upgrade Chipflow"):
        store.get(state["id"])


def test_saved_input_tampering_blocks_retry_and_backup(completed, tmp_path):
    store, state = completed
    directory = store.directory(state["id"])
    project = json.loads((directory / "project.json").read_text())
    Path(project["rtl"][0]).write_text("changed")
    with pytest.raises(ValueError, match="integrity"):
        store.retry(state["id"])
    destination = tmp_path / "history.tar.gz"
    with pytest.raises(ValueError, match="integrity"):
        backup(store, destination)
    assert not destination.exists()


def test_backup_restore_relocates_inputs_and_can_run_after_restore(completed, tmp_path):
    store, state = completed
    destination = tmp_path / "history.tar.gz"
    result = backup(store, destination)
    assert result["runs"] == 1
    assert destination.stat().st_mode & 0o777 == 0o600
    restored = Store(tmp_path / "relocated")
    assert restore(restored, destination)["runs"] == 1
    original = store.directory(state["id"])
    # The restored run must be independent of the original absolute paths.
    import shutil

    shutil.rmtree(original)
    run = restored.retry(state["id"])
    final = wait_for(
        lambda: (
            value
            if (value := restored.get(run["id"]))["status"]
            in {"completed", "failed", "interrupted"}
            else None
        )
    )
    assert final["status"] == "completed", final
    with pytest.raises(ValueError, match="empty"):
        restore(restored, destination)
    with pytest.raises(ValueError, match="already exists"):
        backup(store, destination)


def test_backup_refuses_active_runs(completed, tmp_path):
    store, state = completed
    state.update(status="running", pid=os.getpid(), worker_identity=process_identity(os.getpid()))
    save(store.directory(state["id"]) / "run.json", state)
    with pytest.raises(ValueError, match="active"):
        backup(store, tmp_path / "active.tar.gz")


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../../escape", "file"),
        ("a" * 32 + "/escape", "symlink"),
        ("a" * 32 + "/escape", "hardlink"),
    ],
)
def test_restore_rejects_path_traversal_and_links(tmp_path, name, kind):
    path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        metadata = json.dumps(
            {"version": 1, "source_root": "/previous", "files": {name: "x"}}
        ).encode()
        item = tarfile.TarInfo("backup.json")
        item.size = len(metadata)
        archive.addfile(item, io.BytesIO(metadata))
        item = tarfile.TarInfo(name)
        if kind != "file":
            item.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            item.linkname = "/tmp/escape"
        archive.addfile(item)
    store = Store(tmp_path / "restored")
    with pytest.raises(ValueError, match="Unsafe"):
        restore(store, path)
    assert list(store.root.iterdir()) == [store.root / ".store.lock"]


def test_restore_detects_corruption_before_changing_history(completed, tmp_path):
    store, state = completed
    path = tmp_path / "history.tar.gz"
    backup(store, path)
    corrupted = tmp_path / "corrupt.tar.gz"
    with tarfile.open(path) as source, tarfile.open(corrupted, "w:gz") as target:
        for item in source.getmembers():
            data = source.extractfile(item).read()
            if item.name.endswith("dut.v"):
                data = b"corrupt"
                item.size = len(data)
            target.addfile(item, io.BytesIO(data))
    restored = Store(tmp_path / "restored")
    with pytest.raises(ValueError, match="checksum"):
        restore(restored, corrupted)
    assert not restored.history()


def test_required_outputs_cannot_be_empty_or_invalid_gds(completed):
    store, state = completed
    worker = Worker(store.directory(state["id"]))
    worker.state["stages"] = [{"name": "finish"}]
    with pytest.raises(RuntimeError, match="missing or empty"):
        worker.validate_outputs("finish")
    root = worker.d / "results" / worker.cfg.platform / worker.cfg.top / "base"
    root.mkdir(parents=True)
    (root / "6_final.odb").write_bytes(b"database")
    (root / "6_final.gds").write_bytes(b"not a layout")
    with pytest.raises(RuntimeError, match="GDSII"):
        worker.validate_outputs("finish")
    (root / "6_final.gds").write_bytes(b"\x00\x06\x00\x02\x00\x05")
    worker.validate_outputs("finish")
    assert len(worker.state["stages"][-1]["outputs"]) == 2


def test_guardian_stops_tools_after_worker_sigkill_and_holds_physical_lock(completed, tmp_path):
    store, state = completed
    directory = store.directory(state["id"])
    # Run the real command supervisor in another process, with an inherited flow lock.
    tool = "import subprocess,time; p=subprocess.Popen(['sleep','30']);open('grandchild.pid','w').write(str(p.pid));time.sleep(30)"
    script = """
import fcntl,sys
from pathlib import Path
from chipflow.engine import Worker
w=Worker(Path(sys.argv[1]))
w.flow_lock=(w.d.parent/'.physical-flow.lock').open('a')
fcntl.flock(w.flow_lock,fcntl.LOCK_EX)
w.command([sys.executable,'-c',sys.argv[2]],w.d,60)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(directory), tool])
    pid = None
    try:
        wait_for(lambda: (directory / "grandchild.pid").exists())
        pid = int((directory / "grandchild.pid").read_text())
        process.kill()
        process.wait(timeout=5)
        wait_for(lambda: stopped(pid))
        import fcntl

        with (store.root / ".physical-flow.lock").open("a") as lock:

            def released():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except BlockingIOError:
                    return False

            wait_for(released)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if pid and not stopped(pid):
            os.kill(pid, signal.SIGKILL)


def test_web_request_bounds_security_headers_and_log_symlinks(completed, tmp_path):
    store, state = completed
    client = TestClient(create_app(store.root), base_url="http://127.0.0.1")
    health = client.get("/api/health")
    assert health.json()["status"] == "ok"
    assert health.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in health.headers["content-security-policy"]
    oversized = client.post("/api/runs", content=b"x" * (1024 * 1024 + 1))
    assert oversized.status_code == 413
    assert client.post("/api/runs", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    secret = tmp_path / "secret"
    secret.write_text("private")
    (store.directory(state["id"]) / "console.log").symlink_to(secret)
    assert client.get(f"/api/runs/{state['id']}/logs").status_code == 404


def test_failed_metadata_replace_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save(path, {"status": "running"})

    def disk_full(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("chipflow.storage.os.replace", disk_full)
    with pytest.raises(OSError):
        save(path, {"status": "completed"})
    assert json.loads(path.read_text())["status"] == "running"
    assert not list(tmp_path.glob(".state.json-*"))


def test_saved_configuration_tampering_blocks_execution(completed):
    store, state = completed
    path = store.directory(state["id"]) / "project.json"
    project = json.loads(path.read_text())
    project["top"] = "different"
    save(path, project)
    with pytest.raises(ValueError, match="configuration failed integrity"):
        store.retry(state["id"])


def test_utf8_log_boundaries_truncation_and_incomplete_tail(tmp_path):
    from chipflow.storage import read_log

    path = tmp_path / "console.log"
    path.write_text("a" * 7 + "界" + "hello")
    first = read_log(path, limit=8)
    assert first["text"] == "a" * 7 and first["offset"] == 7
    second = read_log(path, first["offset"])
    assert second["text"] == "界hello"
    path.write_bytes(b"new\xe7")
    truncated = read_log(path, second["offset"])
    assert truncated["text"] == "new" and truncated["offset"] == 3
    assert read_log(path, 3)["offset"] == 3
    assert read_log(path, 3, final=True)["text"] == "�"


def test_adder_mapping_is_snapshotted(completed, tmp_path, monkeypatch):
    store, state = completed
    previous = store.directory(state["id"]) / "project.json"
    project = json.loads(previous.read_text())
    mapping = tmp_path / "maps" / "adder.v"
    mapping.parent.mkdir()
    mapping.write_text("module adder; endmodule")
    project["physical"]["ADDER_MAP_FILE"] = str(mapping)
    config = tmp_path / "mapped.json"
    save(config, project)
    monkeypatch.setattr("chipflow.engine.subprocess.Popen", lambda *a, **k: None)
    result = store.start(config)
    worker = Worker(store.directory(result["id"]))
    assert Path(worker.cfg.physical["ADDER_MAP_FILE"]).read_text() == mapping.read_text()
    mapping.unlink()
    assert "adder_map" in worker.cfg.physical["ADDER_MAP_FILE"]


def test_terminal_setup_suggestions_are_accessible(completed):
    from chipflow.tui import Dashboard

    store, state = completed
    dashboard = Dashboard(store)
    dashboard.handle("s")
    assert dashboard.modal == "setup"
    assert "checks" in dashboard.setup_report
    dashboard.handle("j")
    assert dashboard.setup_scroll == 1
    dashboard.handle("\n")
    assert dashboard.modal is None


def test_wait_drains_completed_logs_larger_than_one_chunk(completed, capsys):
    from chipflow.cli import wait_for_run

    store, state = completed
    text = "first marker\n" + "output\n" * 50000 + "last marker\n"
    (store.directory(state["id"]) / "console.log").write_text(text)
    assert wait_for_run(store, state["id"]) == 0
    assert capsys.readouterr().out.startswith(text)


def test_invalid_backup_has_an_actionable_error(tmp_path):
    path = tmp_path / "not-a-backup.tar.gz"
    path.write_text("invalid archive")
    with pytest.raises(ValueError, match="Invalid backup archive"):
        restore(Store(tmp_path / "runs"), path)


def test_health_reports_unavailable_history(tmp_path):
    store_path = tmp_path / "runs"
    client = TestClient(create_app(store_path), base_url="http://127.0.0.1")
    store_path.rmdir()
    assert client.get("/api/health").status_code == 503
