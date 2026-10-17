"""Small real RTL workloads, not physical/platform qualification evidence."""

import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

from chipflow.config import Project, load_project
from chipflow.verification import SimulationRequest, simulation_plan, simulation_result

EXAMPLES = Path(__file__).resolve().parents[1] / "chipflow" / "examples"


@pytest.mark.parametrize("name", ["fifo", "multiclock", "tiny_cpu"])
def test_reference_rtl_self_checks_and_waveforms(tmp_path, name):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Native Icarus binaries unavailable")
    directory = EXAMPLES / name
    project, base = load_project(directory / "chipflow.yaml")
    assert isinstance(project, Project)
    sim = project.simulation
    request = SimulationRequest(
        sim.top,
        tuple(str(base / path) for path in project.rtl),
        tuple(str(base / path) for path in sim.testbench),
    )
    plan = simulation_plan(request, tmp_path)
    for index, argv in enumerate(plan.commands):
        result = subprocess.run(
            argv,
            cwd=tmp_path,
            timeout=60,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        (tmp_path / f"command-{index}.log").write_text(result.stdout)
        assert result.returncode == 0, result.stdout
    assert simulation_result(result.stdout, result.returncode)["status"] == "passed"
    waveform = (tmp_path / "waveform.vcd").read_text()
    assert "$enddefinitions" in waveform and "#" in waveform


@pytest.mark.parametrize("name", ["fifo", "multiclock", "tiny_cpu"])
def test_reference_real_yosys_synthesis(tmp_path, name):
    if not shutil.which("yosys"):
        pytest.skip("Native Yosys unavailable")
    project, base = load_project(EXAMPLES / name / "chipflow.yaml")
    sources = " ".join('"' + str(base / source) + '"' for source in project.rtl)
    netlist = tmp_path / "synthesized.v"
    command = [
        "yosys",
        "-p",
        f'read_verilog {sources}; synth -top {name}; check -assert; write_verilog "{netlist}"',
    ]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        timeout=60,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    (tmp_path / "synthesis.log").write_text(result.stdout)
    assert result.returncode == 0, result.stdout
    assert f"module {name}" in netlist.read_text()


@pytest.mark.parametrize("missing_module", [False, True])
def test_independent_hierarchy_plugin_with_real_yosys(tmp_path, missing_module):
    if not shutil.which("yosys"):
        pytest.skip("Native Yosys unavailable")
    plugin = runpy.run_path(str(EXAMPLES.parents[1] / "examples/plugins/yosys_hierarchy.py"))
    source = tmp_path / "top.v"
    source.write_text(
        "module top; " + ("missing child();" if missing_module else "") + " endmodule"
    )
    plan = plugin["plan"]("top", [str(source)], tmp_path)
    result = subprocess.run(
        plan.commands[0],
        cwd=plan.cwd,
        timeout=60,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    (tmp_path / "hierarchy.log").write_text(result.stdout)
    assert (result.returncode != 0) == missing_module, result.stdout
