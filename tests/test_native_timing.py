import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from chipflow.environment import discover_orfs, find_tool, flow_path
from chipflow.timing import STAConfig, sta_plan, sta_result


@pytest.fixture
def native_fixture(tmp_path):
    executable = find_tool("openroad")
    platform = flow_path(Path(discover_orfs())) / "platforms" / "nangate45"
    liberty = platform / "lib" / "NangateOpenCellLibrary_typical.lib"
    lefs = [
        platform / "lef" / name
        for name in ("NangateOpenCellLibrary.tech.lef", "NangateOpenCellLibrary.macro.mod.lef")
    ]
    if not executable or not liberty.is_file() or not all(path.is_file() for path in lefs):
        pytest.skip("Real native OpenROAD and matching Nangate45 technology views unavailable")
    netlist = tmp_path / "dut.v"
    netlist.write_text(
        "module dut(input clk, input d, output q); DFF_X1 ff(.CK(clk),.D(d),.Q(q),.QN()); endmodule\n"
    )
    sdc = tmp_path / "dut.sdc"
    sdc.write_text("""create_clock -name main -period 10 [get_ports clk]
set_input_delay -clock main -min 0.2 [get_ports d]
set_input_delay -clock main -max 1 [get_ports d]
set_output_delay -clock main -min 0 [get_ports q]
set_output_delay -clock main -max 1 [get_ports q]
set_input_transition 0.1 [get_ports d]
set_load 0.01 [get_ports q]
""")
    config = dict(
        top="dut",
        netlist=str(netlist),
        liberty=[str(liberty)],
        lef=[str(path) for path in lefs],
        sdc=str(sdc),
        corner_name="Nangate45 typical cell-delay-only test",
        power=True,
    )
    return executable, config


def execute_native(config, executable, directory):
    plan = sta_plan(config, directory, executable=executable)
    process = subprocess.run(
        plan.commands[0], cwd=plan.cwd, capture_output=True, text=True, timeout=30
    )
    return plan, process, sta_result(directory, process.returncode)


def test_native_sta_and_default_activity_power_have_traceable_evidence(native_fixture, tmp_path):
    executable, config = native_fixture
    plan, process, result = execute_native(config, executable, tmp_path / "analysis")
    assert result["status"] == "passed", process.stdout + process.stderr + str(result)
    assert result["constraint_status"] == "passed"
    assert result["metrics"]["setup_slack"]["value"] > 0
    assert result["metrics"]["hold_slack"]["value"] > 0
    assert result["metrics"]["setup_tns"]["value"] == 0
    assert result["clock_count"] == 1
    assert result["endpoint_coverage"] is None
    assert result["units"]["time"] == "1ns"
    assert result["power"]["total_w"] > 0
    assert result["power"]["measured"] is False
    assert result["power"]["activity"] == "tool_default_activity_estimate"
    assert result["manifest"]["parasitics"] == "none_cell_delay_only"
    assert (plan.cwd / "sta-setup.rpt").read_text().find("Startpoint:") >= 0
    assert result["evidence"]
    json.dumps(result, allow_nan=False)


def test_native_impossible_clock_fails_timing_without_hiding_execution(native_fixture, tmp_path):
    executable, config = native_fixture
    source = Path(config["sdc"])
    source.write_text(source.read_text().replace("-period 10", "-period 0.01"))
    _, process, result = execute_native(config, executable, tmp_path / "analysis")
    assert process.returncode == 0
    assert result["execution_status"] == "completed"
    assert result["timing_status"] == "failed"
    assert result["status"] == "failed"
    assert result["metrics"]["setup_slack"]["value"] < 0
    assert result["metrics"]["setup_tns"]["value"] < 0


def test_native_clock_only_constraints_remain_incomplete(native_fixture, tmp_path):
    executable, config = native_fixture
    Path(config["sdc"]).write_text("create_clock -name main -period 10 [get_ports clk]\n")
    _, process, result = execute_native(config, executable, tmp_path / "analysis")
    assert process.returncode == 0
    assert result["constraint_status"] == "warnings"
    assert result["status"] == "incomplete"
    assert result["endpoint_coverage"] is None
    report = (tmp_path / "analysis" / "sta-constraints.rpt").read_text()
    assert "set_input_delay" in report and "set_output_delay" in report
    assert "unconstrained endpoints" in report


def test_native_invalid_sdc_and_changed_snapshot_cannot_pass(native_fixture, tmp_path):
    executable, config = native_fixture
    plan, _, result = execute_native(config, executable, tmp_path / "good")
    assert result["status"] == "passed"
    snapshot = next((plan.cwd / "sta-inputs" / "netlist").rglob("*.v"))
    snapshot.write_text("changed after execution")
    assert sta_result(plan.cwd, 0)["status"] == "unknown"
    Path(config["sdc"]).write_text("chipflow_nonexistent_native_constraint\n")
    _, process, result = execute_native(config, executable, tmp_path / "bad")
    assert process.returncode != 0
    assert result["status"] == "failed"
    assert result["timing_status"] == "unknown"


def test_timing_plan_requires_explicit_views_and_safe_top():
    data = dict(
        top="dut",
        netlist="a.v",
        liberty=["lib.lib"],
        lef=["tech.lef"],
        sdc="a.sdc",
        corner_name="typical",
    )
    for override in (
        {"top": "dut;exec bad"},
        {"lef": []},
        {"liberty": []},
        {"corner_name": " "},
        {"activity": "a.vcd"},
        {"netlist": "bad\nfile"},
    ):
        with pytest.raises(ValidationError):
            STAConfig.model_validate({**data, **override})
    assert sta_result(Path("."), None)["status"] == "not_run"


def test_actual_sta_cli_supervised_success_and_quality_failure(native_fixture, tmp_path):
    import sys

    executable, config = native_fixture
    spec = tmp_path / "timing.json"
    spec.write_text(json.dumps(config))
    output = tmp_path / "cli-good"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "chipflow",
            "sta",
            str(spec),
            "--output",
            str(output),
            "--executable",
            executable,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((output / "timing-result.json").read_text())
    assert result["execution_status"] == "completed"
    assert result["timing_status"] == "passed"
    assert (output / "console.log").stat().st_size > 0
    source = Path(config["sdc"])
    source.write_text(source.read_text().replace("-period 10", "-period 0.01"))
    output = tmp_path / "cli-impossible"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "chipflow",
            "sta",
            str(spec),
            "--output",
            str(output),
            "--executable",
            executable,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 2, process.stdout + process.stderr
    result = json.loads((output / "timing-result.json").read_text())
    assert result["execution_status"] == "completed"
    assert result["timing_status"] == "failed"


def test_rounded_negative_zero_cannot_become_a_timing_pass(native_fixture, tmp_path):
    executable, config = native_fixture
    plan, _, result = execute_native(config, executable, tmp_path / "analysis")
    assert result["status"] == "passed"
    # Application parser negative fixture: do not pretend this replaces tool validation.
    (plan.cwd / "sta-setup-slack.rpt").write_text("worst slack max -0.000000000\n")
    result = sta_result(plan.cwd, 0)
    assert result["timing_status"] == "unknown"
    assert result["status"] == "incomplete"
