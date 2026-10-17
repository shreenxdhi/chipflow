"""Report/experiment application tests; native Icarus evidence is separate below."""

import copy
import json
import os
import threading
import time
from pathlib import Path

import pytest
import yaml

from chipflow.engine import Store
from chipflow.environment import find_tool
from chipflow.experiments import ExperimentStore
from chipflow.reports import build_report, compare_reports, pareto_frontier, render_report
from chipflow.storage import save


@pytest.fixture
def project(tmp_path):
    (tmp_path / "design.v").write_text("module dut(input clk); endmodule\n")
    (tmp_path / "tb.v").write_text(
        'module tb; initial begin $display("PASS"); $finish; end endmodule\n'
    )
    config = {
        "name": "demo",
        "top": "dut",
        "rtl": ["design.v"],
        "orfs": "/missing/orfs",
        "clocks": [{"port": "clk", "period_ns": 10}],
        "simulation": {"top": "tb", "testbench": ["tb.v"]},
    }
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


@pytest.fixture
def report_store(tmp_path):
    store = Store(tmp_path / "runs")
    run_id = "a" * 32
    directory = store.root / run_id
    directory.mkdir()
    save(
        directory / "run.json",
        {
            "id": run_id,
            "name": '<script>alert("x")</script>',
            "created": 1,
            "started": 2,
            "finished": 5,
            "target": "finish",
            "status": "completed",
            "checks": {"timing": "failed", "routing_drc": "passed"},
            "stages": [],
            "metrics": {"area": 12, "wns": -1, "bad_boolean": True},
        },
    )
    save(
        directory / "manifest.json",
        {
            "configuration": {
                "name": "demo",
                "top": "dut",
                "platform": "nangate45",
                "clocks": [{"port": "clk", "period_ns": 10}],
            },
            "input_sha256": {"inputs/rtl/0/design.v": "123", "inputs/clocks.sdc": "456"},
        },
    )
    save(directory / "toolchain.json", {"revision": "abc", "tools": {"yosys": {"version": "1"}}})
    save(directory / "metrics.json", {"logs/6_report.json": {"area": 12, "wns": -1}})
    return store, run_id


def test_report_preserves_negative_and_unknown_evidence(report_store):
    store, run_id = report_store
    report = build_report(store, run_id)
    assert report["execution_status"] == "completed"
    assert report["quality"]["status"] == "unknown"
    assert report["checks"] == {"timing": "failed", "routing_drc": "passed", "physical": "unknown"}
    assert report["duration_seconds"] == 3
    assert report["metric_provenance"]["sources"]["logs/6_report.json"]["wns"] == -1
    assert any("environment-manifest.json" in item for item in report["warnings"])


def test_report_html_and_markdown_escape_project_content(report_store):
    report = build_report(*report_store)
    report["name"] += "\n```\n<img src=x onerror=alert(1)>"
    rendered = render_report(report, "html")
    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "Content-Security-Policy" in rendered
    markdown = render_report(report, "markdown")
    assert "    " + json.dumps(report, indent=2).splitlines()[0] in markdown
    assert json.loads(render_report(report))["checks"]["physical"] == "unknown"
    with pytest.raises(ValueError):
        render_report(report, "pdf")


def test_report_rejects_oversized_or_invalid_metadata(report_store):
    store, run_id = report_store
    (store.directory(run_id) / "metrics.json").write_text("[]")
    report = build_report(store, run_id)
    assert report["metric_provenance"]["sources"] == {}
    assert any("expected JSON object" in message for message in report["warnings"])


def test_comparison_warns_changed_constraints_and_environment(report_store):
    baseline = build_report(*report_store)
    candidate = copy.deepcopy(baseline)
    candidate["run_id"] = "b" * 32
    candidate["metrics"]["area"] = 10
    candidate["reproducibility_manifest"]["input_sha256"]["inputs/clocks.sdc"] = "different"
    candidate["toolchain"]["revision"] = "changed"
    result = compare_reports(baseline, candidate)
    assert not result["compatible"]
    assert {item["field"] for item in result["warnings"]} >= {"constraints", "environment"}
    assert result["metrics"]["area"]["delta"] == -2
    assert result["metrics"]["area"]["unit"] is None
    assert "bad_boolean" not in result["metrics"]


def test_comparison_rejects_unit_mismatch_and_missing_identity(report_store):
    baseline = build_report(*report_store)
    baseline["metric_provenance"]["units"] = {"area": "um2"}
    candidate = copy.deepcopy(baseline)
    candidate["metric_provenance"]["units"]["area"] = "mm2"
    candidate["configuration"]["platform"] = None
    result = compare_reports(baseline, candidate)
    assert "area" not in result["metrics"]
    assert not result["compatible"]
    assert any(item["field"] == "platform" for item in result["warnings"])


