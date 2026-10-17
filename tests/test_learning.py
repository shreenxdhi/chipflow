import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from chipflow.api import create_app
from chipflow.cli import main
from chipflow.config import Project, load_project
from chipflow.customization import supported_points, variable_catalog
from chipflow.engine import Store, Worker, save
from chipflow.environment import init_project


@pytest.fixture
def expert(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    monkeypatch.setattr("chipflow.environment.shutil.which", lambda name, **kw: "/bin/" + name)
    flow = tmp_path / "orfs" / "flow"
    (flow / "scripts").mkdir(parents=True)
    (flow / "Makefile").write_text("SYNTH_SCRIPT = synth.tcl\n")
    (flow / "platforms" / "nangate45").mkdir(parents=True)
    (flow / "platforms" / "nangate45" / "config.mk").touch()
    (flow / "scripts" / "floorplan.tcl").write_text("source_step_tcl POST FLOORPLAN\n")
    (flow / "scripts" / "cts.tcl").write_text("source_step_tcl POST CTS\n")
    (flow / "scripts" / "variables.json").write_text(
        json.dumps(
            {
                "RECOVER_POWER": {"default": 0, "description": "Recover power after timing repair"},
                "ADDITIONAL_LEFS": {"description": "Additional LEF files"},
                "CTS_CLUSTER_SIZE": {"default": 20, "description": "Clock buffer cluster size"},
            }
        )
    )
    config = init_project(tmp_path / "project", orfs=str(flow.parent), expert=True)
    macro = tmp_path / "macro.lef"
    macro.write_text("VERSION 5.8 ;")
    project = yaml.safe_load(config.read_text())
    project["orfs_files"] = {"ADDITIONAL_LEFS": [str(macro)]}
    project["orfs_variables"]["CTS_CLUSTER_SIZE"] = 15
    config.write_text(yaml.safe_dump(project))
    return config, flow, macro


def test_expert_bundle_and_file_overrides_are_preserved_on_retry(expert, tmp_path, monkeypatch):
    config, flow, macro = expert
    monkeypatch.setattr("chipflow.engine.subprocess.Popen", lambda *a, **k: None)
    monkeypatch.setattr(
        "chipflow.provenance.capture", lambda *a, **k: {"evidence": "application-test-double"}
    )
    store = Store(tmp_path / "runs")
    run = store.start(config, "finish")
    directory = store.directory(run["id"])
    worker = Worker(directory)
    saved = worker.make_config().read_text()
    assert "POST_FLOORPLAN_TCL = " + worker.cfg.tcl.directory in saved
    assert "CTS_CLUSTER_SIZE = 15" in saved
    assert "ADDITIONAL_LEFS = " in saved
    manifest = json.loads((directory / "manifest.json").read_text())
    assert any(path.endswith("tcl/common.tcl") for path in manifest["input_sha256"])
    assert any("orfs_files/ADDITIONAL_LEFS" in path for path in manifest["input_sha256"])
    assert run["source_config"] == str(config)
    shutil.rmtree(config.parent)
    macro.unlink()
    rerun = store.retry(run["id"])
    next_worker = Worker(store.directory(rerun["id"]))
    assert (Path(next_worker.cfg.tcl.directory) / "common.tcl").is_file()
    assert Path(next_worker.cfg.orfs_files["ADDITIONAL_LEFS"][0]).read_text() == "VERSION 5.8 ;"


def test_native_catalog_and_supported_hook_discovery(expert):
    config, flow, macro = expert
    assert "Clock" in variable_catalog(flow)["CTS_CLUSTER_SIZE"]["description"]
    points = supported_points(flow)
    assert {"POST_FLOORPLAN_TCL", "POST_CTS_TCL", "SYNTH_SCRIPT"} <= points
    assert "PRE_CTS_TCL" not in points


@pytest.mark.parametrize(
    "extra",
    [
        {"orfs_variables": {"WORK_HOME": "/tmp/override"}},
        {"orfs_variables": {"CTS_CLUSTER_SIZE": "$(shell touch bad)"}},
        {"orfs_variables": {"POST_CTS_TCL": "custom.tcl"}},
        {"orfs_variables": {"PLACE_DENSITY": 0.2}, "physical": {"PLACE_DENSITY": 0.3}},
        {"tcl": {"directory": "tcl", "hooks": {"POST_CTS_TCL": "../escape.tcl"}}},
        {"tcl": {"directory": "tcl", "hooks": {"MADE_UP_TCL": "script.tcl"}}},
        {"tcl": {"directory": "tcl", "parameters": {"bad; name": "text"}}},
    ],
)
def test_customization_does_not_override_manager_paths_or_make_rules(expert, extra):
    config, flow, macro = expert
    data = yaml.safe_load(config.read_text())
    with pytest.raises(ValueError):
        Project.model_validate({**data, **extra})


def test_missing_scripts_unknown_variables_and_unimplemented_hooks_are_actionable(expert):
    from chipflow.projects import inspect_project

    config, flow, macro = expert
    data = yaml.safe_load(config.read_text())
    data["orfs_variables"]["TYPO_SETTING"] = 1
    data["tcl"]["hooks"]["PRE_CTS_TCL"] = "missing.tcl"
    config.write_text(yaml.safe_dump(data))
    result = inspect_project(config, "finish")
    assert not result["ready"]
    assert any("Script not found" in issue for issue in result["issues"])
    assert any("TYPO_SETTING" in issue for issue in result["issues"])
    assert any("does not execute PRE_CTS_TCL" in issue for issue in result["issues"])


def test_tcl_parameter_environment_is_scoped_to_the_tool_command(expert, tmp_path, monkeypatch):
    import os
    import sys

    config, flow, macro = expert
    store = Store(tmp_path / "runs")
    with monkeypatch.context() as patch:
        patch.setattr("chipflow.engine.subprocess.Popen", lambda *a, **k: None)
        run = store.start(config)
    worker = Worker(store.directory(run["id"]))
    worker.command(
        [sys.executable, "-c", "import os;print(os.environ['CHIPFLOW_PARAM_LABEL'])"], worker.d, 10
    )
    assert "counter customization" in (worker.d / "console.log").read_text()
    assert "CHIPFLOW_PARAM_LABEL" not in os.environ


def test_bundle_and_file_overrides_survive_backup_restore(expert, tmp_path, monkeypatch):
    from chipflow.backup import backup, restore

    config, flow, macro = expert
    monkeypatch.setattr("chipflow.engine.subprocess.Popen", lambda *a, **k: None)
    monkeypatch.setattr(
        "chipflow.provenance.capture", lambda *a, **k: {"evidence": "application-test-double"}
    )
    store = Store(tmp_path / "runs")
    run = store.start(config)
    run["status"] = "completed"
    save(store.directory(run["id"]) / "run.json", run)
    archive = tmp_path / "history.tar.gz"
    backup(store, archive)
    restored = Store(tmp_path / "restored")
    restore(restored, archive)
    shutil.rmtree(store.root)
    retry = restored.retry(run["id"])
    worker = Worker(restored.directory(retry["id"]))
    assert (Path(worker.cfg.tcl.directory) / "common.tcl").is_file()


def test_quickstart_runs_real_simulation_and_does_not_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    args = ["--runs", str(tmp_path / "runs"), "quickstart", str(tmp_path / "first-chip")]
    assert main(args) == 0
    text = capsys.readouterr().out
    assert "completed" in text and "counter.v is the hardware" in text
    assert (tmp_path / "first-chip" / "GUIDE.md").is_file()
    assert len(Store(tmp_path / "runs").history()) == 1
    assert main(args) == 1
    assert "already exists" in capsys.readouterr().err


def test_wizard_creates_own_project_without_overwriting(tmp_path, monkeypatch, capsys):
    config = tmp_path / "chipflow.yaml"
    (tmp_path / "dut.v").write_text("module dut; endmodule")
    (tmp_path / "tb.v").write_text("module tb; endmodule")
    answers = iter(
        ["my_design", "dut", "dut.v", "tb.v", "tb", "PASS my design", "clk", "10", "nangate45"]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert main(["wizard", str(config)]) == 0
    project, base = load_project(config)
    assert project.top == "dut" and project.rtl == ["dut.v"]
    assert project.simulation.pass_marker == "PASS my design"
    assert config.stat().st_mode & 0o777 == 0o600
    assert main(["wizard", str(config)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_shared_learning_and_reference_api(expert, tmp_path):
    config, flow, macro = expert
    client = TestClient(create_app(tmp_path / "state" / "runs"), base_url="http://127.0.0.1")
    assert len(client.get("/api/guide").json()["lessons"]) == 7
    assert "GDSII" in client.get("/api/guide").json()["glossary"]
    reference = client.get("/api/reference", params={"orfs": str(flow)}).json()
    assert reference["hooks"]["POST_CTS_TCL"]["available"]
    assert "CTS_CLUSTER_SIZE" in reference["variables"]
    starter = client.post("/api/projects/starter")
    assert starter.status_code == 201
    assert Path(starter.json()["path"]).is_file()
    assert Path(starter.json()["path"]).parent.name.startswith("first-chip-")
    assert any(p["path"] == starter.json()["path"] for p in client.get("/api/projects").json())


def test_guide_is_available_in_terminal(expert, tmp_path):
    from chipflow.tui import Dashboard

    dashboard = Dashboard(Store(tmp_path / "runs"))
    dashboard.handle("l")
    assert dashboard.modal == "guide"
    dashboard.handle("j")
    assert dashboard.guide_scroll == 1
    dashboard.handle("\n")
    assert dashboard.modal is None


def test_yaml_editor_preserves_complete_configuration_and_resolves_inputs(expert, tmp_path):
    config, flow, macro = expert
    original = yaml.safe_load(config.read_text())
    original.update(sdc="timing.sdc", include_dirs=["headers"], timeout_seconds=1234)
    original["clocks"].append({"port": "aux_clk", "period_ns": 20})
    original["physical"]["ADDER_MAP_FILE"] = "adder.v"
    original["simulation"]["cell_models"] = ["cells.v"]
    config.write_text(yaml.safe_dump(original))
    client = TestClient(create_app(tmp_path / "state" / "runs"), base_url="http://127.0.0.1")
    response = client.get("/api/projects/yaml", params={"config": str(config)})
    assert response.status_code == 200
    data = yaml.safe_load(response.json()["yaml"])
    for value in [
        data["rtl"][0],
        data["sdc"],
        data["include_dirs"][0],
        data["physical"]["ADDER_MAP_FILE"],
        data["simulation"]["cell_models"][0],
        data["tcl"]["directory"],
        data["orfs_files"]["ADDITIONAL_LEFS"][0],
    ]:
        assert Path(value).is_absolute()
    data["timeout_seconds"] = 4321
    saved = client.post("/api/projects/yaml", json={"yaml": yaml.safe_dump(data)})
    assert saved.status_code == 201
    copied, _ = load_project(Path(saved.json()["path"]))
    assert copied.model_dump() == data
    assert copied.clocks[1].port == "aux_clk"
    assert yaml.safe_load(config.read_text()) == original
    for text in ["rtl: [", "- invalid", "name: missing_fields"]:
        response = client.post("/api/projects/yaml", json={"yaml": text})
        assert response.status_code == 400
        assert "detail" in response.json()


def test_flow_directory_can_have_a_noncanonical_name(expert):
    from chipflow.projects import inspect_project

    config, flow, macro = expert
    renamed = flow.with_name("native-flow")
    flow.rename(renamed)
    data = yaml.safe_load(config.read_text())
    data["orfs"] = str(renamed)
    config.write_text(yaml.safe_dump(data))
    assert inspect_project(config, "finish")["ready"]
