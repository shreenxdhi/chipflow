"""Shared local settings and toolchain discovery."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .storage import save


def settings_path() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "chipflow"
        / "settings.json"
    )


def read_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) - {"version", "orfs", "bin_dirs"}:
            raise ValueError("unsupported settings fields")
        if value.get("version", 1) != 1:
            raise ValueError("unsupported settings version")
        if value.get("orfs") is not None and not isinstance(value["orfs"], str):
            raise ValueError("orfs must be a path")
        if not isinstance(value.get("bin_dirs", []), list) or any(
            not isinstance(p, str) for p in value.get("bin_dirs", [])
        ):
            raise ValueError("bin_dirs must contain directory paths")
        return value
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read settings at {path}: {exc}") from exc


def write_settings(orfs: str | None = None, bin_dirs: list[str] | None = None) -> Path:
    value = {**read_settings(), "version": 1}
    if orfs is not None:
        root = Path(orfs).expanduser().resolve()
        if not (flow_path(root) / "Makefile").is_file():
            raise ValueError(f"ORFS Makefile not found: {root}")
        value["orfs"] = str(root)
    if bin_dirs is not None:
        paths = [Path(p).expanduser().resolve() for p in bin_dirs]
        if any(not p.is_dir() for p in paths):
            raise ValueError("Every --bin-dir must be an existing directory")
        value["bin_dirs"] = list(dict.fromkeys(map(str, paths)))
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    save(path, value)
    return path


def flow_path(root: Path | str) -> Path:
    root = Path(root).expanduser().resolve()
    return (
        root
        if (root / "Makefile").is_file() and (root / "scripts").is_dir() or root.name == "flow"
        else root / "flow"
    )


def discover_orfs() -> str:
    configured = os.environ.get("ORFS_HOME") or read_settings().get("orfs")
    if configured:
        return str(Path(configured).expanduser().resolve())
    candidates = [
        os.environ.get("FLOW_HOME", ""),
        Path.cwd() / "OpenROAD-flow-scripts",
        Path.home() / "OpenROAD-flow-scripts",
        Path.home() / "vlsi-tools" / "OpenROAD-flow-scripts",
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        / "chipflow"
        / "OpenROAD-flow-scripts",
    ]
    for candidate in candidates:
        if candidate and (flow_path(candidate) / "Makefile").is_file():
            return str(Path(candidate).expanduser().resolve())
    return str(
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        / "chipflow"
        / "OpenROAD-flow-scripts"
    )


def orfs_bin_dirs(root: str | Path) -> list[str]:
    root = flow_path(root).parent
    return [
        str(root / "tools" / "install" / "OpenROAD" / "bin"),
        str(root / "tools" / "install" / "yosys" / "bin"),
        str(root / "tools" / "install" / "klayout"),
    ]


def tool_search_path(extra_dirs: list[str] | None = None) -> str:
    directories = [
        *(extra_dirs or []),
        *read_settings().get("bin_dirs", []),
        *orfs_bin_dirs(discover_orfs()),
        *os.environ.get("PATH", "").split(os.pathsep),
    ]
    return os.pathsep.join(dict.fromkeys(str(Path(p).expanduser()) for p in directories if p))


def activate_environment(extra_dirs: list[str] | None = None) -> None:
    os.environ["PATH"] = tool_search_path(extra_dirs)


def find_tool(name: str, extra_dirs: list[str] | None = None) -> str | None:
    return shutil.which(name, path=tool_search_path(extra_dirs))


def bundled_example() -> Path:
    return Path(__file__).parent / "examples" / "counter" / "chipflow.yaml"


def init_project(destination: Path, orfs: str | None = None) -> Path:
    import yaml

    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}. Choose a new directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundled_example().parent, destination)
    config = destination / "chipflow.yaml"
    data = yaml.safe_load(config.read_text())
    data["orfs"] = orfs or discover_orfs()
    config.write_text(yaml.safe_dump(data, sort_keys=False))
    return config
