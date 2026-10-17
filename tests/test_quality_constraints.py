import json

import pytest
from pydantic import ValidationError

from chipflow.constraints import analyze_constraints, inspect_constraints
from chipflow.quality import QualityConfig, evaluate_quality, metric_evidence, requires_simulation


def native_reports(setup=1.0, hold=0.1, drc=0):
    return {
        "logs/p/d/base/6_report.json": {
            "finish__timing__setup__ws": setup,
            "finish__timing__hold__ws": hold,
            "finish__timing__setup__tns": 0,
            "finish__timing__hold__tns": 0,
        },
        "logs/p/d/base/5_2_route.json": {"detailedroute__route__drc_errors": drc},
    }


def completed_finish():
    return {
        "target": "finish",
        "status": "completed",
        "verification": {
            "gds_inspection": {"status": "passed", "evidence": ["gds-inspection.json"]}
        },
        "stages": [
            {"name": name, "status": "completed", "outputs": [{"path": "output", "sha256": "abc"}]}
            for name in ("sim", "synth", "floorplan", "place", "cts", "route", "finish")
        ],
    }


def by_name(result, name):
    return next(item for item in result["checks"] if item["name"] == name)


def test_legacy_learning_does_not_certify_quality_or_change_execution():
    state = {"status": "completed", "target": "finish", "stages": []}
    result = evaluate_quality({}, state)
    assert result["status"] == "not_required"
    assert state["status"] == "completed"
    assert by_name(result, "physical_verification")["status"] == "not_run"
    assert by_name(result, "setup_slack")["status"] == "unknown"
    assert not requires_simulation({})


def test_standard_requires_actual_simulation_artifacts_and_final_checks():
    state = completed_finish()
    result = evaluate_quality({"policy": "standard"}, state, native_reports())
    assert result["status"] == "passed"
    assert by_name(result, "physical_verification")["status"] == "not_run"
    assert requires_simulation({"policy": "standard"})
    state["stages"] = state["stages"][1:]
    assert (
        evaluate_quality({"policy": "standard"}, state, native_reports())["status"] == "incomplete"
    )


@pytest.mark.parametrize("name,value", [("setup", -0.1), ("hold", -0.01), ("drc", 1)])
def test_real_native_violation_fails_quality_without_relabeling_execution(name, value):
    state = completed_finish()
    result = evaluate_quality({"policy": "standard"}, state, native_reports(**{name: value}))
    assert result["status"] == "failed"
    assert state["status"] == "completed"


@pytest.mark.parametrize("value", [True, None, float("inf"), float("nan"), "1"])
def test_invalid_metric_never_passes(value):
    result = evaluate_quality(
        {"policy": "standard"}, completed_finish(), native_reports(setup=value)
    )
    assert by_name(result, "setup_slack")["status"] == "unknown"
    assert result["status"] == "incomplete"
    json.dumps(result, allow_nan=False)


def test_conflicting_corners_or_aliases_cannot_select_optimistic_metric():
    reports = native_reports()
    reports["logs/other_corner/6_report.json"] = {"finish__timing__setup__ws": -1}
    evidence = metric_evidence(reports, "setup_slack")
    assert evidence["value"] is None
    assert len(evidence["evidence"]) == 2
    reports = native_reports()
    reports["logs/p/d/base/6_report.json"]["timing__setup__ws"] = -1
    assert metric_evidence(reports, "setup_slack")["value"] is None


def test_early_reports_and_flattened_metrics_do_not_prove_final_timing():
    state = completed_finish()
    state["metrics"] = {"finish__timing__setup__ws": 100}
    result = evaluate_quality(
        {"policy": "standard"}, state, {"3_place.json": {"finish__timing__setup__ws": 100}}
    )
    assert result["status"] == "incomplete"


def test_expert_thresholds_and_explicit_requirements():
    result = evaluate_quality(
        {"policy": "expert", "required_checks": ["setup_slack"], "thresholds": {"setup_slack": 2}},
        completed_finish(),
        native_reports(),
    )
    assert result["status"] == "failed"
    assert by_name(result, "setup_slack")["limit"] == 2
    assert requires_simulation({"policy": "expert", "required_checks": ["rtl_simulation"]})


@pytest.mark.parametrize(
    "config",
    [
        {"required_checks": ["invented"]},
        {"required_checks": ["synthesis", "synthesis"]},
        {"thresholds": {"made_up": 1}},
        {"thresholds": {"router_drc": -1}},
        {"thresholds": {"setup_slack": float("nan")}},
        {"policy": "signoff"},
    ],
)
def test_bad_policy_rejected(config):
    with pytest.raises(ValidationError):
        QualityConfig.model_validate(config)


