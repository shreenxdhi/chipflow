"""Local project discovery and preflight checks for the dashboard."""

import uuid
from pathlib import Path

import yaml

from .config import Project, load_project, safe_make_path
from .engine import STAGES
from .environment import bundled_example, discover_orfs, find_tool, orfs_bin_dirs
from .setup import assess


def resolve(base: Path, value: str) -> Path:
    return (base / Path(value).expanduser()).resolve()


class PreflightError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Fix before starting: " + " | ".join(issues))


def workspace():
    report = assess(versions=False)
    return {
        "directory": str(Path.cwd()),
        "orfs": discover_orfs(),
        "platforms": report["platforms"],
        "tools": {name: value["path"] for name, value in report["tools"].items()},
        "example": str(bundled_example()),
        "setup": report,
    }


def inspect_project(path: Path, target: str):
    if target not in STAGES:
        raise ValueError("Choose a valid run stage.")
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(
            f"Project file not found: {path}. Choose the counter example or enter the path to an existing configuration."
        )
    project, base = load_project(path)
    issues = []
    inputs = []
    groups = [("RTL source", project.rtl, False), ("Include folder", project.include_dirs, True)]
    if project.sdc:
        groups.append(("Timing constraints", [project.sdc], False))
    if project.simulation:
        groups.extend(
            [
                ("Testbench", project.simulation.testbench, False),
                ("Cell model", project.simulation.cell_models, False),
            ]
        )
    for label, values, directory in groups:
        for value in values:
            source = resolve(base, value)
            exists = source.is_dir() if directory else source.is_file()
            inputs.append({"kind": label, "path": str(source), "exists": exists})
            if not exists:
                issues.append(f"{label} not found: {source}. Check the path in your project.")
            try:
                safe_make_path(source)
            except ValueError:
                issues.append(
                    f"{label} path contains spaces or unsupported characters: {source}. Move it to a simple path before running."
                )
    sim = target in {"sim", "gate_sim"}
    if sim and not project.simulation:
        issues.append("Add a testbench and its top module to run a simulation.")
    if target == "gate_sim" and project.simulation and not project.simulation.cell_models:
        issues.append(
            "Gate simulation needs functional cell models matching your platform. Add them in Advanced settings."
        )
    required = ["iverilog", "vvp"] if sim else []
    if target != "sim":
        required.extend(["make", "yosys", "openroad"])
        if target == "finish":
            required.append("klayout")
        flow = resolve(base, project.orfs)
        if flow.name != "flow":
            flow /= "flow"
        if not (flow / "Makefile").is_file():
            issues.append(
                f"ORFS Makefile not found in {flow}. Update the OpenROAD flow folder in your project."
            )
        if not (flow / "platforms" / project.platform / "config.mk").is_file():
            issues.append(
                f"Platform '{project.platform}' is unavailable in this ORFS installation. Choose an installed platform."
            )
    try:
        safe_make_path(resolve(base, project.orfs))
    except ValueError as exc:
        issues.append(str(exc))
    for tool in required:
        if not find_tool(tool, orfs_bin_dirs(resolve(base, project.orfs))):
            issues.append(
                f"{tool} is not on PATH. Make it available to the Chipflow server before running this stage."
            )
    return {
        "path": str(path),
        "orfs": str(resolve(base, project.orfs)),
        "project": project.model_dump(),
        "inputs": inputs,
        "ready": not issues,
        "issues": issues,
        "target": target,
    }


def list_projects(folder: Path):
    items = []
    paths = [*sorted(folder.glob("*.yaml")), *Path("examples").glob("*/*.yaml"), bundled_example()]
    seen = set()
    for path in paths:
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        try:
            project, _ = load_project(path)
            items.append(
                {
                    "path": str(path.resolve()),
                    "name": project.name,
                    "platform": project.platform,
                    "example": not path.is_relative_to(folder),
                }
            )
        except (ValueError, OSError):
            continue
    return items


def create_project(folder: Path, project: Project):
    # New versions get new files: source projects and existing runs remain reproducible.
    data = project.model_dump()
    base = Path.cwd()
    for field in ["rtl", "include_dirs"]:
        data[field] = [str(resolve(base, p)) for p in data[field]]
    data["orfs"] = str(resolve(base, data["orfs"]))
    if data["sdc"]:
        data["sdc"] = str(resolve(base, data["sdc"]))
    if data["simulation"]:
        for field in ["testbench", "cell_models"]:
            data["simulation"][field] = [str(resolve(base, p)) for p in data["simulation"][field]]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{project.name}-{uuid.uuid4().hex[:8]}.yaml"
    with path.open("x") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return {"path": str(path.resolve()), "name": project.name}
