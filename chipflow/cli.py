"""Command-line entry points for setup, execution, and local dashboards."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .config import load_project
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


def wait_for_run(store, run_id, quiet=False, require_quality=False, json_only=False):
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
            if quiet and not json_only and stage and stage != previous_stage:
                print(f"Running: {stage}", flush=True)
                previous_stage = stage
            path = store.directory(run_id) / "console.log"
            if path.exists() and not quiet and not json_only:
                chunk = read_log(path, offset, final=finished)
                sys.stdout.write(chunk["text"])
                sys.stdout.flush()
                progressed = chunk["offset"] != offset
                offset, more = chunk["offset"], chunk["more"]
            if finished and not more:
                if json_only:
                    print(json.dumps(state, indent=2))
                elif quiet:
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
                if state["status"] != "completed":
                    return 1
                if require_quality and state.get("quality", {}).get("status") != "passed":
                    return 2
                if (
                    state.get("target") == "equivalence"
                    and state.get("verification", {}).get("equivalence", {}).get("status")
                    != "proven"
                ):
                    return 2
                return 0
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
    item.add_argument(
        "--target",
        choices=[s for s in STAGES if s not in {"lint", "equivalence"}],
        default="finish",
    )
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
    item.add_argument(
        "--require-quality", action="store_true", help="exit 2 unless declared quality gates pass"
    )
    item.add_argument(
        "--json", action="store_true", help="emit only final machine-readable state with --wait"
    )
    for name in ["lint", "sim", "equivalence"]:
        item = commands.add_parser(name, help=f"run supervised {name} checks on a project")
        item.add_argument("config", type=Path)
        item.add_argument("--wait", action="store_true")
        item.add_argument("--require-quality", action="store_true")
        item.add_argument("--json", action="store_true")
    item = commands.add_parser(
        "import", help="discover RTL sources; confirm ordering and top before creating a project"
    )
    item.add_argument("directory", type=Path)
    for name in ["quality", "timing", "diagnostics", "environment"]:
        item = commands.add_parser(name, help=f"show saved {name} evidence")
        item.add_argument("id")
    item = commands.add_parser(
        "constraints", help="inventory project SDC; static review is not endpoint coverage"
    )
    item.add_argument("config", type=Path)
    item = commands.add_parser(
        "sta", help="run explicit native timing analysis into a new evidence directory"
    )
    item.add_argument("spec", type=Path)
    item.add_argument("--output", type=Path, required=True)
    item.add_argument("--executable", default="openroad")
    item = commands.add_parser("sdc", help="write guided SDC from explicit JSON timing assumptions")
    item.add_argument("spec", type=Path)
    item.add_argument("--output", type=Path, required=True)
    item = commands.add_parser("report", help="export traceable engineering results")
    item.add_argument("id")
    item.add_argument("--format", choices=["json", "markdown", "html"], default="markdown")
    item.add_argument("--output", type=Path)
    item = commands.add_parser(
        "experiments", help="create and run bounded local parameter experiments"
    )
    experiments = item.add_subparsers(dest="experiment_command", required=True)
    experiments.add_parser("list")
    item = experiments.add_parser("create")
    item.add_argument("config", type=Path)
    item.add_argument(
        "spec",
        type=Path,
        help="JSON name/hypothesis/parameters/objectives/constraints/required_checks/target",
    )
    for name in ["show", "run", "stop", "analyze"]:
        item = experiments.add_parser(name)
        item.add_argument("id")
        if name == "run":
            item.add_argument("--resume", action="store_true")
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
        if args.command == "import":
            from .importing import discover_sources

            print(json.dumps(discover_sources(args.directory), indent=2))
            return 0
        if args.command == "sta":
            from .runner import execute_native_plan
            from .storage import save
            from .timing import sta_plan, sta_result

            if args.spec.stat().st_size > 1024 * 1024:
                raise ValueError("Timing specification exceeds 1 MiB")
            args.output.mkdir(parents=True, exist_ok=False)
            plan = sta_plan(
                json.loads(args.spec.read_text()),
                args.output.resolve(),
                base=args.spec.resolve().parent,
                executable=args.executable,
            )
            code = execute_native_plan(plan, args.output / "console.log")
            result = sta_result(args.output.resolve(), code)
            save(args.output / "timing-result.json", result)
            print(json.dumps(result, indent=2))
            if code:
                return 1
            return (
                0
                if result.get("timing_status") == "passed"
                and result.get("constraint_status") == "passed"
                else 2
            )
        if args.command == "sdc":
            from .constraints import write_guided_sdc

            if args.spec.stat().st_size > 1024 * 1024:
                raise ValueError("Guided SDC specification exceeds 1 MiB")
            print(
                json.dumps(
                    write_guided_sdc(json.loads(args.spec.read_text()), args.output), indent=2
                )
            )
            return 0
        if args.command == "constraints":
            from .constraints import analyze_constraints, inspect_constraints

            project, base = load_project(args.config)
            result = (
                inspect_constraints(base / project.sdc)
                if project.sdc
                else analyze_constraints(
                    "".join(
                        f"create_clock -name {c.port} -period {c.period_ns} [get_ports {{{c.port}}}]\n"
                        for c in project.clocks
                    ),
                    generated=True,
                )
            )
            print(json.dumps(result, indent=2))
            return 0
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
        if args.command in {"quality", "timing", "diagnostics", "environment", "report"}:
            state = store.get(args.id)
            directory = store.directory(args.id)
            if args.command == "quality":
                result = state.get(
                    "quality",
                    {
                        "status": "unknown",
                        "reason": "Historical run has no recorded quality policy",
                    },
                )
            elif args.command == "timing":
                result = {
                    "checks": state["checks"],
                    "metrics": state.get("metrics", {}),
                    "provenance": state.get("metric_provenance", {}),
                    "quality": state.get("quality"),
                    "scope": "Recorded native report metrics; not full signoff or constraint coverage",
                }
            elif args.command == "environment":
                path = directory / "environment-manifest.json"
                result = (
                    json.loads(path.read_text())
                    if path.exists()
                    else {"status": "unknown", "reason": "Historical environment manifest missing"}
                )
            elif args.command == "diagnostics":
                from .diagnostics import diagnose_log

                result = diagnose_log(store.artifact(args.id, "console.log"))
            else:
                from .reports import build_report, render_report

                output = render_report(build_report(store, args.id), args.format)
                if args.output:
                    with args.output.open("x") as stream:
                        stream.write(output)
                else:
                    print(output)
                return 0
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "experiments":
            from .experiments import ExperimentStore

            experiments = ExperimentStore(store)
            if args.experiment_command == "list":
                result = experiments.list()
            elif args.experiment_command == "create":
                if args.spec.stat().st_size > 1024 * 1024:
                    raise ValueError("Experiment specification exceeds 1 MiB")
                spec = json.loads(args.spec.read_text())
                result = experiments.create(args.config, **spec)
            elif args.experiment_command == "run":
                result = experiments.run(args.id, resume=args.resume)
            elif args.experiment_command == "stop":
                result = experiments.request_stop(args.id)
            elif args.experiment_command == "analyze":
                result = experiments.analyze(args.id)
            else:
                result = experiments.get(args.id)
            print(json.dumps(result, indent=2))
            return int(args.experiment_command == "run" and result.get("status") != "completed")
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
        if args.command in {"run", "lint", "sim", "equivalence"}:
            result = store.start(
                args.config, args.target if args.command == "run" else args.command
            )
            if not (args.wait and args.json):
                print(json.dumps(result, indent=2), flush=True)
            return (
                wait_for_run(
                    store, result["id"], require_quality=args.require_quality, json_only=args.json
                )
                if args.wait
                else 0
            )
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
