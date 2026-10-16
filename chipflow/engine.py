"""One detached worker per run; subprocess groups are killed on cancel/timeout."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .config import Project, load_project, safe_make_path
from .customization import make_value
from .environment import (
    activate_environment,
    discover_orfs,
    find_tool,
    flow_path,
    orfs_bin_dirs,
    tool_search_path,
)
from .storage import SCHEMA_VERSION, locked, process_alive, process_identity, read_state, save

STAGES = ["sim", "synth", "gate_sim", "floorplan", "place", "cts", "route", "finish"]
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_inputs(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest.get("project_sha256")
        and file_hash(directory / "project.json") != manifest["project_sha256"]
    ):
        raise ValueError("Saved configuration failed integrity check: project.json")
    for relative, expected in manifest["input_sha256"].items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"Saved input failed integrity check: {relative}")


def probe(argv, cwd=None):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=15)
        return (p.stdout + p.stderr).strip()[:4000]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def doctor(orfs: str | None = None):
    root = Path(orfs or discover_orfs()).expanduser().resolve()
    flow = root if root.name == "flow" else root / "flow"
    tools = {}
    for name, args in {
        "openroad": ["-version"],
        "yosys": ["-V"],
        "iverilog": ["-V"],
        "vvp": ["-V"],
        "klayout": ["-v"],
        "make": ["--version"],
    }.items():
        exe = find_tool(name, orfs_bin_dirs(root))
        tools[name] = {"path": exe, "version": probe([exe, *args]) if exe else None}
    return {
        "flow": str(flow),
        "makefile": (flow / "Makefile").is_file(),
        "revision": probe(["git", "rev-parse", "HEAD"], flow) if flow.exists() else None,
        "dirty": probe(
            ["git", "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"],
            flow,
        )
        if flow.exists()
        else None,
        "platforms": sorted(
            p.name for p in (flow / "platforms").glob("*") if (p / "config.mk").is_file()
        ),
        "tools": tools,
    }


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def directory(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("invalid run ID")
        result = self.root / run_id
        if not result.resolve().is_relative_to(self.root):
            raise ValueError("Run directory escapes the history directory")
        if not (result / "run.json").is_file():
            raise FileNotFoundError(run_id)
        return result

    def get(self, run_id):
        d = self.directory(run_id)
        with locked(d / ".state.lock"):
            state = json_safe(read_state(d / "run.json"))
            if (
                state.get("id") != run_id
                or state.get("target") not in STAGES
                or not isinstance(state.get("name"), str)
            ):
                raise ValueError("Run metadata has an invalid ID, target, or name")
            dead = False
            if state["status"] not in TERMINAL:
                if state.get("worker_identity"):
                    dead = not process_alive(state["worker_identity"])
                elif time.time() - state["created"] > 5:
                    # Compatibility with history written before worker identities.
                    pid = state.get("pid") or 999999999
                    identity = process_identity(pid)
                    try:
                        dead = (
                            not identity
                            or identity["state"] == "Z"
                            or str(d).encode() not in Path(f"/proc/{pid}/cmdline").read_bytes()
                        )
                    except FileNotFoundError:
                        dead = True
            if dead:
                state["status"] = "interrupted"
                state["error"] = (
                    "Worker exited unexpectedly; tools are stopped by the command guardian. Inspect logs and retry."
                )
                state["finished"] = time.time()
                state.pop("queue_reason", None)
                for stage in state["stages"]:
                    if stage["status"] == "running":
                        stage["status"] = "interrupted"
                        stage["finished"] = state["finished"]
                save(d / "run.json", state)
        return state

    def history(self):
        runs = []
        for path in self.root.glob("*/run.json"):
            try:
                run = self.get(path.parent.name)
                if not isinstance(run.get("created"), (int, float)):
                    raise ValueError("invalid creation timestamp")
                runs.append(run)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                logging.getLogger(__name__).warning("Cannot read run %s: %s", path.parent.name, exc)
        return sorted(runs, key=lambda r: r["created"], reverse=True)

    def compare(self, left, right):
        a, b = self.get(left), self.get(right)
        return {
            "left": a,
            "right": b,
            "deltas": {
                key: b["metrics"][key] - value
                for key, value in a.get("metrics", {}).items()
                if isinstance(value, (int, float))
                and isinstance(b.get("metrics", {}).get(key), (int, float))
            },
        }

    def retry(self, run_id):
        previous = self.get(run_id)
        directory = self.directory(run_id)
        verify_inputs(directory)
        return self.start(directory / "project.json", previous["target"])

    def cancel(self, run_id):
        state = self.get(run_id)
        if state["status"] not in TERMINAL:
            (self.directory(run_id) / "cancel").touch()
        return {"id": run_id, "cancellation_requested": state["status"] not in TERMINAL}

    def artifact(self, run_id, relative):
        root = self.directory(run_id)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError(relative)
        return path

    def artifacts(self, run_id):
        root = self.directory(run_id)
        return [
            {"path": str(p.relative_to(root)), "bytes": p.stat().st_size}
            for p in sorted(root.rglob("*"))
            if p.is_file()
            and not p.is_symlink()
            and not p.name.startswith(".")
            and p.name not in {"cancel", "run.tmp"}
        ]

    def start(self, config_path: Path, target="sim"):
        with locked(self.root / ".store.lock"):
            return self._start(config_path, target)

    def _start(self, config_path: Path, target):
        if target not in STAGES:
            raise ValueError("unknown target")
        activate_environment()
        from .projects import PreflightError, inspect_project

        preflight = inspect_project(config_path, target)
        if not preflight["ready"]:
            raise PreflightError(preflight["issues"])
        project, base = load_project(config_path)
        if target in {"sim", "gate_sim"} and not project.simulation:
            raise ValueError("simulation configuration is required")
        if target == "gate_sim" and not project.simulation.cell_models:
            raise ValueError(
                "gate_sim requires functional cell_models compatible with this platform"
            )
        flow = Path(project.orfs).expanduser()
        if not flow.is_absolute():
            flow = base / flow
        flow = flow_path(flow)
        safe_make_path(flow)
        if target != "sim" and not (flow / "Makefile").is_file():
            raise ValueError(f"ORFS Makefile missing: {flow}")
        run_id = uuid.uuid4().hex
        d = self.root / run_id
        safe_make_path(d)
        d.mkdir(mode=0o700)
        launched = False
        try:
            hashes = {}

            def snapshot(src, category, index):
                original = (base / Path(src).expanduser()).resolve()
                if not original.exists():
                    raise ValueError(f"input does not exist: {original}")
                is_directory = category in {"include_dirs", "tcl"}
                if is_directory != original.is_dir():
                    raise ValueError(
                        f"expected {'directory' if is_directory else 'file'}: {original}"
                    )
                dest = d / "inputs" / category / str(index) / original.name
                safe_make_path(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if original.is_dir():
                    if dest.is_relative_to(original):
                        raise ValueError(
                            "An input directory contains the run store. Use a separate headers or Tcl directory."
                        )
                    shutil.copytree(original, dest)
                else:
                    shutil.copy2(original, dest)
                for file in dest.rglob("*") if dest.is_dir() else [dest]:
                    if file.is_file():
                        hashes[str(file.relative_to(d))] = file_hash(file)
                return str(dest)

            cfg = project.model_dump()
            for field in ["rtl", "include_dirs"]:
                cfg[field] = [snapshot(p, field, i) for i, p in enumerate(cfg[field])]
            if project.simulation:
                for field in ["testbench", "cell_models"]:
                    cfg["simulation"][field] = [
                        snapshot(p, field, i) for i, p in enumerate(cfg["simulation"][field])
                    ]
            if cfg["sdc"]:
                cfg["sdc"] = snapshot(cfg["sdc"], "constraints", 0)
            else:
                sdc = d / "inputs" / "clocks.sdc"
                sdc.write_text(
                    "".join(
                        f"create_clock -name {c.port} -period {c.period_ns} [get_ports {{{c.port}}}]\n"
                        for c in project.clocks
                    )
                )
                cfg["sdc"] = str(sdc)
                hashes[str(sdc.relative_to(d))] = hashlib.sha256(sdc.read_bytes()).hexdigest()
            cfg["orfs"] = str(flow)
            if cfg["tcl"]:
                cfg["tcl"]["directory"] = snapshot(cfg["tcl"]["directory"], "tcl", 0)
            for name, values in cfg["orfs_files"].items():
                cfg["orfs_files"][name] = [
                    snapshot(path, f"orfs_files/{name}", index) for index, path in enumerate(values)
                ]
            if cfg["physical"].get("ADDER_MAP_FILE"):
                cfg["physical"]["ADDER_MAP_FILE"] = snapshot(
                    cfg["physical"]["ADDER_MAP_FILE"], "adder_map", 0
                )
            save(d / "project.json", cfg)
            save(
                d / "manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "source_config": str(config_path.expanduser().resolve()),
                    "input_sha256": hashes,
                    "project_sha256": file_hash(d / "project.json"),
                    "configuration": project.model_dump(),
                },
            )
            state = {
                "schema_version": SCHEMA_VERSION,
                "id": run_id,
                "name": project.name,
                "source_config": str(config_path.expanduser().resolve()),
                "target": target,
                "created": time.time(),
                "status": "queued",
                "stages": [],
                "checks": {"timing": "unknown", "physical": "unknown"},
                "pid": None,
            }
            save(d / "run.json", state)
            log = (d / "worker.log").open("ab")
            with log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "chipflow.worker", str(d)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={**os.environ, "PATH": tool_search_path(orfs_bin_dirs(flow))},
                )
                launched = process is not None
            if process is not None:
                with locked(d / ".state.lock"):
                    state = read_state(d / "run.json")
                    if not state.get("pid") and state["status"] not in TERMINAL:
                        state["pid"] = process.pid
                        state["worker_identity"] = process_identity(process.pid)
                        save(d / "run.json", state)
            return state
        except Exception:
            if not launched:
                shutil.rmtree(d)
            raise


class Cancelled(Exception):
    pass


class Worker:
    def __init__(self, directory: Path):
        self.d = directory
        self.state = json.loads((directory / "run.json").read_text())
        self.cfg = Project.model_validate_json((directory / "project.json").read_text())
        self.flow_lock = None

    def persist(self):
        with locked(self.d / ".state.lock"):
            save(self.d / "run.json", self.state)

    def command(self, argv, cwd, timeout):
        if (self.d / "cancel").exists():
            raise Cancelled()
        with (self.d / "console.log").open("ab", buffering=0) as log:
            log.write(("\n$ " + shlex.join(argv) + "\n").encode())
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["QT_QPA_PLATFORM"] = "offscreen"
            if self.cfg.tcl:
                env.update(
                    {
                        f"CHIPFLOW_PARAM_{key}": make_value(value)
                        for key, value in self.cfg.tcl.parameters.items()
                    }
                )
            parent_fd, worker_fd = os.pipe()
            inherited = (parent_fd,) + ((self.flow_lock.fileno(),) if self.flow_lock else ())
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "chipflow.runner",
                        "--parent-fd",
                        str(parent_fd),
                        "--timeout",
                        str(timeout),
                        "--",
                        *argv,
                    ],
                    cwd=cwd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                    pass_fds=inherited,
                )
            except BaseException:
                os.close(worker_fd)
                raise
            finally:
                os.close(parent_fd)
            try:
                while process.poll() is None:
                    if (self.d / "cancel").exists():
                        raise Cancelled()
                    time.sleep(0.1)
                if (self.d / "cancel").exists():
                    raise Cancelled()
                if process.returncode == 124:
                    raise TimeoutError(f"command exceeded {timeout}s")
                if process.returncode:
                    raise RuntimeError(f"command exited {process.returncode}")
            finally:
                os.close(worker_fd)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def simulation(self, gate=False):
        sim = self.cfg.simulation
        if not sim:
            raise ValueError("simulation configuration missing")
        sources = self.cfg.rtl
        if gate:
            sources = [
                str(self.d / "results" / self.cfg.platform / self.cfg.top / "base" / "1_2_yosys.v"),
                *sim.cell_models,
            ]
        output = self.d / ("gate_sim.vvp" if gate else "rtl_sim.vvp")
        argv = ["iverilog", "-g2012", "-s", sim.top, "-o", str(output)]
        for include in self.cfg.include_dirs:
            argv.extend(["-I", include])
        self.command([*argv, *sources, *sim.testbench], self.d, sim.timeout_seconds)
        offset = (self.d / "console.log").stat().st_size
        self.command(["vvp", str(output)], self.d, sim.timeout_seconds)
        found = False
        marker = sim.pass_marker.encode()
        with (self.d / "console.log").open("rb") as log:
            log.seek(offset)
            # Do not count the command line itself as a pass marker.
            log.readline()
            log.readline()
            tail = b""
            while block := log.read(256 * 1024):
                data = tail + block
                if marker in data:
                    found = True
                    break
                tail = data[-max(len(marker) - 1, 0) :] if len(marker) > 1 else b""
        if not found:
            raise RuntimeError(f"simulation exited without pass marker {sim.pass_marker!r}")

    def compatibility_args(self):
        scripts = Path(self.cfg.orfs) / "scripts"
        changes = []
        adapted = {}
        synth = scripts / "synth.tcl"
        needle = "stat -hierarchy {*}$lib_args"
        help_text = probe([shutil.which("yosys") or "yosys", "-Q", "-T", "-p", "help stat"])
        if synth.exists() and needle in synth.read_text() and "-hierarchy" not in help_text:
            adapted["synth.tcl"] = synth.read_text().replace(needle, "stat {*}$lib_args")
            changes.append(
                "Remove -hierarchy from Yosys statistics command; legacy stat reports hierarchy implicitly"
            )
        # Detect the OpenRCX API rather than guessing from version strings.
        outputs = scripts / "final_outputs.tcl"
        probe_script = self.d / "probe_rcx.tcl"
        probe_script.write_text(
            'puts "CHIPFLOW_RCX:[llength [info commands set_extraction_rules_file]]"\nhelp extract_parasitics\n'
        )
        rcx = probe(
            [shutil.which("openroad") or "openroad", "-no_init", "-exit", str(probe_script)]
        )
        old = "set_extraction_rules_file $::env(RCX_RULES)\n  extract_parasitics"
        if (
            outputs.exists()
            and old in outputs.read_text()
            and "CHIPFLOW_RCX:0" in rcx
            and "-ext_model_file" in rcx
        ):
            adapted["final_outputs.tcl"] = outputs.read_text().replace(
                old, "extract_parasitics -ext_model_file $::env(RCX_RULES)"
            )
            changes.append(
                "Use legacy OpenRCX extract_parasitics -ext_model_file with the same platform rules"
            )
        if outputs.exists() and not self.cfg.save_images:
            image_condition = "if { [ord::openroad_gui_compiled] } {"
            content = adapted.get("final_outputs.tcl", outputs.read_text())
            if image_condition in content:
                adapted["final_outputs.tcl"] = content.replace(image_condition, "if { 0 } {")
                changes.append(
                    "Disable optional GUI images for headless execution (save_images: false)"
                )
        if not adapted:
            return []
        overlay = self.d / "orfs_scripts"
        overlay.mkdir()
        for item in scripts.iterdir():
            (overlay / item.name).symlink_to(item, target_is_directory=item.is_dir())
        hashes = {}
        for name, content in adapted.items():
            (overlay / name).unlink()
            (overlay / name).write_text(content)
            hashes[name] = {
                "original_sha256": hashlib.sha256((scripts / name).read_bytes()).hexdigest(),
                "adapted_sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        save(self.d / "compatibility.json", {"changes": changes, "scripts": hashes})
        return ["SCRIPTS_DIR=" + str(overlay)]

    def make_config(self):
        cfg = self.cfg
        entries = {
            "DESIGN_NAME": cfg.top,
            "PLATFORM": cfg.platform,
            "VERILOG_FILES": " ".join(map(lambda p: safe_make_path(Path(p)), cfg.rtl)),
            "SDC_FILE": safe_make_path(Path(cfg.sdc)),
            "VERILOG_INCLUDE_DIRS": " ".join(cfg.include_dirs),
            **cfg.physical,
            **cfg.orfs_variables,
            **{
                key: " ".join(safe_make_path(Path(path)) for path in values)
                for key, values in cfg.orfs_files.items()
            },
        }
        if cfg.tcl:
            root = Path(cfg.tcl.directory)
            entries["CHIPFLOW_TCL_DIR"] = safe_make_path(root)
            entries.update(
                {name: safe_make_path(root / relative) for name, relative in cfg.tcl.hooks.items()}
            )
        path = self.d / "config.mk"
        path.write_text(
            "".join(f"export {key} = {make_value(value)}\n" for key, value in entries.items())
        )
        return path

    def validate_outputs(self, stage):
        required = {
            "synth": ["1_2_yosys.v"],
            "floorplan": ["2_floorplan.odb"],
            "place": ["3_place.odb"],
            "cts": ["4_cts.odb"],
            "route": ["5_route.odb"],
            "finish": ["6_final.odb", "6_final.gds"],
        }
        root = self.d / "results" / self.cfg.platform / self.cfg.top / "base"
        outputs = []
        for name in required[stage]:
            path = root / name
            if not path.is_file() or path.is_symlink() or not path.stat().st_size:
                raise RuntimeError(
                    f"{stage} exited successfully but required output is missing or empty: {name}"
                )
            if name.endswith(".gds"):
                with path.open("rb") as stream:
                    if stream.read(4) != b"\x00\x06\x00\x02":
                        raise RuntimeError(f"Final layout is not a GDSII stream: {name}")
            outputs.append(
                {
                    "path": str(path.relative_to(self.d)),
                    "bytes": path.stat().st_size,
                    "sha256": file_hash(path),
                }
            )
        self.state["stages"][-1]["outputs"] = outputs

    def collect_metrics(self):
        metrics = {}
        for p in sorted((self.d / "logs").rglob("*.json")):
            try:
                value = json.loads(p.read_text())
                if isinstance(value, dict):
                    metrics[str(p.relative_to(self.d))] = json_safe(value)
            except (ValueError, OSError):
                pass
        save(self.d / "metrics.json", metrics)
        # Only use final-report metrics; earlier stages are estimates.
        final = {
            k: v
            for path, data in metrics.items()
            if Path(path).name.startswith("6_finish") or Path(path).name.startswith("6_report")
            for k, v in data.items()
        }
        setup = final.get("finish__timing__setup__ws", final.get("timing__setup__ws"))
        hold = final.get("finish__timing__hold__ws", final.get("timing__hold__ws"))
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in (setup, hold)
        ):
            self.state["checks"]["timing"] = "passed" if setup >= 0 and hold >= 0 else "failed"
        drc = final.get("route__drc_errors")
        for path, data in metrics.items():
            if Path(path).name == "5_2_route.json":
                drc = data.get("detailedroute__route__drc_errors", drc)
        self.state["checks"]["routing_drc"] = "unknown"
        if (
            isinstance(drc, (int, float))
            and not isinstance(drc, bool)
            and math.isfinite(drc)
            and drc >= 0
        ):
            self.state["checks"]["routing_drc"] = "passed" if drc == 0 else "failed"
            if drc > 0:
                self.state["checks"]["physical"] = "failed"
        self.state["metrics"] = final

    def run(self):
        activate_environment(orfs_bin_dirs(self.cfg.orfs))
        self.state.update(
            status="queued", pid=os.getpid(), worker_identity=process_identity(os.getpid())
        )
        flow_lock = None
        previous_handlers = {}

        def stop(signum, frame):
            (self.d / "cancel").touch()

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, stop)
        self.persist()
        try:
            if (self.d / "manifest.json").exists():
                verify_inputs(self.d)
            if (self.d / "cancel").exists():
                raise Cancelled()
            if self.state["target"] != "sim":
                flow_lock = (self.d.parent / ".physical-flow.lock").open("a")
                self.flow_lock = flow_lock
                while True:
                    if (self.d / "cancel").exists():
                        raise Cancelled()
                    try:
                        fcntl.flock(flow_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        self.state["queue_reason"] = (
                            "Waiting for another physical flow in this workspace"
                        )
                        self.persist()
                        time.sleep(0.2)
            self.state.pop("queue_reason", None)
            self.state.update(status="running", started=time.time())
            self.persist()
            save(self.d / "toolchain.json", doctor(self.cfg.orfs))
            config = self.make_config()
            target = self.state["target"]
            compat = self.compatibility_args() if target != "sim" else []
            physical = ["synth", "floorplan", "place", "cts", "route", "finish"]
            stages = (
                ["sim"]
                if target == "sim"
                else (
                    ["synth", "gate_sim"]
                    if target == "gate_sim"
                    else physical[: physical.index(target) + 1]
                )
            )
            for stage in stages:
                entry = {"name": stage, "status": "running", "started": time.time()}
                self.state["stages"].append(entry)
                self.persist()
                try:
                    if stage in {"sim", "gate_sim"}:
                        self.simulation(stage == "gate_sim")
                    else:
                        self.command(
                            [
                                "make",
                                "--file=" + str(Path(self.cfg.orfs) / "Makefile"),
                                "DESIGN_CONFIG=" + str(config),
                                "WORK_HOME=" + str(self.d),
                                "NUM_CORES=" + str(self.cfg.threads),
                                "OPENROAD_EXE=" + (shutil.which("openroad") or "openroad"),
                                "YOSYS_EXE=" + (shutil.which("yosys") or "yosys"),
                                *compat,
                                stage,
                            ],
                            Path(self.cfg.orfs),
                            self.cfg.timeout_seconds,
                        )
                        self.validate_outputs(stage)
                    entry["status"] = "completed"
                except BaseException:
                    entry["status"] = "cancelled" if (self.d / "cancel").exists() else "failed"
                    raise
                finally:
                    entry["finished"] = time.time()
                    self.persist()
            if (self.d / "cancel").exists():
                raise Cancelled()
            self.state["status"] = "completed"
        except Cancelled:
            self.state["status"] = "cancelled"
        except Exception as exc:
            self.state.update(status="failed", error=str(exc))
        finally:
            try:
                self.collect_metrics()
            except Exception as exc:
                self.state["metrics_error"] = str(exc)
            self.state["finished"] = time.time()
            self.persist()
            if flow_lock is not None:
                flow_lock.close()
                self.flow_lock = None
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
