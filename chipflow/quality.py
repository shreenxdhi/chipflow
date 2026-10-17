"""Evidence-based quality policies, independent of execution completion."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

METRICS = {
    "setup_slack": ("finish__timing__setup__ws", "timing__setup__ws"),
    "hold_slack": ("finish__timing__hold__ws", "timing__hold__ws"),
    "setup_tns": ("finish__timing__setup__tns", "timing__setup__tns"),
    "hold_tns": ("finish__timing__hold__tns", "timing__hold__tns"),
    "router_drc": ("detailedroute__route__drc_errors", "route__drc_errors"),
    "transition_violations": ("finish__timing__drv__max_slew", "timing__drv__max_slew"),
    "capacitance_violations": ("finish__timing__drv__max_cap", "timing__drv__max_cap"),
    "unconstrained_endpoints": ("finish__timing__unconstrained", "timing__unconstrained"),
}
CHECKS = set(METRICS) | {
    "rtl_simulation",
    "synthesis",
    "artifacts",
    "physical_verification",
    "equivalence",
    "native_layout",
    "netlist_structure",
}


class QualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    policy: Literal["learning", "standard", "expert"] = "learning"
    required_checks: list[str] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)
    require_simulation: bool | None = None

    @field_validator("required_checks")
    @classmethod
    def known_checks(cls, values):
        if set(values) - CHECKS:
            raise ValueError(f"Unknown quality checks: {sorted(set(values) - CHECKS)}")
        if len(values) != len(set(values)):
            raise ValueError("Quality requirements must not contain duplicates")
        return values

    @field_validator("thresholds")
    @classmethod
    def metric_thresholds(cls, values):
        if set(values) - set(METRICS):
            raise ValueError("Thresholds must name documented numerical quality checks")
        for key, value in values.items():
            if key not in {"setup_slack", "hold_slack"} and value < 0:
                raise ValueError(f"{key} threshold must be nonnegative")
        return values


def requires_simulation(config: QualityConfig | dict) -> bool:
    config = QualityConfig.model_validate(config)
    return "rtl_simulation" in config.required_checks or (
        config.require_simulation
        if config.require_simulation is not None
        else config.policy == "standard"
    )


def metric_evidence(reports: dict, name: str) -> dict:
    """Accept only final timing or detailed-router reports; ambiguity stays unknown.

    reports maps run-relative native JSON report paths to unmodified mappings.
    No corner is silently preferred. Different reported values are ambiguous.
    """
    evidence = []
    for path, data in sorted(reports.items()):
        basename = Path(path).name
        final = basename.startswith(("6_finish", "6_report"))
        if not isinstance(data, dict) or not (
            final or name == "router_drc" and basename == "5_2_route.json"
        ):
            continue
        for key in METRICS[name]:
            if key in data:
                value = data[key]
                valid = (
                    isinstance(value, (float, int))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                )
                if name not in {"setup_slack", "hold_slack", "setup_tns", "hold_tns"}:
                    valid = valid and value >= 0
                evidence.append({"path": path, "key": key, "value": value if valid else None})
    values = {item["value"] for item in evidence}
    if not evidence:
        return {"value": None, "evidence": [], "reason": "No applicable native report metric."}
    if None in values or len(values) != 1:
        return {
            "value": None,
            "evidence": evidence,
            "reason": "Invalid or conflicting native metrics; review reports and corners.",
        }
    return {
        "value": evidence[0]["value"],
        "evidence": evidence,
        "reason": "Native report metric; scope is the reported constraints and corner.",
    }


def evaluate_quality(
    config: QualityConfig | dict, state: dict, reports: dict | None = None
) -> dict:
    """Evaluate quality without changing run status or trusting flattened metrics.

    Unknown required evidence prevents a pass. Learning reports warnings; standard
    and expert report failed/incomplete requirements. state.stages supplies actual
    completed stage records, state.verification optional explicit proof outcomes.
    """
    config = QualityConfig.model_validate(config)
    reports = reports or {}
    target = state.get("target", "sim")
    physical = target not in {"sim", "lint", "equivalence"}
    required = set(config.required_checks)
    if requires_simulation(config):
        required.add("rtl_simulation")
    if config.policy == "standard" and physical:
        required.add("synthesis")
        required.add("artifacts")
        if target == "finish":
            required.update(
                {
                    "setup_slack",
                    "hold_slack",
                    "setup_tns",
                    "hold_tns",
                    "router_drc",
                    "native_layout",
                }
            )
    checks = []
    stages = {item["name"]: item for item in state.get("stages", [])}
    for name, stage_name in (("rtl_simulation", "sim"), ("synthesis", "synth")):
        stage = stages.get(stage_name)
        status = (
            "not_run"
            if stage is None
            else {"completed": "passed", "failed": "failed"}.get(stage.get("status"), "unknown")
        )
        checks.append(
            {
                "name": name,
                "status": status,
                "required": name in required,
                "evidence": [{"stage": stage_name, "status": stage.get("status")}] if stage else [],
                "reason": "Testbench completion and configured pass marker."
                if name == "rtl_simulation"
                else "Synthesis execution and expected output validation.",
            }
        )
    for name in METRICS:
        result = metric_evidence(reports, name)
        threshold = config.thresholds.get(name, 0.0)
        value = result["value"]
        status = "unknown"
        if value is not None:
            if name in {"setup_slack", "hold_slack"}:
                passing = value >= threshold
            elif name in {"setup_tns", "hold_tns"}:
                passing = abs(value) <= threshold
            else:
                passing = value <= threshold
            status = "passed" if passing else "failed"
        checks.append(
            {
                "name": name,
                "required": name in required,
                "status": status,
                "limit": threshold,
                **result,
            }
        )
    physical_stages = [
        item
        for item in state.get("stages", [])
        if item.get("name") in {"synth", "floorplan", "place", "cts", "route", "finish"}
    ]
    sequence = ["synth", "floorplan", "place", "cts", "route", "finish"]
    expected = sequence[: sequence.index(target) + 1] if target in sequence else ["synth"]
    validated = physical and all(
        name in stages and stages[name].get("status") == "completed" and stages[name].get("outputs")
        for name in expected
    )
    checks.append(
        {
            "name": "artifacts",
            "status": "passed" if validated else "unknown",
            "required": "artifacts" in required,
            "evidence": [
                {"stage": item["name"], "outputs": item.get("outputs", [])}
                for item in physical_stages
            ],
            "reason": "Recorded expected stage outputs and integrity hashes; does not establish signoff.",
        }
    )
    for name in ("physical_verification", "equivalence", "native_layout", "netlist_structure"):
        # Full physical verification requires independently integrated, qualified
        # checks. Legacy checks.physical or a router pass never establishes this.
        verification_name = {
            "native_layout": "gds_inspection",
            "netlist_structure": "netlist_inspection",
        }.get(name, name)
        result = state.get("verification", {}).get(
            verification_name, state.get(verification_name, {})
        )
        status = result.get("status", "not_run")
        if status == "proven" and name == "equivalence":
            status = "passed"
        if status not in {
            "passed",
            "failed",
            "not_run",
            "unsupported",
            "unavailable",
            "inconclusive",
        }:
            status = "unknown"
        if status == "passed" and not result.get("evidence"):
            status = "unknown"
        checks.append(
            {
                "name": name,
                "required": name in required,
                "status": status,
                "evidence": result.get("evidence", []),
                "reason": "Requires explicit verification result and evidence; execution or GDS generation is insufficient.",
            }
        )
    needed = [item for item in checks if item["required"]]
    failed = any(item["status"] == "failed" for item in needed)
    incomplete = any(item["status"] != "passed" for item in needed)
    if failed:
        status = "failed"
    elif incomplete:
        status = "incomplete"
    elif needed:
        status = "passed"
    else:
        status = "not_required"
    warnings = [
        f"{item['name']}: {item['status']}" for item in checks if item["status"] != "passed"
    ]
    return {
        "policy": config.policy,
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "scope": "Configured requirements only; no tapeout or full-signoff certification.",
    }
