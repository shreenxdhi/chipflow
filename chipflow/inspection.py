"""Native layout/netlist inspection; no custom GDS parser or signoff inference."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .environment import find_tool
from .verification import CommandPlan

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _file(path):
    result = Path(path).expanduser().resolve(strict=True)
    if not result.is_file() or any(char in str(result) for char in "\n\r\0"):
        raise ValueError(f"Expected a regular input file: {path}")
    return result


def _identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("Expected a simple HDL top identifier")


def _quoted(value):
    # Yosys string syntax supports quoted paths; JSON escapes quote/backslash.
    return json.dumps(str(value))


def _native_gds(path, expected_top=None, module=None):
    if module is None:
        import klayout.db as module
    layout = module.Layout()
    layout.read(str(path))
    tops = sorted(cell.name for cell in layout.top_cells())
    layers = [
        {"layer": info.layer, "datatype": info.datatype, "name": info.name}
        for index in layout.layer_indexes()
        for info in [layout.get_info(index)]
    ]
    top_metadata = []
    for cell in layout.top_cells():
        box = cell.bbox()
        top_metadata.append(
            {
                "name": cell.name,
                "empty": box.empty(),
                "bbox_dbu": None if box.empty() else [box.left, box.bottom, box.right, box.top],
            }
        )
    issues = []
    if not tops:
        issues.append("Layout has no top cell.")
    if not any(not item["empty"] for item in top_metadata):
        issues.append("Layout has no top-level geometry.")
    if expected_top is not None and tops != [expected_top]:
        issues.append(f"Expected the single top cell {expected_top!r}; read {tops!r}.")
    return {
        "status": "failed" if issues else "passed",
        "tool": "KLayout",
        "checks": ["native_gds_read", "top_cell_identity", "nonempty_geometry"],
        "expected_top": expected_top,
        "top_cells": tops,
        "cell_count": layout.cells(),
        "database_unit_um": layout.dbu,
        "layers": layers,
        "top_metadata": top_metadata,
        "issues": issues,
        "scope": "Native layout structure inspection only; no DRC, LVS or tapeout qualification.",
    }


# This macro is passed to the real KLayout executable when its Python wheel is absent.
# Arguments arrive via KLayout's -rd globals, never source interpolation.
KLAYOUT_MACRO = """import json
import pya
from chipflow.inspection import _native_gds
try:
    result = _native_gds(chipflow_input, chipflow_top or None, pya)
except Exception as exc:
    result = {"status": "failed", "tool": "KLayout", "issues": [str(exc)]}
with open(chipflow_output, "w") as stream:
    json.dump(result, stream, allow_nan=False)
