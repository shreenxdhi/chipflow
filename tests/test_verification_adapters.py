"""Plan/result tests plus native integrations; absent binaries are explicitly skipped."""

import shutil
import subprocess
from pathlib import Path

import pytest

from chipflow.verification import (
    SIMULATORS,
    SimulationRequest,
    cocotb_available,
    cocotb_plan,
    equivalence_plan,
    equivalence_result,
    junit_results,
    lint_diagnostics,
    lint_plan,
    simulation_plan,
    simulation_result,
)


def execute(plan):
    outputs = []
    returncode = 0
    for index, command in enumerate(plan.commands):
        result = subprocess.run(
            command,
            cwd=plan.cwd,
            timeout=plan.timeout_seconds,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        (plan.cwd / f"command-{index}.log").write_text(result.stdout)
        outputs.append(result.stdout)
        returncode = result.returncode
        if returncode:
            break
    return outputs, returncode


def require(*binaries):
    missing = [binary for binary in binaries if not shutil.which(binary)]
    if missing:
        pytest.skip("Native integration requires " + ", ".join(missing))


@pytest.fixture
def sim_request(tmp_path):
    rtl = tmp_path / "dut with spaces.sv"
    rtl.write_text("module dut(input a, output y); assign y = ~a; endmodule\n")
    tb = tmp_path / "tb.sv"
    tb.write_text("""module tb;
      reg a = 0;
      wire y;
      dut d(a,y);
      initial begin
        $dumpfile("waveform.vcd"); $dumpvars(0,tb);
        #1; if (y !== 1) $fatal(1,"bad output");
        a=1; #1; if (y !== 0) $fatal(1,"bad output");
        $display("PASS: real test"); $finish;
      end
    endmodule
    """)
    return SimulationRequest("tb", (str(rtl),), (str(tb),), timeout_seconds=120)


@pytest.mark.parametrize("backend", ["icarus", "verilator"])
def test_native_simulator_pass_and_vcd(sim_request, tmp_path, backend):
    require(*SIMULATORS[backend].binaries)
    plan = simulation_plan(sim_request, tmp_path, backend)
    outputs, code = execute(plan)
    assert code == 0, "\n".join(outputs)
    assert simulation_result(outputs[-1], code)["status"] == "passed"
    assert "$enddefinitions" in (tmp_path / "waveform.vcd").read_text()
    assert all((tmp_path / artifact).is_file() for artifact in plan.artifacts)


@pytest.mark.parametrize("backend", ["icarus", "verilator"])
def test_native_assertion_failure_overrides_marker(sim_request, tmp_path, backend):
    require(*SIMULATORS[backend].binaries)
    Path(sim_request.testbench[0]).write_text(
        'module tb; initial begin $display("PASS"); $fatal(1,"intentional"); end endmodule'
    )
    outputs, code = execute(simulation_plan(sim_request, tmp_path, backend))
    assert code != 0
    assert simulation_result(outputs[-1], code)["status"] == "failed"


def test_plan_validates_unsafe_names_paths_and_preserves_order(sim_request, tmp_path):
    with pytest.raises(ValueError):
        SimulationRequest("tb; exit", sim_request.sources, sim_request.testbench)
    with pytest.raises(ValueError):
        SimulationRequest("tb", ("bad\nsource.v",), sim_request.testbench)
    with pytest.raises(ValueError):
        simulation_plan(sim_request, tmp_path, "unknown")
    with pytest.raises(ValueError):
        simulation_plan(sim_request, tmp_path, prefix="../escape")
    plan = simulation_plan(sim_request, tmp_path)
    assert list(plan.commands[0][-2:]) == [*sim_request.sources, *sim_request.testbench]
    calls = []
    plan.execute(lambda *args: calls.append(args))
    assert len(calls) == 2
    assert calls[-1][1:] == (tmp_path, 120)


def test_result_requires_actual_runtime_evidence():
    assert simulation_result("", 0)["status"] == "failed"
    assert simulation_result("PASS", 1)["status"] == "failed"
    assert simulation_result("PASS", None)["status"] == "not_run"
    assert simulation_result("PASS", 0)["status"] == "passed"
    with pytest.raises(ValueError):
        simulation_result("", 0, "")


def test_lint_diagnostic_locations():
    result = lint_diagnostics(
        "%Warning-WIDTHTRUNC: /path with spaces/a.sv:8:12: Operator expects 1 bits\n"
        "%Error: Exiting due to 1 warning(s)\n"
    )
    assert result[0] == {
        "severity": "warning",
        "code": "WIDTHTRUNC",
        "file": "/path with spaces/a.sv",
        "line": 8,
        "column": 12,
        "message": "Operator expects 1 bits",
    }
    assert result[1]["severity"] == "error"
    assert result[1]["file"] is None


def test_native_lint_reports_width_error(tmp_path):
    require("verilator")
    rtl = tmp_path / "width.sv"
    rtl.write_text("module width(input [7:0] a, output y); assign y=a; endmodule")
    outputs, code = execute(lint_plan("width", [str(rtl)], tmp_path))
    assert code != 0
    findings = lint_diagnostics(outputs[0])
    assert any(finding["file"] == str(rtl) and finding["line"] == 1 for finding in findings)
    assert any("WIDTH" in (finding["code"] or "") for finding in findings)


def test_junit_preserves_failure_skips_and_empty_status(tmp_path):
    path = tmp_path / "results.xml"
    assert junit_results(path)["status"] == "not_run"
    path.write_text(
        '<testsuite><testcase name="a" time="0.01"/>'
        '<testcase name="b"><failure message="bad assertion"/></testcase>'
        '<testcase name="c"><skipped/></testcase></testsuite>'
    )
    result = junit_results(path)
    assert result["status"] == "failed"
    assert [case["status"] for case in result["tests"]] == ["passed", "failed", "skipped"]
    path.write_text('<testsuite><testcase name="c"><skipped/></testcase></testsuite>')
    assert junit_results(path)["status"] == "not_run"
    path.write_text('<testsuite errors="1"><testcase name="a"/></testsuite>')
    assert junit_results(path)["status"] == "failed"
    path.write_text("<testsuite/>")
    assert junit_results(path)["status"] == "not_run"


@pytest.mark.parametrize("xml", ["not xml", "<html/>", "<!DOCTYPE x><testsuite/>"])
def test_junit_rejects_invalid_or_entity_input(tmp_path, xml):
    path = tmp_path / "results.xml"
    path.write_text(xml)
    with pytest.raises(ValueError):
        junit_results(path)


def test_cocotb_plan_is_explicit_and_optional(sim_request, tmp_path):
    plan = cocotb_plan("dut", sim_request.sources, ["test_counter"], tmp_path)
    assert plan.backend == "cocotb-icarus"
    assert plan.commands[0][-2:] == ("cocotb", str(tmp_path / "cocotb-request.json"))
    assert isinstance(cocotb_available(), bool)
    with pytest.raises(ValueError):
        cocotb_plan("dut", sim_request.sources, ["bad;code"], tmp_path)


@pytest.mark.parametrize("matching", [True, False])
def test_native_yosys_equivalence(tmp_path, matching):
    require("yosys")
    rtl = tmp_path / "gold source.sv"
    netlist = tmp_path / "gate.v"
    rtl.write_text("`timescale 1ns/1ps\nmodule dut(input a, output y); assign y=~a; endmodule")
    netlist.write_text(
        "module dut(input a, output y); assign y=" + ("~a" if matching else "a") + "; endmodule"
    )
    outputs, code = execute(equivalence_plan("dut", [str(rtl)], [str(netlist)], tmp_path))
    result = equivalence_result(outputs[0], code, str(tmp_path / "command-0.log"))
    assert result.status == ("proven" if matching else "inconclusive"), outputs[0]
    assert result.evidence == (str(tmp_path / "command-0.log"),)


def test_equivalence_never_infers_proof_from_exit_code():
    assert equivalence_result("", 0).status == "inconclusive"
    assert equivalence_result("Equivalence successfully proven!", 1).status == "inconclusive"
    assert equivalence_result("", None).status == "not_run"
    assert equivalence_result("ERROR: syntax error", 1).status == "unsupported"


def test_native_equivalence_against_real_yosys_synthesis(tmp_path):
    require("yosys")
    rtl = tmp_path / "original.sv"
    gate = tmp_path / "synthesized.v"
    rtl.write_text(
        "module dut(input clk, input [3:0] a, output reg [3:0] y);"
        "always @(posedge clk) y <= a + 1; endmodule"
    )
    # These filenames are generated by the test, not untrusted script fragments.
    synth = subprocess.run(
        ["yosys", "-p", f'read_verilog "{rtl}"; synth -top dut; write_verilog "{gate}"'],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    (tmp_path / "synthesis.log").write_text(synth.stdout)
    assert synth.returncode == 0, synth.stdout
    assert gate.is_file()
    outputs, code = execute(equivalence_plan("dut", [str(rtl)], [str(gate)], tmp_path))
    assert equivalence_result(outputs[0], code).status == "proven", outputs[0]


def test_junit_rejects_utf16_dtd(tmp_path):
    path = tmp_path / "utf16.xml"
    path.write_bytes('<!DOCTYPE x [<!ENTITY e "expanded">]><testsuite/>'.encode("utf-16"))
    with pytest.raises(ValueError):
        junit_results(path)


@pytest.mark.parametrize(
    "message",
    [
        "FAIL: expected PASS but got 0",
        "ERROR: PASS is missing",
        "FATAL: simulation PASS",
        "  FAILED: PASS",
        "NOT PASS",
    ],
)
def test_explicit_failure_is_never_a_pass(message):
    assert simulation_result(message, 0)["status"] == "failed"


@pytest.mark.parametrize("fail", [False, True])
def test_native_cocotb_assertion_and_junit(tmp_path, fail):
    require("iverilog", "vvp")
    if not cocotb_available():
        pytest.skip("Cocotb not installed in manager Python environment")
    rtl = tmp_path / "dut.v"
    rtl.write_text("`timescale 1ns/1ps\nmodule dut(input a, output y); assign y=~a; endmodule")
    test_dir = tmp_path / "python_tests"
    test_dir.mkdir()
    (test_dir / "test_dut.py").write_text(
        "import cocotb\nfrom cocotb.triggers import Timer\n"
        "@cocotb.test()\nasync def test_inverter(dut):\n"
        "    dut.a.value = 0\n    await Timer(1, unit='ns')\n"
        f"    assert int(dut.y.value) == {0 if fail else 1}\n"
    )
    plan = cocotb_plan(
        "dut",
        [str(rtl)],
        ["test_dut"],
        tmp_path,
        test_dir=test_dir,
        seed=42,
    )
    outputs, code = execute(plan)
    report = junit_results(tmp_path / "cocotb-results.xml")
    assert report["status"] == ("failed" if fail else "passed"), outputs[0]
    assert (code != 0) == fail, outputs[0]
    assert report["tests"][0]["name"] == "test_inverter"


def test_native_yosys_equivalence_include_directory_with_spaces(tmp_path):
    require("yosys")
    include = tmp_path / "include headers"
    include.mkdir()
    (include / "settings.vh").write_text("`define INVERT ~\n")
    rtl = tmp_path / "dut.v"
    rtl.write_text(
        '`include "settings.vh"\nmodule dut(input a, output y); assign y=`INVERT a; endmodule'
    )
    outputs, code = execute(
        equivalence_plan("dut", [str(rtl)], [str(rtl)], tmp_path, include_dirs=[str(include)])
    )
    assert equivalence_result(outputs[0], code).status == "proven", outputs[0]


@pytest.mark.parametrize("marker", ["\nPASS", "PASS\0", "P" * 4097])
def test_marker_is_a_bounded_single_line(marker):
    with pytest.raises(ValueError):
        simulation_result("PASS", 0, marker)


def test_equivalence_rejects_occupied_include_scratch_paths(tmp_path):
    source = tmp_path / "a.v"
    source.write_text("module a; endmodule")
    include = tmp_path / "includes"
    include.mkdir()
    occupied = tmp_path / ".equivalence_include_0"
    occupied.write_text("preserve existing data")
    with pytest.raises(ValueError, match="occupied"):
        equivalence_plan("a", [str(source)], [str(source)], tmp_path, include_dirs=[str(include)])
    assert occupied.read_text() == "preserve existing data"