def test_pareto_frontier_reports_only_explored_points():
    runs = [
        {"id": "a", "status": "completed", "metrics": {"area": 10, "slack": 2}},
        {"id": "b", "status": "completed", "metrics": {"area": 12, "slack": 1}},
        {"id": "c", "status": "completed", "metrics": {"area": 8, "slack": 1}},
        {"id": "d", "status": "failed", "metrics": {"area": 1, "slack": 100}},
        {"id": "e", "status": "completed", "metrics": {"area": float("nan"), "slack": 1}},
    ]
    result = pareto_frontier(runs, {"area": "min", "slack": "max"})
    assert set(result["run_ids"]) == {"a", "c"}
    assert len(result["excluded"]) == 2
    assert "no global optimum" in result["scope"]
    with pytest.raises(ValueError):
        pareto_frontier(runs, {"area": "best"})


class RecordingStore:
    """Application-only scheduler double: never evidence of EDA integration."""

    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.starts = []
        self.states = {}
        self.cancelled = []

    def start(self, path, target):
        run_id = f"{len(self.starts) + 1:032x}"
        self.starts.append((path, target, json.loads(path.read_text())))
        result = {
            "id": run_id,
            "status": "completed",
            "checks": {"timing": "passed", "routing_drc": "passed"},
            "quality": {"status": "passed"},
            "metrics": {"area": len(self.starts), "wns": 1},
        }
        self.states[run_id] = result
        return result

    def get(self, run_id):
        return self.states[run_id]

    def cancel(self, run_id):
        self.cancelled.append(run_id)
        self.states[run_id]["status"] = "cancelled"


def create_experiment(store, project, **kwargs):
    return ExperimentStore(store).create(
        project,
        name="density study",
        hypothesis="Lower density may help",
        parameters={"physical.PLACE_DENSITY": [0.3, 0.5]},
        objectives={"area": "min"},
        **kwargs,
    )


def test_experiment_freezes_inputs_and_executes_sequential_verification_first(tmp_path, project):
    store = RecordingStore(tmp_path / "runs")
    experiments = ExperimentStore(store)
    experiment = create_experiment(store, project)
    assert experiment["required_checks"] == ["timing", "routing_drc"]
    (project.parent / "design.v").write_text("BROKEN ORIGINAL")
    result = experiments.run(experiment["id"])
    assert result["status"] == "completed"
    assert [target for _, target, _ in store.starts] == ["sim", "finish", "sim", "finish"]
    assert Path(store.starts[0][2]["rtl"][0]).read_text().startswith("module dut")
    assert store.starts[0][2]["physical"]["PLACE_DENSITY"] == 0.3
    assert store.starts[2][2]["physical"]["PLACE_DENSITY"] == 0.5
    assert result["analysis"]["run_ids"] == ["2".zfill(32)]
    experiments.run(experiment["id"])
    assert len(store.starts) == 4  # No duplicated runs on a repeated controller call.
    assert experiments.list()[0]["hypothesis"] == "Lower density may help"


@pytest.mark.parametrize(
    "parameters",
    [
        {"physical.SHELL": [1]},
        {"physical.PLACE_DENSITY": [0]},
        {"physical.PLACE_DENSITY": [float("inf")]},
        {"clock.missing.period_ns": [5]},
        {"clock.clk.period_ns": [10, 10]},
        {"physical.CORE_UTILIZATION": [101]},
        {"physical.PLACE_DENSITY": list(range(65))},
        {
            "clock.clk.period_ns": list(range(1, 10)),
            "physical.CORE_UTILIZATION": list(range(1, 10)),
        },
    ],
)
def test_experiment_invalid_sweeps_create_no_drafts(tmp_path, project, parameters):
    experiments = ExperimentStore(RecordingStore(tmp_path / "runs"))
    with pytest.raises(ValueError):
        experiments.create(project, name="bad", parameters=parameters, objectives={"area": "min"})
    assert experiments.list() == []


def test_experiment_does_not_rewrite_custom_sdc(tmp_path, project):
    config = yaml.safe_load(project.read_text())
    config["sdc"] = "timing.sdc"
    (project.parent / "timing.sdc").write_text("# expert constraints")
    project.write_text(yaml.safe_dump(config))
    experiments = ExperimentStore(RecordingStore(tmp_path / "runs"))
    with pytest.raises(ValueError, match="custom SDC"):
        experiments.create(
            project, name="bad", parameters={"clock.clk.period_ns": [5]}, objectives={"area": "min"}
        )


def test_experiment_rejects_corrupt_snapshot_and_candidate_traversal(tmp_path, project):
    store = RecordingStore(tmp_path / "runs")
    experiments = ExperimentStore(store)
    experiment = create_experiment(store, project)
    directory = experiments.directory(experiment["id"])
    config_path = directory / "candidate-000.json"
    config_path.write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        experiments.run(experiment["id"])
    assert not store.starts
    experiment["candidates"][0]["configuration"] = "../../outside.json"
    save(directory / "experiment.json", experiment)
    with pytest.raises(ValueError, match="configuration reference"):
        experiments.get(experiment["id"])


