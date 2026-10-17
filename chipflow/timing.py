"""Native OpenROAD/OpenSTA report plans with explicit inputs and scoped results."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .physical_verification import _tcl
from .verification import CommandPlan


class STAConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    top: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    netlist: str
    liberty: list[str] = Field(min_length=1)
    lef: list[str] = Field(min_length=1)
    sdc: str
    corner_name: str = Field(min_length=1, max_length=100)
    spef: str | None = None
    power: bool = False
    activity: str | None = None
    activity_format: Literal["vcd", "saif"] | None = None
    activity_scope: str | None = None
    timeout_seconds: int = Field(default=600, ge=1, le=604800)

    @model_validator(mode="after")
    def explicit_activity(self):
        if bool(self.activity) != bool(self.activity_format):
            raise ValueError("Activity input and its vcd/saif format must be supplied together")
        if self.activity_scope and not self.activity:
            raise ValueError("Activity scope requires an activity input")
        if not self.corner_name.strip():
            raise ValueError("Declare the library corner label explicitly")
        if any(
            "\0" in str(value) or "\n" in str(value) or "\r" in str(value)
            for value in (
                self.netlist,
                self.sdc,
                *self.liberty,
                *self.lef,
                self.spef or "",
                self.activity or "",
                self.activity_scope or "",
            )
        ):
            raise ValueError("Timing input paths/scope must not contain control characters")
        return self


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sta_plan(config: STAConfig | dict, directory: Path, *, base=None, executable="openroad"):
    """Snapshot explicit inputs, then produce native reports with zero inferred SDC.

    corner_name labels one explicit library configuration. Separate plans can
    compare corners; this is not automatic MCMM or qualified timing signoff.
    """
    config = STAConfig.model_validate(config)
    base = Path(base or Path.cwd()).resolve()
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    inputs = directory / "sta-inputs"
    inputs.mkdir(exist_ok=False)
    manifest = {
        "configuration": config.model_dump(),
        "inputs": {},
        "corner": config.corner_name,
        "parasitics": "explicit_spef" if config.spef else "none_cell_delay_only",
        "activity": config.activity_format if config.activity else "tool_default_activity_estimate",
        "scope": "Declared library corner and SDC only; not qualified timing or measured power.",
    }

    def snapshot(value, category, index=0):
        source = (base / Path(value).expanduser()).resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Expected timing input file: {source}")
        folder = inputs / category / str(index)
        folder.mkdir(parents=True)
        target = folder / source.name
        shutil.copyfile(source, target)
        manifest["inputs"][str(target.relative_to(directory))] = {
            "source": str(source),
            "sha256": _hash(target),
        }
        return target

    libraries = [snapshot(value, "liberty", index) for index, value in enumerate(config.liberty)]
    netlist = snapshot(config.netlist, "netlist")
    sdc = snapshot(config.sdc, "sdc")
    lefs = [snapshot(value, "lef", index) for index, value in enumerate(config.lef)]
    lines = [f"read_lef {_tcl(path)}" for path in lefs]
    lines.extend(f"read_liberty {_tcl(path)}" for path in libraries)
    lines.extend(
        [f"read_verilog {_tcl(netlist)}", f"link_design {config.top}", f"read_sdc {_tcl(sdc)}"]
    )
    if config.spef:
        lines.append(f"read_spef {_tcl(snapshot(config.spef, 'spef'))}")
    if config.activity:
        activity = snapshot(config.activity, "activity")
        scope = f"-scope {_tcl(config.activity_scope)} " if config.activity_scope else ""
        lines.append(f"read_{config.activity_format} {scope}{_tcl(activity)}")
    reports = [
        "sta-clocks.rpt",
        "sta-constraints.rpt",
        "sta-setup.rpt",
        "sta-hold.rpt",
        "sta-setup-slack.rpt",
        "sta-hold-slack.rpt",
        "sta-setup-tns.rpt",
        "sta-hold-tns.rpt",
        "sta-electrical.rpt",
    ]
    native = [
        "report_clock_properties [all_clocks]",
        "check_setup -verbose",
        "report_checks -path_delay max -format full_clock_expanded -fields {slew cap fanout} -digits 6",
        "report_checks -path_delay min -format full_clock_expanded -fields {slew cap fanout} -digits 6",
        "report_worst_slack -max -digits 9",
        "report_worst_slack -min -digits 9",
        "report_tns -max -digits 9",
        "report_tns -min -digits 9",
        "report_check_types -max_slew -max_capacitance -violators",
    ]
    lines.append(
        "report_units"
    )  # This public command has no redirection option; retain native log.
    for command, report in zip(native, reports, strict=True):
        # Preserve check_setup's native boolean separately; an empty clean report is valid.
        prefix = "set coverage_ok [" if command.startswith("check_setup") else ""
        suffix = "]" if prefix else ""
        lines.append(f"{prefix}{command} > {_tcl(directory / report)}{suffix}")
    marker = directory / "sta-completion.txt"
    lines.extend(
        [
            f"set stream [open {_tcl(marker)} w]",
            'puts $stream "clock_count [llength [all_clocks]]"',
            'puts $stream "constraint_check_pass $coverage_ok"',
            "close $stream",
        ]
    )
    if config.power:
        lines.append(f"report_power -digits 9 > {_tcl(directory / 'sta-power.rpt')}")
        lines.append("report_activity_annotation")  # Native annotation coverage remains in the log.
        reports.append("sta-power.rpt")
    script = directory / "sta-analysis.tcl"
    # Fail explicitly on Tcl/native command errors instead of continuing to markers.
    script.write_text(
        "if {[catch {\n" + "\n".join(lines) + "\n} message]} {puts stderr $message; exit 1}\n"
    )
    (directory / "sta-manifest.json").write_text(json.dumps(manifest, indent=2))
    return CommandPlan(
        "openroad-sta",
        (
            (
                str(executable),
                "-no_init",
                "-exit",
                "-log",
                str(directory / "sta-native.log"),
                str(script),
            ),
        ),
        directory,
        (*reports, marker.name, script.name, "sta-manifest.json", "sta-native.log"),
        config.timeout_seconds,
    )


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def sta_result(directory: Path, returncode: int | None):
    """Read only recorded native reports. Unknown paths/coverage stay unknown."""
    result = {
        "status": "not_run",
        "execution_status": "not_run",
        "timing_status": "unknown",
        "constraint_status": "unknown",
        "metrics": {},
        "evidence": [],
        "endpoint_coverage": None,
        "power": None,
        "scope": "Specified corner, libraries and SDC; unconstrained paths and unavailable checks are not proven.",
    }
    if returncode is None:
        return result
    if returncode:
        result.update(
            status="failed",
            execution_status="failed",
            reason=f"OpenROAD exited {returncode}; inspect native log",
        )
        return result
    try:
        manifest = json.loads((directory / "sta-manifest.json").read_text())
        result["manifest"] = manifest
        for relative, identity in manifest["inputs"].items():
            source = (directory / relative).resolve()
            if (
                not source.is_relative_to(directory.resolve())
                or _hash(source) != identity["sha256"]
            ):
                raise ValueError("Timing snapshot changed after analysis")
        marker = (directory / "sta-completion.txt").read_text()
        clocks = re.search(r"(?m)^clock_count (\d+)$", marker)
        coverage = re.search(r"(?m)^constraint_check_pass ([01])$", marker)
        if not clocks or not coverage:
            raise ValueError("Native timing completion evidence is missing")
        result["clock_count"] = int(clocks[1])
        native_log = directory / "sta-native.log"
        result["units"] = {}
        if native_log.is_file():
            log_text = native_log.read_text(errors="replace")
            result["units"] = dict(
                re.findall(
                    r"(?m)^\s*(time|capacitance|resistance|voltage|current|power|distance)\s+(\S+)\s*$",
                    log_text,
                )
            )
        result["constraint_status"] = (
            "passed" if coverage[1] == "1" and int(clocks[1]) else "warnings"
        )
        mappings = {
            "setup_slack": ("sta-setup-slack.rpt", "worst slack"),
            "hold_slack": ("sta-hold-slack.rpt", "worst slack"),
            "setup_tns": ("sta-setup-tns.rpt", "tns"),
            "hold_tns": ("sta-hold-tns.rpt", "tns"),
        }
        for name, (filename, label) in mappings.items():
            text = (directory / filename).read_text()
            matches = re.findall(rf"(?m)^\s*{label}(?:\s+(?:min|max))?\s+({NUMBER})\s*$", text)
            value = float(matches[0]) if len(matches) == 1 else None
            # A rounded negative zero cannot establish a nonnegative slack/TNS.
            if (
                value is not None
                and math.isfinite(value)
                and not (value == 0 and matches[0].startswith("-"))
            ):
                result["metrics"][name] = {
                    "value": value,
                    "path": filename,
                    "corner": manifest["corner"],
                }
        if len(result["metrics"]) == 4 and int(clocks[1]):
            metrics = result["metrics"]
            passing = (
                metrics["setup_slack"]["value"] >= 0
                and metrics["hold_slack"]["value"] >= 0
                and metrics["setup_tns"]["value"] == 0
                and metrics["hold_tns"]["value"] == 0
            )
            result["timing_status"] = "passed" if passing else "failed"
        if manifest["configuration"]["power"]:
            text = (directory / "sta-power.rpt").read_text()
            matches = re.findall(
                rf"(?m)^\s*Total\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})(?:\s|$)", text
            )
            if len(matches) == 1 and "Power (Watts)" in text:
                values = [float(value) for value in matches[0]]
                if all(math.isfinite(value) and value >= 0 for value in values):
                    result["power"] = dict(
                        zip(
                            ("internal_w", "switching_w", "leakage_w", "total_w"),
                            values,
                            strict=True,
                        )
                    )
                    result["power"].update(
                        activity=manifest["activity"],
                        estimate=True,
                        measured=False,
                        evidence="sta-power.rpt",
                        assumptions="Liberty models, selected corner and native activity propagation; review units and activity annotation report.",
                    )
        result["execution_status"] = "completed"
        result["status"] = (
            "passed"
            if result["timing_status"] == "passed" and result["constraint_status"] == "passed"
            else "failed"
            if result["timing_status"] == "failed"
            else "incomplete"
        )
        result["evidence"] = [
            {"path": path.name, "sha256": _hash(path)}
            for path in sorted(directory.glob("sta-*.rpt"))
        ]
        if native_log.is_file():
            result["evidence"].append({"path": native_log.name, "sha256": _hash(native_log)})
        result["reason"] = (
            "Native reports apply only to explicit loaded inputs; review coverage warnings and omitted parasitics."
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.update(status="unknown", reason=str(exc))
    return result