"""


def gds_plan(path, directory: Path, expected_top=None, timeout_seconds=60):
    path = _file(path)
    if expected_top is not None:
        _identifier(expected_top)
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "inspection-gds.json"
    output.unlink(missing_ok=True)
    try:
        package = importlib.util.find_spec("klayout.db") is not None
    except ModuleNotFoundError:
        package = False
    if package:
        argv = (
            sys.executable,
            "-m",
            "chipflow.inspection",
            "--gds-worker",
            str(path),
            str(output),
            expected_top or "",
        )
    else:
        executable = find_tool("klayout")
        if not executable:
            return None
        macro = directory / "inspect-gds.py"
        macro.write_text(
            "import json\nimport pya\n" + inspect.getsource(_native_gds) + "\n" + KLAYOUT_MACRO
        )
        argv = (
            executable,
            "-b",
            "-r",
            str(macro),
            "-rd",
            f"chipflow_input={path}",
            "-rd",
            f"chipflow_output={output}",
            "-rd",
            f"chipflow_top={expected_top or ''}",
        )
    return CommandPlan("klayout-inspection", (argv,), directory, (output.name,), timeout_seconds)


def inspect_gds(path, expected_top=None, timeout_seconds=60):
    """Bounded convenience API; supervised callers should use gds_plan instead."""
    path = _file(path)
    with tempfile.TemporaryDirectory(prefix="chipflow-inspect-") as folder:
        plan = gds_plan(path, Path(folder), expected_top, timeout_seconds)
        if plan is None:
            return {
                "status": "unavailable",
                "tool": "KLayout",
                "issues": [
                    "Install KLayout or its optional Python package for native GDS inspection."
                ],
            }
        try:
            process = subprocess.run(
                plan.commands[0],
                cwd=plan.cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if process.returncode:
                return {
                    "status": "failed",
                    "tool": "KLayout",
                    "issues": [process.stderr[-4000:] or f"KLayout exited {process.returncode}"],
                }
            result = json.loads((plan.cwd / "inspection-gds.json").read_text())
            result["source"] = str(path)
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return {"status": "failed", "tool": "KLayout", "issues": [str(exc)]}


def netlist_plan(path, top, directory: Path, libraries=(), timeout_seconds=60):
    """Yosys elaboration, hierarchy and structural checks, not equivalence."""
    path = _file(path)
    _identifier(top)
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    commands = []
    for library in libraries:
        source = _file(library)
        reader = "read_liberty -lib" if source.suffix == ".lib" else "read_verilog -lib"
        commands.append(f"{reader} {_quoted(source)}")
    commands.extend(
        [
            f"read_verilog -sv {_quoted(path)}",
            f"hierarchy -check -top {top}",
            "proc",
            "check -assert",
            "tee -o inspection-netlist.json stat -json",
        ]
    )
    script = directory / "inspect-netlist.ys"
    script.write_text("\n".join(commands) + "\n")
    (directory / "inspection-netlist.json").unlink(missing_ok=True)
    return CommandPlan(
        "yosys-netlist-inspection",
        (("yosys", "-Q", "-T", "-s", str(script)),),
        directory,
        ("inspection-netlist.json", script.name),
        timeout_seconds,
    )


def netlist_result(path, top, returncode):
    if returncode is None:
        return {"status": "not_run", "tool": "Yosys", "issues": ["No execution recorded."]}
    if returncode:
        return {
            "status": "failed",
            "tool": "Yosys",
            "issues": [f"Yosys exited {returncode}; inspect the native log."],
        }
    try:
        metadata = json.loads(Path(path).read_text())
        if f"\\{top}" not in metadata.get("modules", {}):
            raise ValueError("Expected top is missing from native Yosys statistics.")
        return {
            "status": "passed",
            "tool": "Yosys",
            "metadata": metadata,
            "checks": ["elaboration", "hierarchy_check", "structural_check"],
            "evidence": [str(path)],
            "scope": "Structural checks only; no functional equivalence or timing proof.",
        }
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "failed", "tool": "Yosys", "issues": [str(exc)]}


def _render_native(
    path, output, expected_top=None, module=None, lay_module=None, width=1024, height=768
):
    result = _native_gds(path, expected_top, module)
    if result["status"] != "passed":
        return result
    if lay_module is None:
        import klayout.lay as lay_module
    view = lay_module.LayoutView()
    view.load_layout(str(path), False)
    # Engineering overview: labels/instance boxes obscure dense standard cells.
    view.set_config("text-visible", "false")
    view.set_config("cell-box-visible", "false")
    view.set_config("grid-visible", "false")
    rendered_top = expected_top or result["top_cells"][0]
    cell = view.cellview(0).layout().cell(rendered_top)
    view.select_cell(cell.cell_index(), 0)
    view.max_hier()
    view.zoom_fit()
    view.save_image(str(output), width, height)
    if not Path(output).is_file() or Path(output).stat().st_size == 0:
        raise ValueError("Native renderer did not write an image")
    with Path(path).open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    result.update(
        image=str(output),
        rendered_top=rendered_top,
        width=width,
        height=height,
        renderer="KLayout LayoutView",
        source_sha256=source_hash,
        scope="Native rendered layout preview only; no interactive editing, DRC or signoff.",
    )
    return result


def preview_gds_plan(path, directory: Path, expected_top=None, timeout_seconds=60):
    """Supervised native renderer plan; images and their metadata stay run-local."""
    path = _file(path)
    if expected_top is not None:
        _identifier(expected_top)
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "layout-preview.png"
    metadata = directory / "layout-preview.json"
    for stale in (output, metadata):
        stale.unlink(missing_ok=True)
    try:
        package = importlib.util.find_spec("klayout.lay") is not None
    except ModuleNotFoundError:
        package = False
    if package:
        argv = (
            sys.executable,
            "-m",
            "chipflow.inspection",
            "--preview-worker",
            str(path),
            str(output),
            str(metadata),
            expected_top or "",
        )
    else:
        executable = find_tool("klayout")
        if not executable:
            return None
        macro = directory / "preview-gds.py"
        body = """try:
    result = _render_native(chipflow_input, chipflow_image, chipflow_top or None, pya, pya)