def test_experiment_rejects_symlink_bundles(tmp_path, project):
    headers = tmp_path / "headers"
    headers.mkdir()
    (headers / "secret.vh").symlink_to(project.parent / "design.v")
    config = yaml.safe_load(project.read_text())
    config["include_dirs"] = [str(headers)]
    project.write_text(yaml.safe_dump(config))
    store = RecordingStore(tmp_path / "runs")
    with pytest.raises(ValueError, match="symlinks"):
        create_experiment(store, project)
    assert ExperimentStore(store).list() == []


def test_experiment_rejects_failed_and_unknown_required_evidence(tmp_path, project):
    store = RecordingStore(tmp_path / "runs")
    experiment = create_experiment(store, project, constraints={"wns": {"min": 0}})
    experiments = ExperimentStore(store)
    complete = experiments.run(experiment["id"])
    first, second = [candidate["run_id"] for candidate in complete["candidates"]]
    store.states[first]["checks"]["timing"] = "unknown"
    del store.states[second]["metrics"]["wns"]
    result = experiments.analyze(complete)
    assert result["run_ids"] == []
    assert len(result["excluded"]) == 2


def test_experiment_stop_cancels_active_without_launching_next(tmp_path, project):
    store = RecordingStore(tmp_path / "runs")
    original_start = store.start

    def start(path, target):
        result = original_start(path, target)
        result["status"] = "running"
        return result

    store.start = start
    experiments = ExperimentStore(store)
    experiment = create_experiment(store, project, target="sim")
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(experiments.run(experiment["id"], poll_seconds=0.01))
    )
    worker.start()
    deadline = time.monotonic() + 3
    while not store.starts and time.monotonic() < deadline:
        time.sleep(0.01)
    experiments.request_stop(experiment["id"])
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert result["status"] == "stopped"
    assert len(store.starts) == 1
    assert store.cancelled == [store.starts and "1".zfill(32)]


# This test launches real detached Chipflow workers and Icarus; no tool mocks.
def test_native_icarus_experiment_and_report(tmp_path, project, monkeypatch):
    iverilog, vvp = find_tool("iverilog"), find_tool("vvp")
    if not iverilog or not vvp:
        pytest.skip("native Icarus/vvp unavailable")
    monkeypatch.setenv("PATH", str(Path(iverilog).parent) + os.pathsep + os.environ["PATH"])
    store = Store(tmp_path / "native-runs")
    experiments = ExperimentStore(store)
    experiment = create_experiment(store, project, target="sim")
    result = experiments.run(experiment["id"], poll_seconds=0.05)
    assert result["status"] == "completed"
    assert len(result["candidates"]) == 2
    for candidate in result["candidates"]:
        report = build_report(store, candidate["run_id"])
        assert report["execution_status"] == "completed"
        assert report["stages"][0]["name"] == "sim"
        assert any(item["path"].endswith("console.log") for item in report["artifacts"])
        assert report["checks"]["physical"] == "unknown"
    assert result["analysis"]["run_ids"] == []  # Icarus provides no physical area metric.


@pytest.mark.parametrize(
    "field,value",
    [
        ("created", "yesterday"),
        ("target", "unknown"),
        ("status", "success"),
        ("verify_first", "false"),
    ],
)
def test_corrupt_experiment_metadata_has_controlled_error(tmp_path, project, field, value):
    store = RecordingStore(tmp_path / "runs")
    experiments = ExperimentStore(store)
    experiment = create_experiment(store, project)
    experiment[field] = value
    save(experiments.directory(experiment["id"]) / "experiment.json", experiment)
    with pytest.raises(ValueError, match="state metadata"):
        experiments.get(experiment["id"])


def test_nonstring_experiment_configuration_has_controlled_error(tmp_path, project):
    store = RecordingStore(tmp_path / "runs")
    experiments = ExperimentStore(store)
    experiment = create_experiment(store, project)
    experiment["candidates"][0]["configuration"] = 123
    save(experiments.directory(experiment["id"]) / "experiment.json", experiment)
    with pytest.raises(ValueError, match="configuration reference"):
        experiments.get(experiment["id"])


def test_stop_before_start_is_respected_until_explicit_resume(tmp_path, project):
    store = RecordingStore(tmp_path / "runs")
    experiments = ExperimentStore(store)
    experiment = create_experiment(store, project, target="sim")
    experiments.request_stop(experiment["id"])
    assert experiments.run(experiment["id"])["status"] == "stopped"
    assert store.starts == []
    assert experiments.run(experiment["id"], resume=True)["status"] == "completed"
    assert len(store.starts) == 2
