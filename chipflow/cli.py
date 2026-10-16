"""Command-line entry points for setup, execution, and local dashboards."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .engine import STAGES, TERMINAL, Store, doctor
from .environment import activate_environment, init_project, write_settings
from .setup import assess, format_report


def wait_for_run(store, run_id):
    offset = 0
    try:
        while True:
            path = store.directory(run_id) / "console.log"
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(offset)
                    sys.stdout.write(stream.read().decode(errors="replace"))
                    sys.stdout.flush()
                    offset = stream.tell()
            state = store.get(run_id)
            if state["status"] in TERMINAL:
                print(json.dumps(state, indent=2))
                return 0 if state["status"] == "completed" else 1
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
    item = commands.add_parser("setup", help="check prerequisites and suggest installation steps")
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
    item = commands.add_parser("init", help="create a runnable counter example in a new directory")
    item.add_argument("directory", type=Path)
    item.add_argument("--orfs")
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
    for name in ["status", "cancel", "artifacts", "logs", "retry"]:
        item = commands.add_parser(name)
        item.add_argument("id")
    item = commands.add_parser("compare")
    item.add_argument("left")
    item.add_argument("right")
    item = commands.add_parser("serve", help="start the local browser dashboard")
    item.add_argument("--port", type=int, default=8080)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "setup":
            saved = None
            if args.save:
                saved = str(write_settings(args.orfs, args.bin_dir))
            activate_environment()
            result = assess(args.orfs, args.target, args.platform, bin_dirs=args.bin_dir)
            if saved:
                result["saved_settings"] = saved
            print(json.dumps(result, indent=2) if args.json else format_report(result))
            return 0 if result["ready"] else 1
        activate_environment()
        if args.command == "init":
            config = init_project(args.directory, args.orfs)
            print(f"Created {config}\nRun: chipflow run {config} --target sim --wait")
            return 0
        if args.command == "doctor":
            print(json.dumps(doctor(args.orfs), indent=2))
            return 0
        if args.command in {None, "dashboard", "tui"}:
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
    except (ValueError, OSError) as exc:
        print(f"chipflow: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
