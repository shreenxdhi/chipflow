"""Guided configuration for a user's own RTL, without editing YAML."""

from pathlib import Path

import yaml

from .config import Project
from .environment import discover_orfs
from .projects import inspect_project


def wizard(destination: Path):
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError(f"Configuration already exists: {destination}. Choose a new filename.")
    print("Create your RTL project. Press Enter to accept a default.")
    print(f"File paths are relative to {destination.parent}.")
    print("For a working example with its own testbench, use chipflow quickstart first-chip.")

    def ask(label, default=""):
        try:
            value = input(label + (f" [{default}]" if default else "") + ": ").strip()
        except EOFError as exc:
            raise ValueError(
                "The wizard needs an interactive terminal. Use chipflow init or a YAML project in scripts."
            ) from exc
        return value or default

    name = ask("Project name", "my_first_chip")
    top = ask("Top hardware module (the module to build)", "counter")
    rtl = [
        path.strip()
        for path in ask("RTL source files, separated by commas").split(",")
        if path.strip()
    ]
    testbench = [
        path.strip()
        for path in ask("Testbench files, separated by commas (Enter to add later)").split(",")
        if path.strip()
    ]
    simulation = None
    if testbench:
        simulation = {
            "top": ask("Testbench top module", "tb"),
            "testbench": testbench,
            "pass_marker": ask("Message printed only when the test passes", "PASS"),
        }
    clock = ask("Clock input port", "clk")
    try:
        period = float(ask("Clock period in ns (10 ns = 100 MHz)", "10"))
    except ValueError as exc:
        raise ValueError("Clock period must be a positive number of nanoseconds") from exc
    platform = ask("Technology platform (Nangate45 is the learning example)", "nangate45")
    project = Project.model_validate(
        {
            "name": name,
            "top": top,
            "rtl": rtl,
            "simulation": simulation,
            "clocks": [{"port": clock, "period_ns": period}],
            "platform": platform,
            "orfs": discover_orfs(),
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    import os

    with open(destination, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        yaml.safe_dump(project.model_dump(exclude_none=True), stream, sort_keys=False)
    target = "sim" if simulation else "synth"
    report = inspect_project(destination, target)
    print(f"\nSaved {destination}")
    if report["issues"]:
        print("Before running:")
        for issue in report["issues"]:
            print("  " + issue)
        print("Run chipflow setup for installation suggestions.")
    print(f"Run: chipflow run {destination} --target {target} --wait")
    print("Customize later: chipflow hooks; chipflow variables --search clock")
    return destination
