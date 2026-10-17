"""Command-line entry points for setup, execution, and local dashboards."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .customization import TCL_POINTS, supported_points, variable_catalog
from .engine import STAGES, TERMINAL, Store, doctor
from .environment import (
    activate_environment,
    discover_orfs,
    flow_path,
    init_project,
    write_settings,
)
from .learning import GLOSSARY, LESSONS, guide_text
from .setup import assess, format_report
from .storage import read_log


def wait_for_run(store, run_id, quiet=False):
    offset = 0
    previous_stage = None
    try:
        while True:
            state = store.get(run_id)
            finished = state["status"] in TERMINAL
            more = False
            progressed = False
            stage = next(
                (item["name"] for item in state["stages"] if item["status"] == "running"), None
            )
            if quiet and stage and stage != previous_stage:
                print(f"Running: {stage}", flush=True)
                previous_stage = stage
            path = store.directory(run_id) / "console.log"
            if path.exists() and not quiet:
                chunk = read_log(path, offset, final=finished)
                sys.stdout.write(chunk["text"])
                sys.stdout.flush()
                progressed = chunk["offset"] != offset
                offset, more = chunk["offset"], chunk["more"]
            if finished and not more:
                if quiet:
                    print(f"{state['name']}: {state['status']}. Run ID: {run_id}")
                    print(f"Saved outputs: {store.directory(run_id)}")
                    if state.get("error"):
                        print(state["error"], file=sys.stderr)
                        print(
                            f"Inspect the error: chipflow --runs {store.root} logs {run_id}",
                            file=sys.stderr,
                        )
                else:
                    print(json.dumps(state, indent=2))
                return 0 if state["status"] == "completed" else 1
            if not more or not progressed:
                time.sleep(0.25)
    except KeyboardInterrupt:
        store.cancel(run_id)
        print("Cancellation requested.", file=sys.stderr)
        return 130


def parser():
    root = argparse.ArgumentParser(prog="chipflow", description="Local RTL-to-GDS with ORFS")
    root.add_argument("--version", action="version", version=f"chipflow {__version__}")
    root.add_argument("--runs", default=".chipflow/runs", help="persistent run directory")
    commands = root.add_subparsers(dest="command")
    item = commands.add_parser("guide", help="learn each stage and common VLSI terms")
    item.add_argument("--json", action="store_true")
    item = commands.add_parser("quickstart", help="create and run your first self-checking counter")
    item.add_argument("directory", nargs="?", type=Path, default=Path("first-chip"))
    item.add_argument("--target", choices=["sim", "finish"], default="sim")
    item.add_argument("--verbose", action="store_true", help="show the complete tool log")
    item = commands.add_parser(
        "wizard", help="create a configuration for your own RTL interactively"
    )
    item.add_argument("config", nargs="?", type=Path, default=Path("chipflow.yaml"))
    item = commands.add_parser("hooks", help="list native Tcl hooks and script overrides")
    item.add_argument("--orfs")
    item.add_argument("--json", action="store_true")
    item = commands.add_parser("variables", help="search the installed ORFS variable reference")
    item.add_argument("--orfs")
    item.add_argument("--search", default="")
    item.add_argument("--json", action="store_true")
    item = commands.add_parser("setup", help="check prerequisites and choose tool installers")
    item.add_argument("--orfs", help="ORFS repository or flow directory")
    item.add_argument(
        "--bin-dir", action="append", default=None, help="additional tool directory (repeatable)"
    )
    item.add_argument(
        "--save", action="store_true", help="remember the provided ORFS/tool directories"
    )
    item.add_argument("--target", choices=STAGES, default="finish")
    item.add_argument("--platform", default="nangate45")
    item.add_argument("--json", action="store_true", help="machine-readable assessment")
    item.add_argument("--check", action="store_true", help="check only; never prompt")
    item.add_argument(
        "--options", action="store_true", help="list installation variants without installing"
    )
    item.add_argument(
        "--install", action="store_true", help="choose tools and approve installation commands"
    )
    item.add_argument("--option", help="preselect an installation option from --options")
    item.add_argument(
        "--install-dir",
        type=Path,
        help="dedicated directory for an ORFS checkout or prebuilt suite",
    )
    item.add_argument("--package", type=Path, help="local upstream .deb/.rpm package to install")
    item.add_argument(
        "--dry-run",
        action="store_true",
        help="print a selected installation plan without running it",
    )
    item = commands.add_parser("init", help="create a runnable counter example in a new directory")
    item.add_argument("directory", type=Path)
    item.add_argument("--orfs")
    item.add_argument(
        "--expert", action="store_true", help="enable example Tcl hooks and ORFS settings"
    )
    item = commands.add_parser(
        "verify", help="exercise the configured tools with the bundled example"
    )
    item.add_argument("--target", choices=["sim", "finish"], default="sim")
    item = commands.add_parser(
        "dashboard", aliases=["tui"], help="interactive terminal dashboard (default)"
    )
    item.add_argument("--run", help="run ID or unique prefix to watch")
    item.add_argument(
        "--view", choices=["overview", "logs", "files", "metrics"], default="overview"
    )
    item.add_argument(
        "--refresh", type=float, default=1.0, help="refresh interval in seconds (0.2 to 60)"
    )
    item = commands.add_parser(
        "doctor", help="inspect native tool versions and ORFS revision as JSON"
    )
    item.add_argument("--orfs")
    item = commands.add_parser("run", help="launch a project")
    item.add_argument("config", type=Path)
    item.add_argument("--target", choices=STAGES, default="sim")
    item.add_argument("--wait", action="store_true")
    commands.add_parser("history", help="list saved runs as JSON")
    item = commands.add_parser("backup", help="back up completed run history and saved inputs")
    item.add_argument("archive", type=Path)
    item = commands.add_parser(
        "restore", help="restore a verified backup into an empty run directory"
    )
    item.add_argument("archive", type=Path)
    for name in ["status", "cancel", "artifacts", "logs", "retry"]:
        item = commands.add_parser(name)
        item.add_argument("id")
    item = commands.add_parser("compare")
    item.add_argument("left")
    item.add_argument("right")
    item = commands.add_parser("serve", help="start the local browser dashboard")
    item.add_argument("--port", type=int, choices=range(1, 65536), metavar="PORT", default=8080)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "guide":
            print(
                json.dumps({"lessons": LESSONS, "glossary": GLOSSARY}, indent=2)
                if args.json
                else guide_text()
            )
            return 0
        if args.command == "hooks":
            available = supported_points(flow_path(args.orfs or discover_orfs()))
            data = {
                name: {**info, "available": name in available} for name, info in TCL_POINTS.items()
            }
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                for name, info in data.items():
                    print(
                        f"{name}: {info['description']} ({'available' if info['available'] else 'not found in installed scripts'})"
                    )
                print("Configure paths under tcl.directory; list relative scripts in tcl.hooks.")
            return 0
        if args.command == "variables":
            query = args.search.lower()
            data = {
                key: value
                for key, value in variable_catalog(args.orfs).items()
                if query in (key + " " + value["description"]).lower()
            }
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                for key, value in sorted(data.items()):
                    print(
                        f"{key}{' [managed by Chipflow]' if value['managed'] else ''}: {value['description']}"
                    )
                if not data:
                    print("No matching settings. Check --orfs and the search term.")
            return 0
        if args.command == "setup":
            from .installer import ask, interactive_setup

            if (
                args.option or args.dry_run or args.install_dir or args.package
            ) and not args.install:
                raise ValueError(
                    "Use --install with --option, --dry-run, --install-dir or --package."
                )
            if args.install and (args.json or args.check or args.options):
                raise ValueError("Use --install separately from --json, --check and --options.")
            if args.save and args.dry_run:
                raise ValueError("--dry-run cannot be combined with --save.")
            saved = None
            if args.save:
                saved = str(write_settings(args.orfs, args.bin_dir))
            activate_environment()
            result = assess(args.orfs, args.target, args.platform, bin_dirs=args.bin_dir)
            if saved:
                result["saved_settings"] = saved
            print(json.dumps(result, indent=2) if args.json else format_report(result))
            if args.options and not args.json:
                for option in result["installation_options"]:
                    print("\n" + option["id"] + ": " + option["label"])
                    print(option["description"])
                    print(option["command"] if option["available"] else option["reason"])
                    print("Source: " + option["source"])
            interactive = sys.stdin.isatty() and sys.stdout.isatty()
            install = args.install
            if (
                not result["ready"]
                and interactive
                and not (args.json or args.check or args.options or install)
            ):
                install = ask("Review installation choices for missing tools? [y/N]: ").lower() in {
                    "y",
                    "yes",
                }
            if install:
                result = interactive_setup(
                    result,
                    option_id=args.option,
                    directory=args.install_dir,
                    package=args.package,
                    dry_run=args.dry_run,
                )
                if args.dry_run:
                    return 0
            return 0 if result["ready"] else 1
        activate_environment()
        if args.command == "wizard":
            from .onboarding import wizard

            wizard(args.config)
            return 0
        if args.command == "init":
            config = init_project(args.directory, args.orfs, expert=args.expert)
            print(f"Created {config}\nRun: chipflow run {config} --target sim --wait")
            return 0
        if args.command == "doctor":
            print(json.dumps(doctor(args.orfs), indent=2))
            return 0
        if args.command in {None, "dashboard", "tui"}:
            if args.command is None and sys.stdin.isatty() and sys.stdout.isatty():
                report = assess(versions=False)
                if not report["ready"]:
                    from .installer import ask, interactive_setup

                    print(format_report(report))
                    if ask(
                        "Review installation choices before opening the dashboard? [y/N]: "
                    ).lower() in {"y", "yes"}:
                        interactive_setup(report)
            from .tui import launch

            return launch(
                args.runs,
                getattr(args, "run", None),
                getattr(args, "view", "overview"),
                getattr(args, "refresh", 1.0),
            )
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(args.runs), host="127.0.0.1", port=args.port)
            return 0
        store = Store(args.runs)
        if args.command == "quickstart":
            for target in ["sim", "finish"] if args.target == "finish" else ["sim"]:
                report = assess(target=target, versions=False)
                if not report["ready"]:
                    print(format_report(report))
                    from .installer import ask, interactive_setup

                    if (
                        sys.stdin.isatty()
                        and sys.stdout.isatty()
                        and ask("Review installation choices for missing tools? [y/N]: ").lower()
                        in {"y", "yes"}
                    ):
                        report = interactive_setup(report)
                    if not report["ready"]:
                        return 1
            config = init_project(args.directory)
            print(
                f"Created {config.parent}\ncounter.v is the hardware; tb.v checks its behavior. Read GUIDE.md while learning."
            )
            for target in ["sim", "finish"] if args.target == "finish" else ["sim"]:
                print(f"Starting {target}; your tools will run locally.", flush=True)
                result = store.start(config, target)
                code = wait_for_run(store, result["id"], quiet=not args.verbose)
                if code:
                    return code
            print(
                f"\nNext: chipflow run {config} --target synth --wait\nLearn each stage: chipflow guide\nView results: chipflow --runs {store.root}"
            )
            return 0
        if args.command in {"backup", "restore"}:
            from .backup import backup, restore

            result = (backup if args.command == "backup" else restore)(store, args.archive)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "verify":
            destination = store.root.parent / "verification" / uuid.uuid4().hex
            config = init_project(destination)
            result = store.start(config, args.target)
            print(json.dumps(result, indent=2), flush=True)
            return wait_for_run(store, result["id"])
        if args.command == "run":
            result = store.start(args.config, args.target)
            print(json.dumps(result, indent=2), flush=True)
            return wait_for_run(store, result["id"]) if args.wait else 0
        if args.command == "history":
            result = store.history()
        elif args.command == "status":
            result = store.get(args.id)
        elif args.command == "cancel":
            result = store.cancel(args.id)
        elif args.command == "retry":
            result = store.retry(args.id)
        elif args.command == "artifacts":
            result = store.artifacts(args.id)
        elif args.command == "logs":
            path = store.directory(args.id) / "console.log"
            print(path.read_text(errors="replace") if path.exists() else "")
            return 0
        elif args.command == "compare":
            result = store.compare(args.left, args.right)
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print(
            "\nStopped. Completed installation steps are kept; rerun chipflow setup to recheck.",
            file=sys.stderr,
        )
        return 130
    except (ValueError, OSError) as exc:
        print(f"chipflow: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
