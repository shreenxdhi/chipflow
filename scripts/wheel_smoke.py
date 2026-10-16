"""Install a wheel in isolation and exercise the installed application."""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--require-sim", action="store_true")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="chipflow-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "environment"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / "bin" / "python"
        command = environment / "bin" / "chipflow"
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "ORFS_HOME", "FLOW_HOME"}
        }
        env["XDG_CONFIG_HOME"] = str(root / "settings")
        subprocess.run(
            [str(python), "-m", "pip", "install", str(wheel)],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        workspace = root / "workspace"
        workspace.mkdir()

        def run(*argv, check=True):
            return subprocess.run(
                [str(command), *argv],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                check=check,
            )

        assert re.fullmatch(r"chipflow \d+\.\d+\.\d+\n", run("--version").stdout)
        assert len(json.loads(run("guide", "--json").stdout)["lessons"]) == 7
        run("init", "demo", "--expert")
        assert (workspace / "demo" / "tcl" / "common.tcl").is_file()
        assert (workspace / "demo" / "GUIDE.md").is_file()
        assert (workspace / "demo" / "counter.v").is_file()
        setup = run("setup", "--target", "sim", "--json", check=False)
        report = json.loads(setup.stdout)
        simulation = bool(shutil.which("iverilog") and shutil.which("vvp"))
        if args.require_sim and not simulation:
            raise RuntimeError("Icarus is required for this smoke test")
        if simulation:
            result = run("run", "demo/chipflow.yaml", "--target", "sim", "--wait")
            assert "PASS: counter self-check" in result.stdout
            assert "completed" in run("quickstart", "first-chip").stdout
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = subprocess.Popen(
            [str(command), "serve", "--port", str(port)],
            cwd=workspace,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        html = response.read().decode()
                    break
                except (urllib.error.URLError, TimeoutError):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Installed browser dashboard did not start") from None
                    time.sleep(0.1)
            assert "Start a design run" in html
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects") as response:
                projects = json.load(response)
            assert any(
                project["example"] and Path(project["path"]).is_relative_to(environment)
                for project in projects
            )
            print(
                json.dumps(
                    {
                        "wheel_install": "passed",
                        "bundled_demo": "passed",
                        "setup_ready": report["ready"],
                        "simulation": "passed" if simulation else "not available",
                        "installed_web_dashboard": "passed",
                    }
                )
            )
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