def test_proof_requires_explicit_evidence_and_router_is_not_signoff():
    state = completed_finish()
    state["checks"] = {"physical": "passed"}
    state["verification"] = {"equivalence": {"status": "proven"}}
    config = {"policy": "expert", "required_checks": ["equivalence", "physical_verification"]}
    assert evaluate_quality(config, state, native_reports())["status"] == "incomplete"
    state["verification"]["equivalence"]["evidence"] = ["verification/equivalence.log"]
    assert by_name(evaluate_quality(config, state), "equivalence")["status"] == "passed"
    assert by_name(evaluate_quality(config, state), "physical_verification")["status"] == "not_run"


def test_standard_sim_only_does_not_require_physical_checks():
    result = evaluate_quality(
        {"policy": "standard"},
        {"target": "sim", "stages": [{"name": "sim", "status": "completed"}]},
    )
    assert result["status"] == "passed"
    assert not by_name(result, "setup_slack")["required"]


def test_constraint_inventory_multiple_clocks_exceptions_and_io(tmp_path):
    text = """# create_clock -name fake
create_clock -name clk -period 10 [get_ports clk]
create_generated_clock -name divided -source [get_ports clk] -divide_by 2 [get_pins div/Q]
set_input_delay -clock clk 1 [get_ports data]
set_output_delay -clock clk 2 [get_ports result]
set_clock_groups -asynchronous -group [get_clocks clk] -group [get_clocks aux]
set_false_path -from [get_ports reset]
set_multicycle_path 2 -setup -from [get_clocks clk]
set_clock_uncertainty 0.1 [get_clocks clk]
set_clock_latency 1 [get_clocks clk]
set_input_transition 0.1 [get_ports data]
set_load 0.2 [get_ports result]
set_min_delay 0.1 -from [get_ports data]
set_max_delay 3 -to [get_ports result]
"""
    path = tmp_path / "design.sdc"
    path.write_text(text)
    result = inspect_constraints(path)
    assert len(result["commands"]["primary_clocks"]) == 1
    assert len(result["commands"]["generated_clocks"]) == 1
    assert result["commands"]["primary_clocks"][0]["line"] == 2
    assert result["coverage"]["unconstrained_endpoints"] is None
    assert not result["authoritative"]
    assert not any("No top-level" in warning for warning in result["warnings"])
    assert any("review set_false_path" in warning for warning in result["warnings"])


def test_dynamic_sdc_never_executes_and_does_not_claim_nested_clocks(tmp_path):
    marker = tmp_path / "must_not_exist"
    result = analyze_constraints(f"""exec touch {marker}
source other.sdc
foreach port {{clk aux}} {{
 create_clock -period 10 [get_ports $port]
}}
""")
    assert not marker.exists()
    assert not result["commands"]["primary_clocks"]
    assert any("requires native Tcl evaluation" in warning for warning in result["warnings"])
    assert any("outside this advisory inventory" in warning for warning in result["warnings"])


def test_generated_clock_only_constraints_explain_limitations():
    result = analyze_constraints("create_clock -period 10 [get_ports clk]", generated=True)
    assert result["generated_simplified"]
    assert any("Simplified" in warning for warning in result["warnings"])
    assert any("input delays" in warning for warning in result["warnings"])
    assert result["coverage"]["setup_analysis"] == "not_run"


def test_lexical_inventory_multiline_semicolons_and_unbalanced_tcl():
    result = analyze_constraints(
        "create_clock -name {semi;colon} \\\n -period 10 [get_ports {clk}]; set_input_delay 1 [get_ports {a b}]\n"
    )
    assert len(result["commands"]["primary_clocks"]) == 1
    assert len(result["commands"]["input_delays"]) == 1
    assert not any("Unbalanced" in item for item in result["warnings"])
    malformed = analyze_constraints("create_clock [get_ports {clk}")
    assert any("Unbalanced" in item for item in malformed["warnings"])


def test_incomplete_stage_chain_cannot_pass_artifact_requirement():
    state = completed_finish()
    state["stages"] = [item for item in state["stages"] if item["name"] != "place"]
    result = evaluate_quality({"policy": "standard"}, state, native_reports())
    assert result["status"] == "incomplete"
    assert by_name(result, "artifacts")["status"] == "unknown"


