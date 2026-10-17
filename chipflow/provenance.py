"""Additive environment evidence and explicit retry compatibility decisions.

A manifest describes observations, not a hermetic build guarantee. External
libraries and untracked tool dependencies can remain outside the captured scope.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

from . import __version__
from .environment import find_tool, flow_path, orfs_bin_dirs
from .execution import ExecutionConfig, container_identity, probe_command

TOOL_PROBES = {
    "iverilog": ["-V"],
    "vvp": ["-V"],
    "yosys": ["-V"],
    "openroad": ["-version"],
    "klayout": ["-v"],
    "verilator": ["--version"],
    "make": ["--version"],
}


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _probe(argv, cwd=None, environment=None):
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", **(environment or {})},
        )
        return {
            "status": "available" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "output": (result.stdout + result.stderr).strip()[:8000],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "returncode": None, "output": str(exc)}


def git_identity(root: Path) -> dict:
    revision = _probe(["git", "rev-parse", "HEAD"], root)
    if revision["status"] != "available":
        return {"status": "unknown"}
    dirty = _probe(
        ["git", "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"], root
    )
    try:
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--"],
            cwd=root,
            capture_output=True,
            timeout=15,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        diff = None
    return {
        "status": "available",
        "revision": revision["output"],
        "dirty": bool(dirty["output"]) if dirty["status"] == "available" else None,
        "diff_sha256": hashlib.sha256(diff).hexdigest() if diff is not None else None,
        "scope": "tracked files; submodule content and untracked files excluded",
    }


def directory_identity(root: Path) -> dict:
    """Hash explicit external mounts without storing their content or environment."""
    root = root.expanduser().resolve()
    digest = hashlib.sha256()
    count, unknown = 0, []
    if not root.is_dir():
        return {"status": "unavailable", "sha256": None}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink() and path.is_dir():
            unknown.append(relative)
            continue
        if not path.is_file():
            continue
        try:
            item = sha256(path)
            digest.update((relative + "\0" + item + "\n").encode())
            count += 1
        except OSError:
            unknown.append(relative)
    return {
        "status": "available" if not unknown else "incomplete",
        "sha256": digest.hexdigest(),
        "files": count,
        "unreadable_or_directory_symlinks": unknown,
    }


def capture(project, run_dir: Path, input_hashes: dict | None = None, target="finish") -> dict:
    """Capture the selected backend and platform. Does not read ambient secrets.

    Explicit environment dependency values are never stored; their presence is
    recorded as unknown evidence because publishing low-entropy secret hashes can
    disclose values. Strict retries require review of declared dependencies.
    """
    execution = getattr(project, "execution", ExecutionConfig())
    flow = flow_path(project.orfs)
    config = execution.model_dump(exclude={"environment"})
    config["environment_names"] = sorted(execution.environment)
    tools = {}
    for name, args in TOOL_PROBES.items():
        if execution.backend == "container":
            result = probe_command(execution, [name, *args], run_dir, flow)
            tools[name] = {**result, "identity": "container image digest"}
        else:
            path = find_tool(name, orfs_bin_dirs(flow))
            if path:
                resolved = Path(path).resolve()
                try:
                    digest = sha256(resolved)
                except OSError:
                    digest = None
                tools[name] = {
                    **_probe([path, *args], environment=execution.environment),
                    "path": path,
                    "resolved_path": str(resolved),
                    "sha256": digest,
                }
            else:
                tools[name] = {"status": "unavailable", "path": None, "sha256": None}
    platform_dir = flow / "platforms" / project.platform
    assets, unreadable = {}, []
    if platform_dir.is_dir():
        for item in sorted(platform_dir.rglob("*")):
            if item.is_file() and not item.is_symlink():
                try:
                    assets[str(item.relative_to(platform_dir))] = sha256(item)
                except OSError:
                    unreadable.append(str(item.relative_to(platform_dir)))
    source = Path(__file__).resolve().parent.parent
    return {
        "schema_version": 1,
        "target": target,
        "chipflow": {"version": __version__, "source": git_identity(source)},
        "host": {
            "system": platform.system(),
            "architecture": platform.machine(),
            "python": sys.version.split()[0],
        },
        "execution": config,
        "image": container_identity(execution) if execution.backend == "container" else None,
        "external_mounts": {
            str(Path(path).expanduser().resolve()): directory_identity(Path(path))
            for path in execution.readonly_mounts
        },
        "tools": tools,
        "orfs": {"path": str(flow), **git_identity(flow)}
        if flow.exists()
        else {"path": str(flow), "status": "unavailable"},
        "platform": {
            "name": project.platform,
            "files_sha256": assets,
            "unreadable": unreadable,
            "scope": "regular files within platform directory; symlink targets excluded",
        },
        "inputs_sha256": input_hashes or {},
        "settings": {"threads": project.threads, "timeout_seconds": project.timeout_seconds},
        "limitations": [
            "Version and executable identity do not capture all shared libraries or runtime dependencies.",
            "Declared environment values are not recorded and require independent review.",
        ],
    }


def compare(original: dict | None, current: dict) -> dict:
    """Compare relevant identities, returning differences and missing evidence."""
    if not original:
        return {
            "compatible": False,
            "differences": [],
            "unknown": ["original manifest unavailable"],
        }
    differences, unknown = [], []

    def walk(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                if key not in left or key not in right:
                    differences.append(
                        {"field": f"{path}.{key}", "before": left.get(key), "after": right.get(key)}
                    )
                else:
                    walk(left[key], right[key], f"{path}.{key}")
        elif left != right:
            differences.append({"field": path, "before": left, "after": right})

    for section in [
        "chipflow",
        "execution",
        "image",
        "tools",
        "orfs",
        "platform",
        "settings",
        "inputs_sha256",
        "external_mounts",
    ]:
        if section not in original or section not in current:
            unknown.append(f"{section} identity unavailable")
        else:
            walk(original[section], current[section], section)
    required = (
        {"iverilog", "vvp"} if current.get("target") == "sim" else {"make", "yosys", "openroad"}
    )
    if current.get("target") == "finish":
        required.add("klayout")
    for name, tool in current.get("tools", {}).items():
        if name in required and tool.get("status") != "available":
            unknown.append(f"{name} unavailable")
    if current.get("target") != "sim" and not current.get("platform", {}).get("files_sha256"):
        unknown.append("platform identity unavailable")
    for path, identity in current.get("external_mounts", {}).items():
        if identity.get("status") != "available":
            unknown.append(f"external mount identity incomplete: {path}")
    if current.get("execution", {}).get("environment_names"):
        unknown.append("declared environment values require independent review")
    return {
        "compatible": not differences and not unknown,
        "differences": differences,
        "unknown": unknown,
    }


def enforce_comparison(result: dict, policy: str) -> dict:
    if policy not in {"warn", "strict", "ignore"}:
        raise ValueError("Unknown reproducibility policy")
    if policy == "strict" and not result["compatible"]:
        changed = ", ".join(item["field"] for item in result["differences"])
        details = "; ".join(filter(None, [changed, *result["unknown"]]))
        raise ValueError("Strict reproducibility check failed: " + details)
    return {**result, "policy": policy}
