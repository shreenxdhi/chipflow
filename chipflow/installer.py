"""Curated, reviewable tool installation plans executed only in a terminal."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .config import safe_make_path
from .environment import activate_environment, orfs_bin_dirs, read_settings, write_settings

ORFS_REVISION = "1ff305ccbe694958d0125f0695dceefb58dfb3f6"
ORFS_REPOSITORY = "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git"
ORFS_SOURCE_DOCS = (
    "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/"
    + ORFS_REVISION
    + "/docs/user/BuildLocally.md"
)
PACKAGES = {
    "iverilog": ["iverilog", "vvp"],
    "make": ["make"],
    "klayout": ["klayout"],
    "git": ["git"],
}
MANAGERS = {
    "ubuntu": "apt-get",
    "debian": "apt-get",
    "linuxmint": "apt-get",
    "pop": "apt-get",
    "fedora": "dnf",
    "rhel": "dnf",
    "rocky": "dnf",
    "almalinux": "dnf",
    "arch": "pacman",
    "manjaro": "pacman",
    "opensuse-leap": "zypper",
    "opensuse-tumbleweed": "zypper",
}


def install_root() -> Path:
    return (
        (
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            / "chipflow"
            / "toolchains"
            / ("orfs-" + ORFS_REVISION[:12])
        )
        .expanduser()
        .resolve()
    )


def installation_options(report: dict) -> list[dict]:
    """Return display data only. Discovery never installs software or writes files."""
    missing = {check["name"] for check in report["checks"] if check["status"] != "available"}
    if report["target"] != "sim":
        missing.update(
            name
            for name, tool in report["tools"].items()
            if not tool["path"] or tool["probe_error"]
        )
        if not shutil.which("git"):
            missing.add("git")
    host = report["host"]
    linux = host["system"] == "Linux"
    manager = MANAGERS.get(host["distribution"])
    admin = os.geteuid() == 0 or bool(shutil.which("sudo")) if linux else False
    options = []

    def add(key, label, provides, description, source, available=True, reason=""):
        options.append(
            {
                "id": key,
                "label": label,
                "provides": provides,
                "description": description,
                "source": source,
                "available": available,
                "reason": "" if available else reason,
                "command": f"chipflow setup --target {report['target']} --platform "
                + shlex.quote(report["platform"])
                + " --install --option "
                + key,
            }
        )

    if not linux:
        add(
            "linux-guide",
            "Use Linux, Windows WSL2, or a Linux virtual machine",
            ["Linux runtime"],
            "Windows users can install Ubuntu in WSL2; macOS users can use a Linux VM. "
            "Run Chipflow and install the EDA tools inside Linux. Native macOS/Windows "
            "execution is not validated.",
            "https://learn.microsoft.com/windows/wsl/install",
            False,
            "Open the guide, then run chipflow setup inside Linux.",
        )
    for package, provides in PACKAGES.items():
        if not missing.intersection(provides):
            continue
        if manager:
            add(
                "system-" + package,
                f"{package} — distribution package ({manager})",
                provides,
                "Uses your configured distribution repositories. Administrator access may be "
                "required; package versions and availability depend on your distribution.",
                report["tools"].get(provides[0], {}).get("source", ORFS_SOURCE_DOCS),
                linux and bool(shutil.which(manager)) and admin,
                "Requires Linux, administrator access (root or sudo), and an installed "
                + manager
                + " package manager.",
            )
        add(
            "brew-" + package,
            f"{package} — Homebrew package",
            provides,
            "Uses an existing Linux Homebrew installation without sudo. Homebrew cannot "
            "run as root. Executable directories are saved for future runs.",
            "https://brew.sh/",
            linux and bool(shutil.which("brew")) and os.geteuid() != 0,
            "Requires Linux, an existing Homebrew installation, and a non-root user.",
        )
    if missing.intersection({"iverilog", "vvp", "yosys"}):
        add(
            "oss-cad-suite",
            "Icarus + Yosys — prebuilt OSS CAD Suite (no sudo)",
            ["iverilog", "vvp", "yosys"],
            "Downloads a dated upstream Linux x86-64 release into your user directory. "
            "Shows the release, download size and SHA-256 before approval; verifies the "
            "digest before extraction. The download is large (roughly 750 MB). This does "
            "not include OpenROAD or ORFS. Verify compatibility with your physical flow.",
            "https://github.com/YosysHQ/oss-cad-suite-build/releases",
            linux
            and host["architecture"] in {"x86_64", "amd64"}
            and hasattr(tarfile, "data_filter"),
            "Automated prebuilt installation requires Linux x86-64 and an updated Python "
            "with tarfile.data_filter. Other architectures: use distribution packages or existing tools.",
        )
    physical = missing.intersection({"ORFS", "yosys", "openroad", "klayout"}) or any(
        name.startswith("Platform:") for name in missing
    )
    if physical and report["target"] != "sim":
        add(
            "orfs-checkout",
            "ORFS flow and bundled platform data only",
            ["ORFS", "bundled platforms"],
            "Downloads the pinned flow and its submodules into a dedicated directory. "
            "Choose this if you already have compatible Yosys, OpenROAD and KLayout binaries. "
            "Some technologies require additional separately licensed data.",
            ORFS_SOURCE_DOCS,
            linux and bool(shutil.which("git")),
            "Requires Linux and git. Install git with your distribution package manager first.",
        )
        supported_source = (
            host["distribution"] == "ubuntu" and host["version"] in {"20.04", "22.04", "24.04"}
        ) or (
            host["distribution"] in {"rhel", "rocky", "almalinux"}
            and host["version"].split(".")[0] in {"8", "9"}
        )
        add(
            "orfs-source",
            "ORFS toolchain — pinned native source build",
            ["ORFS", "yosys", "openroad", "klayout", "bundled platforms"],
            "Builds Yosys and OpenROAD from ORFS revision " + ORFS_REVISION + ". "
            "The upstream setup script installs system build dependencies and KLayout, "
            "may add repositories or replace packages, and runs with sudo. "
            "Allow several GB of disk, substantial memory and a long build. "
            "Icarus Verilog is a separate choice. Use the upstream guide to review dependencies.",
            ORFS_SOURCE_DOCS,
            linux
            and supported_source
            and host["architecture"] in {"x86_64", "aarch64"}
            and bool(shutil.which("git"))
            and bool(shutil.which("sudo")),
            "Automated source setup requires git, sudo and a supported 64-bit Ubuntu "
            "20.04/22.04/24.04 or Enterprise Linux 8/9 host. Other hosts: use prebuilt "
            "binaries, an existing installation or the upstream build guide.",
        )
        for tool in ["openroad", "klayout"]:
            if tool not in missing:
                continue
            add(
                "prebuilt-" + tool,
                f"{tool} — local upstream .deb/.rpm package",
                [tool],
                "Download a package matching your OS and CPU from the linked upstream "
                "release page, then select its local path. Your native package manager "
                "installs it and resolves dependencies. Verify the upstream checksum before "
                "installation. Individually chosen versions still need flow verification.",
                "https://github.com/Precision-Innovations/OpenROAD/releases"
                if tool == "openroad"
                else "https://www.klayout.de/build.html",
                linux and manager in {"apt-get", "dnf"} and bool(shutil.which(manager)) and admin,
                "Local package installation requires administrator access and apt-get (.deb) or dnf (.rpm).",
            )
    add(
        "existing",
        "Use existing tools / custom installation paths",
        ["saved tool paths", "optional ORFS path"],
        "Keep tools you installed yourself, including prebuilt suites or a site-managed "
        "toolchain. Save their executable directories and optionally an ORFS checkout. "
        "No administrator access or download is needed.",
        "https://github.com/shreenxdhi/chipflow/blob/main/docs/INSTALLATION.md",
        linux,
        "Run Chipflow inside Linux first.",
    )
    return options


def privileged(argv: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return argv
    if not shutil.which("sudo"):
        raise ValueError(
            "sudo is unavailable. Ask your administrator or use existing tools/Homebrew."
        )
    return ["sudo", "--", *argv]


def probe(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Cannot inspect installation: {exc}") from exc


def build_plan(report: dict, option_id: str, directory=None, package=None) -> dict:
    """Construct an argv-only plan from a known choice; never accept shell commands."""
    options = {item["id"]: item for item in installation_options(report)}
    option = options.get(option_id)
    if option is None:
        raise ValueError("Unknown installation option. Run chipflow setup --options.")
    if not option["available"]:
        raise ValueError(option["reason"])
    plan = {"option": option, "steps": [], "orfs": None, "bin_dirs": []}

    def step(argv, cwd=None):
        plan["steps"].append({"argv": argv, "cwd": str(cwd) if cwd else None})

    if option_id == "oss-cad-suite":
        release = suite_release()
        root = (
            Path(directory or install_root().parent / ("oss-cad-suite-" + release["version"]))
            .expanduser()
            .resolve()
        )
        if root.exists():
            raise ValueError(
                "Installation directory already exists. Choose another --install-dir or use existing tools."
            )
        plan["download"] = {**release, "directory": str(root)}
        plan["bin_dirs"] = [str(root / "bin")]
    elif option_id.startswith("system-"):
        tool = option_id.removeprefix("system-")
        manager = MANAGERS[report["host"]["distribution"]]
        if manager == "apt-get":
            step(privileged([manager, "update"]))
        flags = ["-S", "--needed"] if manager == "pacman" else ["install"]
        step(privileged([manager, *flags, tool]))
    elif option_id.startswith("brew-"):
        tool = option_id.removeprefix("brew-")
        step(["brew", "install", tool])
        if tool == "make":
            plan["brew_make"] = True
    elif option_id.startswith("orfs-"):
        root = Path(directory or install_root()).expanduser().resolve()
        safe_make_path(root)
        plan["orfs"] = str(root)
        plan["checkout"] = str(root)
        if root.exists():
            # Never modify an unrelated checkout, switch branches, or discard local edits.
            if not (root / ".git").is_dir():
                raise ValueError(f"Destination already exists and is not an ORFS checkout: {root}")
            head = probe(["git", "-C", str(root), "rev-parse", "HEAD"])
            if head != ORFS_REVISION:
                raise ValueError(
                    "Existing checkout has another revision. Choose a new --install-dir."
                )
            step(["git", "diff", "--exit-code", "HEAD", "--"], root)
        else:
            step(["mkdir", "-p", "--", str(root.parent)])
            step(["git", "clone", "--no-checkout", "--", ORFS_REPOSITORY, str(root)])
            step(["git", "checkout", "--detach", ORFS_REVISION], root)
        step(["git", "submodule", "update", "--init", "--recursive"], root)
        step(["git", "submodule", "foreach", "--recursive", "git diff --exit-code HEAD --"], root)
        if option_id == "orfs-source":
            plan["bin_dirs"] = orfs_bin_dirs(root)[:2]
            step(["sudo", "--", "./setup.sh"], root)
            step(
                ["./build_openroad.sh", "--local", "--threads", str(min(os.cpu_count() or 1, 4))],
                root,
            )
    elif option_id.startswith("prebuilt-"):
        if not package:
            raise ValueError(
                "Select the downloaded package with --package /path/to/package.deb (or .rpm)."
            )
        path = Path(package).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Package file not found: {path}")
        manager = MANAGERS[report["host"]["distribution"]]
        suffix = ".deb" if manager == "apt-get" else ".rpm"
        if path.suffix != suffix:
            raise ValueError(f"This host requires a {suffix} package.")
        package_probe = (
            ["dpkg-deb", "-f", str(path), "Package"]
            if suffix == ".deb"
            else ["rpm", "-qp", "--queryformat", "%{NAME}", str(path)]
        )
        name = probe(package_probe).lower()
        tool = option_id.removeprefix("prebuilt-")
        if name != tool:
            raise ValueError(f"Selected package is {name!r}; expected {tool!r}.")
        step(privileged([manager, "install", str(path)]))
    return plan


def format_plan(plan: dict) -> str:
    lines = [
        plan["option"]["label"],
        plan["option"]["description"],
        "Source: " + plan["option"]["source"],
    ]
    if plan.get("download"):
        download = plan["download"]
        lines.extend(
            [
                "Release: " + download["version"],
                f"Download: {download['url']} ({download['size'] / 1024**2:.0f} MiB)",
                "SHA-256: " + download["sha256"],
                "Verify, extract into " + download["directory"] + " and save its bin directory.",
            ]
        )
    for item in plan["steps"]:
        prefix = "(in " + item["cwd"] + ") " if item["cwd"] else ""
        lines.append("  " + prefix + shlex.join(item["argv"]))
    if plan["orfs"]:
        lines.append("Save ORFS path after success: " + plan["orfs"])
    if plan["bin_dirs"]:
        lines.append(
            "Prefer these executable directories after success: " + ", ".join(plan["bin_dirs"])
        )
    return "\n".join(lines)


def execute_plan(plan: dict) -> None:
    """Call only after the UI has obtained approval for format_plan(plan)."""
    if plan.get("download"):
        install_suite(plan["download"])
    for item in plan["steps"]:
        print("\n$ " + shlex.join(item["argv"]), flush=True)
        try:
            subprocess.run(item["argv"], cwd=item["cwd"], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError(
                f"Installation stopped: {exc}. Earlier steps may have installed packages or "
                "downloaded files; nothing was removed. Fix the error and rerun setup. "
                "A pinned ORFS checkout can be reused with the same --install-dir."
            ) from exc
    bins = list(plan["bin_dirs"])
    if plan.get("brew_make"):
        prefix = probe(["brew", "--prefix", "make"])
        bins.append(str(Path(prefix) / "libexec" / "gnubin"))
    if plan["orfs"] or bins:
        write_settings(
            plan["orfs"], list(dict.fromkeys([*bins, *read_settings().get("bin_dirs", [])]))
        )
        if plan["orfs"] and os.environ.get("ORFS_HOME"):
            print(
                "ORFS_HOME overrides the saved ORFS path. Update or unset it to use " + plan["orfs"]
            )
    activate_environment()


def suite_release() -> dict:
    """Resolve latest to a dated release and an upstream-published digest before approval."""
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "chipflow-setup"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.loads(response.read(2 * 1024 * 1024))
        version = release["tag_name"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
            raise ValueError("Expected a dated OSS CAD Suite release")
        name = "oss-cad-suite-linux-x64-" + version.replace("-", "") + ".tgz"
        asset = next(a for a in release["assets"] if a["name"] == name)
        digest = asset.get("digest") or ""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Upstream did not provide a SHA-256 digest; choose another installer")
        url = (
            "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/"
            + version
            + "/"
            + name
        )
        if asset["browser_download_url"] != url or not 0 < asset["size"] <= 2 * 1024**3:
            raise ValueError("Unexpected OSS CAD Suite download metadata")
        return {"version": version, "url": url, "sha256": digest[7:], "size": asset["size"]}
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise ValueError(
            f"Cannot resolve OSS CAD Suite release: {exc}. Try distribution packages or existing tools."
        ) from exc


def install_suite(download: dict) -> None:
    root = Path(download["directory"])
    if root.exists():
        raise ValueError(
            "Installation directory appeared after planning; choose another directory."
        )
    root.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading OSS CAD Suite " + download["version"] + "…", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".chipflow-install-", dir=root.parent) as temporary:
            archive_path = Path(temporary) / "suite.tgz"
            digest = hashlib.sha256()
            size = 0
            with (
                urllib.request.urlopen(download["url"], timeout=30) as response,
                archive_path.open("xb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > download["size"]:
                        raise ValueError("Download exceeds the approved size")
                    output.write(chunk)
                    digest.update(chunk)
                    if size // (64 * 1024**2) != (size - len(chunk)) // (64 * 1024**2):
                        print(
                            f"Downloaded {size // 1024**2} / {download['size'] // 1024**2} MiB",
                            flush=True,
                        )
            if size != download["size"] or digest.hexdigest() != download["sha256"]:
                raise ValueError("Downloaded archive does not match the approved size and SHA-256")
            unpacked = Path(temporary) / "unpacked"
            with tarfile.open(archive_path, "r:gz") as archive:
                members = archive.getmembers()
                if len(members) > 200000 or sum(m.size for m in members) > 8 * 1024**3:
                    raise ValueError("Archive exceeds extraction limits")
                if any(m.name.split("/")[0] != "oss-cad-suite" for m in members):
                    raise ValueError("Archive has an unexpected root directory")
                archive.extractall(unpacked, filter="data")
            suite = unpacked / "oss-cad-suite"
            for tool in ["iverilog", "vvp", "yosys"]:
                if not (suite / "bin" / tool).is_file():
                    raise ValueError("Archive does not contain the expected tool: " + tool)
            if root.exists():
                raise ValueError("Installation directory appeared during download")
            suite.rename(root)
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise ValueError(
            f"OSS CAD Suite installation failed: {exc}. No tool paths were saved."
        ) from exc


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def interactive_setup(
    report: dict, *, option_id=None, directory=None, package=None, dry_run=False
) -> dict:
    """Choose one or more installers and reassess after each approved operation."""
    from .setup import assess, format_report

    if dry_run:
        if not option_id:
            raise ValueError("Use --option with --dry-run; list choices with --options.")
        print(format_plan(build_plan(report, option_id, directory, package)))
        return report
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError(
            "Installation needs an interactive terminal. Use --options or --dry-run to plan in scripts."
        )
    active_orfs = report["orfs"]
    active_bins = report.get("bin_dirs", [])
    while True:
        options = installation_options(report)
        if not option_id:
            print("\nChoose an installation option. You can return to install another tool.")
            for index, option in enumerate(options, 1):
                print(
                    f"  {index}. {option['label']}"
                    + (" (guide only / unavailable)" if not option["available"] else "")
                )
                print("     " + option["description"])
                if not option["available"]:
                    print("     " + option["reason"] + " " + option["source"])
            choice = ask("Option number (Enter to finish): ")
            if not choice:
                return report
            if not choice.isdecimal() or not 1 <= int(choice) <= len(options):
                print("Choose a number from the list.")
                continue
            option_id = options[int(choice) - 1]["id"]
        current = next((item for item in options if item["id"] == option_id), None)
        if current is None or not current["available"]:
            print(
                current["reason"] + " " + current["source"]
                if current
                else "Unknown installation option."
            )
            option_id = None
            continue
        if option_id == "existing":
            root = ask("ORFS checkout path (Enter to keep current): ")
            paths = ask("Executable directories, separated by commas (Enter to keep current): ")
            bins = [p.strip() for p in paths.split(",") if p.strip()]
            if root or bins:
                print("ORFS: " + (root or report["orfs"]))
                print("Additional executable directories: " + ", ".join(bins))
                if ask("Save these paths? [y/N]: ").lower() in {"y", "yes"}:
                    write_settings(
                        root or None,
                        [*bins, *read_settings().get("bin_dirs", [])] if bins else None,
                    )
                    activate_environment()
                    if root:
                        active_orfs = root
        else:
            if option_id.startswith("orfs-") and not directory:
                directory = ask(f"Installation directory [{install_root()}]: ") or str(
                    install_root()
                )
            if option_id.startswith("prebuilt-") and not package:
                package = ask("Local package path: ")
            plan = build_plan(report, option_id, directory, package)
            print("\n" + format_plan(plan))
            if ask("Run these installation commands? [y/N]: ").lower() in {"y", "yes"}:
                execute_plan(plan)
                if plan["orfs"]:
                    active_orfs = os.environ.get("ORFS_HOME") or plan["orfs"]
        # A newly saved ORFS installation supersedes the previous assessment path.
        report = assess(
            orfs=active_orfs,
            target=report["target"],
            platform=report["platform"],
            bin_dirs=active_bins,
        )
        print("\n" + format_report(report))
        if report["ready"]:
            return report
        option_id, directory, package = None, None, None