@pytest.mark.parametrize("tns", [-0.01, 0.01])
def test_tns_requires_zero_default_and_explicit_absolute_tolerance(tns):
    reports = native_reports()
    reports["logs/p/d/base/6_report.json"]["finish__timing__setup__tns"] = tns
    config = {"policy": "expert", "required_checks": ["setup_tns"]}
    assert evaluate_quality(config, completed_finish(), reports)["status"] == "failed"
    config["thresholds"] = {"setup_tns": 0.02}
    assert evaluate_quality(config, completed_finish(), reports)["status"] == "passed"


def test_explicit_requirement_cannot_be_disabled_by_simulation_flag():
    config = {"required_checks": ["rtl_simulation"], "require_simulation": False}
    assert requires_simulation(config)


def test_native_layout_and_netlist_structure_require_native_evidence():
    config = {"policy": "expert", "required_checks": ["native_layout", "netlist_structure"]}
    state = completed_finish()
    state["verification"] = {
        "gds_inspection": {"status": "passed", "evidence": ["gds-inspection.json"]},
        "netlist_inspection": {"status": "unsupported", "reason": "missing cell models"},
    }
    result = evaluate_quality(config, state)
    assert by_name(result, "native_layout")["status"] == "passed"
    assert by_name(result, "netlist_structure")["status"] == "unsupported"
    assert result["status"] == "incomplete"
    state["verification"]["netlist_inspection"] = {"status": "passed", "evidence": ["netlist.json"]}
    assert evaluate_quality(config, state)["status"] == "passed"
    del state["verification"]["gds_inspection"]["evidence"]
    assert evaluate_quality(config, state)["status"] == "incomplete"


def guided_config():
    return {
        "time_unit": "ns",
        "capacitance_unit": "pF",
        "assumptions": ["Interface timing reviewed for this test; labels must match Liberty units"],
        "clocks": [
            {"name": "main", "port": "clk", "period": 10, "uncertainty": 0.1, "latency": 0.2},
            {"name": "aux", "port": "aux_clk", "period": 20},
        ],
        "io_delays": [
            {
                "direction": "input",
                "ports": ["data"],
                "clock": "main",
                "minimum": -0.1,
                "maximum": 1,
            },
            {
                "direction": "output",
                "ports": ["result"],
                "clock": "main",
                "minimum": 0,
                "maximum": 2,
            },
        ],
        "input_transitions": {"data": 0.2},
        "output_loads": {"result": 0.1},
    }


def test_guided_sdc_explicit_multiclock_io_and_safe_exclusive_write(tmp_path):
    from chipflow.constraints import build_guided_sdc, write_guided_sdc

    data = guided_config()
    text = build_guided_sdc(data)
    report = analyze_constraints(text)
    assert len(report["commands"]["primary_clocks"]) == 2
    assert len(report["commands"]["input_delays"]) == 2
    assert len(report["commands"]["output_delays"]) == 2
    assert report["commands"]["clock_uncertainty"]
    assert report["commands"]["clock_latency"]
    assert report["commands"]["input_transition"]
    assert report["commands"]["output_load"]
    assert "Assumption:" in text
    assert "native timing engine" in text
    path = tmp_path / "explicit.sdc"
    result = write_guided_sdc(data, path)
    assert result["user_assumptions_required"]
    assert path.read_text() == text
    with pytest.raises(FileExistsError):
        write_guided_sdc(data, path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(assumptions=[]),
        lambda c: c["clocks"][0].update(port="clk]; exec bad"),
        lambda c: c["clocks"][0].update(period=float("inf")),
        lambda c: c["clocks"][1].update(name="main"),
        lambda c: c["io_delays"][0].update(clock="undefined"),
        lambda c: c["io_delays"][0].update(minimum=10),
        lambda c: c["io_delays"].append(c["io_delays"][0].copy()),
        lambda c: c.update(output_loads={"result": -1}),
    ],
)
def test_guided_sdc_rejects_unsafe_or_conflicting_assumptions(mutate):
    from chipflow.constraints import build_guided_sdc

    data = guided_config()
    mutate(data)
    with pytest.raises(ValidationError):
        build_guided_sdc(data)


def test_guided_comment_values_cannot_inject_executable_tcl():
    from chipflow.constraints import build_guided_sdc

    data = guided_config()
    data["assumptions"] = ["first\nexec bad"]
    data["time_unit"] = "ns\nexec bad"
    text = build_guided_sdc(data)
    assert "\nexec bad" not in text
    assert "# Assumption: exec bad" in text
