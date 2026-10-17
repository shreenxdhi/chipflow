#!/usr/bin/env python3
"""Measure manager overhead with explicit synthetic history/log application fixtures.

No EDA tool is invoked and no fixture is represented as engineering validation.
API timings use in-process TestClient, excluding network/browser latency.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def timing(operation, repetitions):
    samples = []
    for _ in range(repetitions):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "samples": len(samples),
        "median_ms": statistics.median(samples),
        "p95_ms": sorted(samples)[max(0, math.ceil(len(samples) * 0.95) - 1)],
        "max_ms": max(samples),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1000, help="synthetic terminal history records")
    parser.add_argument(
        "--log-mib", type=int, default=8, help="synthetic log bytes per selected run"
    )
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.runs <= 10000 or not 1 <= args.samples <= 100 or not 0 <= args.log_mib <= 256:
        parser.error("budgets: runs 1–10000, samples 1–100, log-mib 0–256")
    # Make direct invocation from a source checkout work without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from fastapi.testclient import TestClient

    from chipflow.api import create_app
    from chipflow.resources import inventory
    from chipflow.storage import save

    environment = inventory()
    with tempfile.TemporaryDirectory(prefix="chipflow-manager-benchmark-") as temporary:
        root = Path(temporary) / "runs"
        root.mkdir(mode=0o700)
        for index in range(1, args.runs + 1):
            run_id = f"{index:032x}"
            directory = root / run_id
            directory.mkdir(mode=0o700)
            save(
                directory / "run.json",
                {
                    "schema_version": 1,
                    "id": run_id,
                    "name": f"benchmark-fixture-{index}",
                    "target": "sim",
                    "status": "failed",
                    "created": float(index),
                    "stages": [],
                    "checks": {},
                    "metrics": {},
                    "error": "Synthetic application benchmark fixture; no EDA execution.",
                },
            )
        selected = root / f"{1:032x}" / "console.log"
        with selected.open("wb") as stream:
            block = b"Application benchmark log fixture; no tool result.\n"
            remaining = args.log_mib * 1024 * 1024
            while remaining:
                chunk = (block * 2048)[:remaining]
                stream.write(chunk)
                remaining -= len(chunk)
        startup = timing(
            lambda: subprocess.run(
                [sys.executable, "-m", "chipflow", "--version"],
                cwd=Path(__file__).resolve().parent.parent,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            ),
            args.samples,
        )
        with TestClient(create_app(root), base_url="http://localhost") as client:

            def history():
                response = client.get("/api/runs")
                response.raise_for_status()
                assert len(response.json()) == args.runs

            def logs():
                response = client.get(f"/api/runs/{1:032x}/logs")
                response.raise_for_status()
                assert len(response.json()["text"].encode()) <= 256 * 1024

            history()  # Exclude one warm-up request from steady-state timings.
            logs()
            results = {
                "cli_startup": startup,
                "history_api": timing(history, args.samples),
                "bounded_log_api": timing(logs, args.samples),
            }
        report = {
            "scope": "Manager application benchmark only; synthetic failed-history/log fixtures; no EDA validation.",
            "methodology": "perf_counter milliseconds; CLI fresh processes, warm in-process API TestClient; excludes network/browser latency; fixture creation excluded.",
            "environment": {"python": platform.python_version(), "pid": os.getpid(), **environment},
            "fixture": {"runs": args.runs, "log_mib": args.log_mib},
            "results": results,
        }
        serialized = json.dumps(report, indent=2) + "\n"
        if args.output:
            # Refuse overwriting another result or an unrelated user file.
            with args.output.open("x") as stream:
                stream.write(serialized)
        print(serialized, end="")


if __name__ == "__main__":
    main()
