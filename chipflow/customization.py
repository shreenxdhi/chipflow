"""Native ORFS customization points and configuration reference."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .environment import discover_orfs, flow_path

HOOK_STEPS = {
    "SYNTH": ("synth", "Synthesis database"),
    "FLOORPLAN": ("floorplan", "Floorplan"),
    "MACRO_PLACE": ("floorplan", "Macro placement"),
    "TAPCELL": ("floorplan", "Tap/endcap insertion"),
    "PDN": ("floorplan", "Power grid generation"),
    "GLOBAL_PLACE_SKIP_IO": ("place", "Initial global placement"),
    "IO_PLACEMENT": ("place", "Pin placement"),
    "GLOBAL_PLACE": ("place", "Global placement"),
    "RESIZE": ("place", "Timing-driven resizing"),
    "DETAIL_PLACE": ("place", "Detailed placement"),
    "CTS": ("cts", "Clock tree synthesis"),
    "GLOBAL_ROUTE": ("route", "Global routing"),
    "DETAIL_ROUTE": ("route", "Detailed routing"),
    "FILLCELL": ("route", "Filler cell insertion"),
    "DENSITY_FILL": ("finish", "Metal density fill"),
    "FINAL_REPORT": ("finish", "Final reporting"),
}
TCL_POINTS = {
    f"{position}_{step}_TCL": {
        "stage": stage,
        "description": f"{position.title()} {label.lower()}",
        "kind": "hook",
    }
    for step, (stage, label) in HOOK_STEPS.items()
    for position in ("PRE", "POST")
}
TCL_POINTS.update(
    {
        key: {"stage": stage, "description": description, "kind": "override"}
        for key, stage, description in [
            ("SYNTH_CANONICALIZE_TCL", "synth", "Additional Yosys canonicalization commands"),
            ("SYNTH_SCRIPT", "synth", "Replace the Yosys synthesis script"),
            ("TAPCELL_TCL", "floorplan", "Replace tap/endcap insertion policy"),
            ("PDN_TCL", "floorplan", "Replace power-grid policy"),
            ("MACRO_PLACEMENT_TCL", "floorplan", "Custom macro placement"),
            ("IO_CONSTRAINTS", "floorplan", "Custom pin placement constraints"),
            ("FASTROUTE_TCL", "route", "Custom global routing setup"),
            ("FOOTPRINT_TCL", "floorplan", "Custom footprint setup"),
        ]
    }
)
RESERVED = {
    "DESIGN_NAME",
    "PLATFORM",
    "VERILOG_FILES",
    "VERILOG_INCLUDE_DIRS",
    "SDC_FILE",
    "DESIGN_CONFIG",
    "FLOW_HOME",
    "WORK_HOME",
    "RESULTS_DIR",
    "REPORTS_DIR",
    "LOG_DIR",
    "OBJECTS_DIR",
    "SCRIPTS_DIR",
    "NUM_CORES",
    "OPENROAD_EXE",
    "YOSYS_EXE",
    "KLAYOUT_CMD",
    "KLAYOUT_EXE",
    "SHELL",
    "PATH",
    "HOME",
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "WRITE_ODB_AND_SDC_EACH_STAGE",
    "KEEP_VARS",
    "FLOW_VARIANT",
}


def protected(name):
    return name in RESERVED or name.startswith("CHIPFLOW_")


def make_value(value):
    return "1" if value is True else "0" if value is False else str(value)


def validate_variable(name, value):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or protected(name):
        raise ValueError(f"{name} is managed by Chipflow or is not an ORFS variable name")
    if name in TCL_POINTS:
        raise ValueError(f"Configure {name} in tcl.hooks so its script is preserved")
    if not re.fullmatch(r"[A-Za-z0-9_./+,:@%*?\[\]{}= \-]*", make_value(value)):
        raise ValueError(f"{name} contains Make/shell expressions or unsupported characters")


def variable_catalog(orfs=None):
    flow = flow_path(orfs or discover_orfs())
    for name in ("variables.json", "variables.yaml"):
        path = flow / "scripts" / name
        if path.is_file():
            try:
                data = (
                    json.loads(path.read_text())
                    if name.endswith("json")
                    else yaml.safe_load(path.read_text())
                )
                if not isinstance(data, dict):
                    raise ValueError("expected a variable mapping")
                return {
                    key: {
                        "description": str(value.get("description", "")).strip(),
                        "default": value.get("default"),
                        "managed": protected(key),
                        "script": key in TCL_POINTS,
                    }
                    for key, value in data.items()
                    if isinstance(value, dict)
                }
            except (ValueError, OSError, yaml.YAMLError) as exc:
                raise ValueError(f"Cannot read ORFS variable reference at {path}: {exc}") from exc
    return {}


def supported_points(flow):
    """Inspect the installed scripts, including hooks derived by source_step_tcl."""
    points = set()
    scripts = Path(flow) / "scripts"
    for path in scripts.glob("*.tcl"):
        text = path.read_text(errors="replace")
        points.update(name for name in TCL_POINTS if re.search(r"\b" + name + r"\b", text))
        for position, step in re.findall(r"source_step_tcl\s+(PRE|POST)\s+([A-Z_]+)", text):
            points.add(f"{position}_{step}_TCL")
    makefile = Path(flow) / "Makefile"
    if makefile.exists() and "SYNTH_SCRIPT" in makefile.read_text():
        points.add("SYNTH_SCRIPT")
    return points
