"""Read-only prerequisite assessment with platform-specific installation choices."""

from __future__ import annotations

import os
import platform as system_platform
import re
import subprocess

from .environment import discover_orfs, find_tool, flow_path, orfs_bin_dirs, settings_path
from .resources import inventory

ORFS_DOCS = "https://openroad-flow-scripts.readthedocs.io/en/latest/"
SOURCES = {
    "iverilog": "https://steveicarus.github.io/iverilog/usage/installation.html",
    "vvp": "https://steveicarus.github.io/iverilog/usage/installation.html",
    "make": "https://www.gnu.org/software/make/",
    "yosys": "https://yosyshq.readthedocs.io/projects/yosys/en/latest/getting_started/installation.html",
    "openroad": "https://openroad-flow-scripts.readthedocs.io/en/latest/user/BuildWithPrebuilt.html",
    "klayout": "https://www.klayout.de/build.html",
}
TOOL_ARGS = {
    "iverilog": ["-V"],
    "vvp": ["-V"],
    "make": ["--version"],
    "yosys": ["-V"],
    "openroad": ["-version"],
    "klayout": ["-v"],
}


def host_info() -> dict:
    try:
        release = system_platform.freedesktop_os_release()
    except OSError:
        release = {}
    return {
        "system": system_platform.system(),
        "architecture": system_platform.machine(),
        "distribution": release.get("ID", "unknown"),
        "version": release.get("VERSION_ID", "unknown"),
    }


def install_hint(tool: str, host: dict) -> str:
    distro = host["distribution"]
    if tool in {"openroad", "yosys"}:
        return f"Install {tool} from the same ORFS toolchain revision. Follow {ORFS_DOCS} and run chipflow setup again."
    package = "iverilog" if tool == "vvp" else tool
    if distro in {"ubuntu", "debian", "linuxmint", "pop"}:
        return f"sudo apt update && sudo apt install {package}"
    if distro in {"fedora", "rhel", "rocky", "almalinux"}:
        return f"sudo dnf install {package} (package availability depends on enabled repositories)"
    if distro in {"arch", "manjaro"}:
        return f"sudo pacman -S {package}"
    return f"Install {package} using your distribution's package manager or {SOURCES[tool]}"


def assess(
    orfs: str | None = None, target="finish", platform="nangate45", versions=True, bin_dirs=None
) -> dict:
    if target not in {"sim", "synth", "gate_sim", "floorplan", "place", "cts", "route", "finish"}:
        raise ValueError("Unknown target")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", platform):
        raise ValueError("Platform must be a simple identifier")
    root = orfs or discover_orfs()
    effective_dirs = [*(bin_dirs or []), *orfs_bin_dirs(root)]
    host = host_info()
    checks = []
    required = ["iverilog", "vvp"] if target in {"sim", "gate_sim"} else []
    if target != "sim":
        required += ["make", "yosys", "openroad"]
        if target == "finish":
            required.append("klayout")
    if host["system"] != "Linux":
        checks.append(
            {
                "name": "Operating system",
                "status": "unsupported",
                "detail": f"{host['system']} is not in the validated runtime matrix.",
                "action": "Use Linux or a Linux VM. Installation on other hosts does not establish flow compatibility.",
            }
        )
    tools = {}
    for name, args in TOOL_ARGS.items():
        path = find_tool(name, effective_dirs)
        version = None
        problem = None
        if path and versions:
            try:
                process = subprocess.run(
                    [path, *args],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                )
                output = (process.stdout + process.stderr).strip()
                version = output.splitlines()[0] if output else f"exit {process.returncode}"
                if process.returncode:
                    problem = f"Version probe exited {process.returncode}: {version}"
            except (OSError, subprocess.TimeoutExpired) as exc:
                version = f"Could not probe version: {exc}"
                problem = version
        tools[name] = {
            "path": path,
            "version": version,
            "required": name in required,
            "source": SOURCES[name],
            "probe_error": problem,
        }
        if name in required:
            checks.append(
                {
                    "name": name,
                    "status": "broken" if problem else "available" if path else "missing",
                    "detail": problem or path or "Executable not found",
                    "action": "Repair the executable or its shared libraries, then rerun chipflow setup. "
                    + install_hint(name, host)
                    if problem
                    else ""
                    if path
                    else install_hint(name, host),
                    "source": SOURCES[name],
                }
            )
    root = orfs or discover_orfs()
    flow = flow_path(root)
    available_platforms = sorted(
        p.name for p in (flow / "platforms").glob("*") if (p / "config.mk").is_file()
    )
    if target != "sim":
        found = (flow / "Makefile").is_file()
        checks.append(
            {
                "name": "ORFS",
                "status": "available" if found else "missing",
                "detail": str(flow),
                "action": ""
                if found
                else f"Install ORFS using {ORFS_DOCS}, then run chipflow setup --orfs /path/to/OpenROAD-flow-scripts --save",
                "source": ORFS_DOCS,
            }
        )
        platform_dir = flow / "platforms" / platform
        present = platform in available_platforms
        assets = {
            kind: any(p.is_file() for p in platform_dir.rglob(pattern))
            for kind, pattern in [("Liberty", "*.lib*"), ("LEF", "*.lef*")]
        }
        if target == "finish":
            assets["GDS"] = any(p.is_file() for p in platform_dir.rglob("*.gds*"))
        complete = present and all(assets.values())
        missing = ", ".join(name for name, ok in assets.items() if not ok)
        checks.append(
            {
                "name": f"Platform: {platform}",
                "status": "available" if complete else "missing",
                "detail": str(platform_dir) + (f" (missing {missing})" if missing else ""),
                "action": ""
                if complete
                else "Install the selected ORFS platform data, including cell libraries and physical views. Respect each platform's license.",
                "source": ORFS_DOCS,
            }
        )
    ready = all(c["status"] == "available" for c in checks)
    report = {
        "ready": ready,
        "target": target,
        "platform": platform,
        "host": host,
        "resources": inventory(),
        "bin_dirs": bin_dirs or [],
        "orfs": str(flow),
        "platforms": available_platforms,
        "tools": tools,
        "checks": checks,
        "settings_file": str(settings_path()),
        "verification": "Availability checks do not prove version compatibility. Run chipflow verify --target sim, then chipflow verify --target finish to exercise the installed tools.",
    }
    from .installer import installation_options

    report["installation_options"] = installation_options(report)
    return report


def format_report(report: dict) -> str:
    host = report["host"]
    lines = [
        f"Chipflow setup: {report['target']} / {report['platform']}",
        f"Host: {host['distribution']} {host['version']} ({host['architecture']})",
        "",
    ]
    for check in report["checks"]:
        lines.append(
            f"[{'OK' if check['status'] == 'available' else 'FIX'}] {check['name']}: {check['detail']}"
        )
        if check["action"]:
            lines.append("      " + check["action"])
    lines += [
        "",
        "Ready for verification."
        if report["ready"]
        else "Resolve the items marked FIX, then run chipflow setup again.",
        report["verification"],
    ]
    if not report["ready"]:
        lines.append(
            "Choose and install missing tools: chipflow setup --install --target "
            + report["target"]
            + " --platform "
            + report["platform"]
            + ". List variants without installing: chipflow setup --options."
        )
    return "\n".join(lines)
