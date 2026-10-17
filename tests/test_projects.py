import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from chipflow.api import create_app
from chipflow.config import Project, load_project
from chipflow.projects import create_project, inspect_project


@pytest.fixture
def local_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "chipflow.environment.shutil.which", lambda name, **kwargs: "/usr/bin/" + name
    )
    (tmp_path / "rtl.v").write_text("module dut; endmodule")
    (tmp_path / "tb.v").write_text(
        'module tb; initial begin $display("PASS"); $finish; end endmodule'
    )
    config = dict(
        name="local_test",
        top="dut",
        rtl=["rtl.v"],
        orfs="orfs",
        clocks=[dict(port="clk", period_ns=10)],
        simulation=dict(top="tb", testbench=["tb.v"]),
    )
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(config))
    return path, config


def test_simulation_preflight_and_actionable_missing_files(local_project):
    path, _ = local_project
    result = inspect_project(path, "sim")
    assert result["ready"]
    assert result["inputs"][0]["path"] == str(path.parent / "rtl.v")
    assert result["orfs"] == str(path.parent / "orfs")
    (path.parent / "tb.v").unlink()
    result = inspect_project(path, "sim")
    assert not result["ready"]
    assert any("Testbench not found:" in issue for issue in result["issues"])
    assert not inspect_project(path, "finish")["ready"]


def test_preflight_checks_platform_tools_and_gate_models(local_project, monkeypatch):
    path, _ = local_project
    assert any(
        "functional cell models" in issue for issue in inspect_project(path, "gate_sim")["issues"]
    )
    flow = path.parent / "orfs" / "flow"
    (flow / "platforms" / "nangate45").mkdir(parents=True)
    (flow / "Makefile").touch()
    (flow / "platforms" / "nangate45" / "config.mk").touch()
    assert inspect_project(path, "finish")["ready"]
    monkeypatch.setattr(
        "chipflow.environment.shutil.which",
        lambda name, **kwargs: None if name == "klayout" else "/bin/" + name,
    )
    assert any(
        "klayout is not on PATH" in issue for issue in inspect_project(path, "finish")["issues"]
    )


def test_saved_projects_are_portable_within_workspace_and_versioned(local_project):
    path, config = local_project
    folder = path.parent / "managed"
    first = create_project(folder, Project.model_validate(config))
    second = create_project(folder, Project.model_validate(config))
    assert first["path"] != second["path"]
    saved, base = load_project(Path(first["path"]))
    assert Path(saved.rtl[0]) == path.parent / "rtl.v"
    assert Path(saved.simulation.testbench[0]) == path.parent / "tb.v"
    assert Path(saved.orfs) == path.parent / "orfs"
    assert inspect_project(Path(first["path"]), "sim")["ready"]
    assert yaml.safe_load(path.read_text()) == config


def test_project_api_and_preflight_block_invalid_run(local_project):
    path, config = local_project
    app = create_app(path.parent / "state" / "runs")
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post("/api/projects", json=config)
    assert response.status_code == 201
    assert client.get("/api/projects").json()[0]["name"] == "local_test"
    assert client.post("/api/projects/inspect", json={"config": response.json()["path"]}).json()[
        "ready"
    ]
    assert client.get("/api/workspace").json()["directory"] == str(path.parent)
    (path.parent / "rtl.v").unlink()
    blocked = client.post("/api/runs", json={"config": str(path)})
    assert blocked.status_code == 400
    assert "RTL source not found" in blocked.json()["issues"][0]
    assert app.state.store.history() == []
    assert client.post("/api/projects", json={**config, "name": "../escape"}).status_code == 422
    assert (
        client.post(
            "/api/projects", json=config, headers={"Origin": "https://outside.example"}
        ).status_code
        == 403
    )


def test_retry_uses_saved_inputs_after_original_changes(local_project, monkeypatch):
    path, config = local_project
    monkeypatch.setattr("chipflow.engine.subprocess.Popen", lambda *a, **k: None)
    monkeypatch.setattr(
        "chipflow.provenance.capture", lambda *a, **k: {"evidence": "application-test-double"}
    )
    app = create_app(path.parent / "state" / "runs")
    store = app.state.store
    original = store.start(path)
    (path.parent / "rtl.v").unlink()
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post(f"/api/runs/{original['id']}/retry")
    assert response.status_code == 202
    next_id = response.json()["id"]
    assert next_id != original["id"]
    saved = json.loads((store.directory(next_id) / "project.json").read_text())
    assert Path(saved["rtl"][0]).read_text() == "module dut; endmodule"


def test_malformed_yaml_is_explained(local_project):
    path, _ = local_project
    path.write_text("rtl: [not closed")
    client = TestClient(create_app(path.parent / "runs"), base_url="http://127.0.0.1")
    response = client.post("/api/projects/inspect", json={"config": str(path)})
    assert response.status_code == 400
    assert "indentation and syntax" in response.json()["detail"]