except Exception as exc:
    result = {"status": "failed", "tool": "KLayout", "issues": [str(exc)]}
with open(chipflow_metadata, "w") as stream:
    json.dump(result, stream, allow_nan=False)
"""
        macro.write_text(
            "import json\nimport hashlib\nfrom pathlib import Path\nimport pya\n"
            + inspect.getsource(_native_gds)
            + "\n"
            + inspect.getsource(_render_native)
            + "\n"
            + body
        )
        argv = (
            executable,
            "-b",
            "-r",
            str(macro),
            "-rd",
            f"chipflow_input={path}",
            "-rd",
            f"chipflow_image={output}",
            "-rd",
            f"chipflow_metadata={metadata}",
            "-rd",
            f"chipflow_top={expected_top or ''}",
        )
    return CommandPlan(
        "klayout-layout-preview", (argv,), directory, (output.name, metadata.name), timeout_seconds
    )


def preview_gds(path, output, expected_top=None, timeout_seconds=60):
    """Convenience wrapper; execution workers should supervise preview_gds_plan."""
    path = _file(path)
    output = Path(output).resolve()
    if path == output:
        raise ValueError("Preview output must differ from the source layout")
    with tempfile.TemporaryDirectory(prefix="chipflow-preview-") as folder:
        plan = preview_gds_plan(path, Path(folder), expected_top, timeout_seconds)
        if plan is None:
            return {
                "status": "unavailable",
                "tool": "KLayout",
                "issues": ["Native KLayout rendering is unavailable."],
            }
        try:
            process = subprocess.run(
                plan.commands[0],
                cwd=plan.cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if process.returncode:
                return {
                    "status": "failed",
                    "tool": "KLayout",
                    "issues": [process.stderr[-4000:] or f"Renderer exited {process.returncode}"],
                }
            result = json.loads((plan.cwd / "layout-preview.json").read_text())
            if result.get("status") == "passed":
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(plan.cwd / "layout-preview.png", output)
                result["image"] = str(output)
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return {"status": "failed", "tool": "KLayout", "issues": [str(exc)]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--gds-worker", nargs=3, metavar=("INPUT", "OUTPUT", "TOP"))
    parser.add_argument("--preview-worker", nargs=4, metavar=("INPUT", "IMAGE", "METADATA", "TOP"))
    args = parser.parse_args(argv)
    if args.gds_worker:
        source, output, top = args.gds_worker
        try:
            result = _native_gds(source, top or None)
            import klayout.db as db

            result["tool_version"] = getattr(db, "__version__", "unknown")
        except Exception as exc:
            result = {"status": "failed", "tool": "KLayout", "issues": [str(exc)]}
        Path(output).write_text(json.dumps(result, allow_nan=False))
    if args.preview_worker:
        source, output, metadata, top = args.preview_worker
        try:
            result = _render_native(source, output, top or None)
        except Exception as exc:
            result = {"status": "failed", "tool": "KLayout", "issues": [str(exc)]}
        Path(metadata).write_text(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
