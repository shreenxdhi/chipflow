"""Real local workflow checks across CLI, persisted evidence and browser API."""

import json
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from chipflow.api import create_app
from chipflow.cli import main
from chipflow.engine import Store
from chipflow.environment import init_project


def test_standard_simulation_cli_and_evidence_api(tmp_path, monkeypatch, capsys):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Real Icarus is required for workflow acceptance")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    config = init_project(tmp_path / "counter")
    project = yaml.safe_load(config.read_text())
    project["quality"] = {"policy": "standard"}
    config.write_text(yaml.safe_dump(project))
    root = tmp_path / "runs"
    assert (
        main(["--runs", str(root), "sim", str(config), "--wait", "--json", "--require-quality"])
        == 0
    )
    run = json.loads(capsys.readouterr().out)
    assert run["status"] == "completed"
    assert run["quality"]["status"] == "passed"
    assert run["verification"]["rtl_simulation"]["status"] == "passed"
    assert run["checks"]["physical"] == "unknown"
    directory = Store(root).directory(run["id"])
    assert json.loads((directory / "environment-manifest.json").read_text())
    assert json.loads((directory / "constraints.json").read_text())["generated_simplified"]
    with TestClient(create_app(root), base_url="http://localhost") as client:
        waveform = client.get(f"/api/runs/{run['id']}/waveform")
        assert waveform.status_code == 200
        assert any(signal["events"] for signal in waveform.json()["signals"])
        report = client.get(f"/api/runs/{run['id']}/report", params={"format": "html"})
        assert report.status_code == 200 and "text/html" in report.headers["content-type"]
        assert "counter" in report.text
    # A required, unavailable physical check cannot be turned into a green sim.
    project["quality"] = {"policy": "expert", "required_checks": ["physical_verification"]}
    config.write_text(yaml.safe_dump(project))
    assert (
        main(["--runs", str(root), "sim", str(config), "--wait", "--json", "--require-quality"])
        == 2
    )
    incomplete = json.loads(capsys.readouterr().out)
    assert incomplete["status"] == "completed"
    assert incomplete["quality"]["status"] == "incomplete"


def test_guided_sdc_cli_preserves_existing_constraints(tmp_path, capsys):
    spec = tmp_path / "timing.json"
    output = tmp_path / "review.sdc"
    spec.write_text(
        json.dumps(
            {
                "time_unit": "ns",
                "capacitance_unit": "pF",
                "assumptions": ["External interface budgets require engineering review"],
                "clocks": [{"name": "clk", "port": "clk", "period": 10}],
            }
        )
    )
    assert main(["sdc", str(spec), "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["authoritative"] is False
    assert "create_clock -name clk -period 10" in output.read_text()
    original = output.read_bytes()
    assert main(["sdc", str(spec), "--output", str(output)]) == 1
    assert output.read_bytes() == original
