"""Local, bounded experiments with frozen inputs and real sequential Store runs."""

from __future__ import annotations

import itertools
import json
import math
import re
import shutil
import time
import uuid
from pathlib import Path

from .config import Project, load_project
from .reports import content_identity, pareto_frontier
from .storage import locked, save

MAX_CANDIDATES = 64
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
PHYSICAL_PARAMETERS = {
    "physical.PLACE_DENSITY": (0, 1),
    "physical.CORE_UTILIZATION": (0, 100),
    "physical.CORE_ASPECT_RATIO": (0, 100),
    "physical.CORE_MARGIN": (0, 10000),
}


def _finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def _apply(config, key, value):
    if not _finite(value):
        raise ValueError("sweep values must be finite numbers")
    result = json.loads(json.dumps(config))
    match = re.fullmatch(r"clock\.([A-Za-z_][A-Za-z0-9_]*)\.period_ns", key)
    if match:
        if result.get("sdc"):
            raise ValueError(
                "clock sweeps require generated constraints; custom SDC is never rewritten"
            )
        clocks = [clock for clock in result["clocks"] if clock["port"] == match[1]]
        if len(clocks) != 1 or value <= 0:
            raise ValueError("clock sweep must name one configured clock with positive period")
        clocks[0]["period_ns"] = value
    elif key in PHYSICAL_PARAMETERS:
        lower, upper = PHYSICAL_PARAMETERS[key]
        if not lower < value <= upper:
            raise ValueError(f"{key} must be greater than {lower} and at most {upper}")
        name = key.split(".")[1]
        if name in result.get("orfs_variables", {}):
            raise ValueError("sweep parameter conflicts with orfs_variables")
        result.setdefault("physical", {})[name] = value
    else:
        raise ValueError(f"unsupported sweep parameter: {key}")
    Project.model_validate(result)
    return result


def _validate_objectives(objectives, constraints, required_checks):
    if (
        not isinstance(objectives, dict)
        or not objectives
        or any(
            not isinstance(key, str) or not key or direction not in {"min", "max"}
            for key, direction in objectives.items()
        )
    ):
        raise ValueError("objectives must map metric names to min/max")
    if not isinstance(constraints, dict):
        raise ValueError("constraints must map metric names to min/max bounds")
    for key, bounds in constraints.items():
        if (
            not isinstance(key, str)
            or not isinstance(bounds, dict)
            or not bounds
            or set(bounds) - {"min", "max"}
            or not all(_finite(v) for v in bounds.values())
        ):
            raise ValueError("constraint bounds must contain finite min/max values")
        if bounds.get("min", -math.inf) > bounds.get("max", math.inf):
            raise ValueError("constraint min must not exceed max")
    if not isinstance(required_checks, list) or any(
        not isinstance(key, str) or not key for key in required_checks
    ):
        raise ValueError("required_checks must be a list of check names")


class ExperimentStore:
    """Small controller; execution remains owned by Store and its existing workers.

    Experiments are private local metadata under .experiments. They contain frozen
    inputs plus explicitly saved candidate configurations; no shell text is accepted.
    """

    def __init__(self, store):
        self.store = store
        self.root = store.root / ".experiments"
        if self.root.is_symlink():
            raise ValueError("experiment directory must not be a symlink")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if not self.root.resolve().is_relative_to(store.root):
            raise ValueError("experiment directory escapes run store")

    def directory(self, experiment_id):
        if not isinstance(experiment_id, str) or not re.fullmatch(r"[0-9a-f]{32}", experiment_id):
            raise ValueError("invalid experiment ID")
        path = self.root / experiment_id
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("experiment path escapes store")
        if not (path / "experiment.json").is_file():
            raise FileNotFoundError(experiment_id)
        return path

    def get(self, experiment_id):
        path = self.directory(experiment_id) / "experiment.json"
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("unsafe or oversized experiment metadata")
        result = json.loads(path.read_text())
        if (
            not isinstance(result, dict)
            or result.get("id") != experiment_id
            or result.get("schema") != 1
        ):
            raise ValueError("invalid experiment metadata")
        from .engine import STAGES

        if (
            not _finite(result.get("created"))
            or result.get("status") not in {"draft", "running", "completed", "stopped", "failed"}
            or result.get("target") not in STAGES
            or not isinstance(result.get("verify_first"), bool)
            or not isinstance(result.get("name"), str)
            or not isinstance(result.get("parameters"), dict)
        ):
            raise ValueError("invalid experiment state metadata")
        candidates = result.get("candidates")
        files = result.get("frozen_files")
        if (
            not isinstance(candidates, list)
            or not 1 <= len(candidates) <= MAX_CANDIDATES
            or not isinstance(files, dict)
        ):
            raise ValueError("invalid experiment candidate list or integrity manifest")
        for candidate in candidates:
            if (
                not isinstance(candidate, dict)
                or not isinstance(candidate.get("configuration"), str)
                or not re.fullmatch(r"candidate-[0-9]{3}\.json", candidate["configuration"])
                or candidate["configuration"] not in files
                or not isinstance(candidate.get("parameters"), dict)
            ):
                raise ValueError("invalid experiment configuration reference")
        for candidate in candidates:
            for field in ("run_id", "verification_run_id"):
                run_id = candidate.get(field)
                if run_id is not None and (
                    not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id)
                ):
                    raise ValueError("invalid experiment run reference")
        _validate_objectives(
            result.get("objectives"), result.get("constraints"), result.get("required_checks")
        )
        return result

    def list(self):
        return sorted(
            [self.get(path.parent.name) for path in self.root.glob("*/experiment.json")],
            key=lambda item: item["created"],
            reverse=True,
        )

    def create(
        self,
        config_path: Path,
        *,
        name: str,
        hypothesis: str = "",
        parameters: dict,
        objectives: dict,
        constraints: dict | None = None,
        required_checks: list | None = None,
        baseline: str | None = None,
        target: str = "finish",
        verify_first: bool = True,
    ):
        from .engine import STAGES

        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("provide an experiment name of 1–200 characters")
        if not isinstance(hypothesis, str) or len(hypothesis) > 10000:
            raise ValueError("hypothesis must be text of at most 10000 characters")
        if target not in STAGES:
            raise ValueError("unknown target")
        if not isinstance(verify_first, bool):
            raise ValueError("verify_first must be boolean")
        constraints = {} if constraints is None else constraints
        required_checks = (
            (["timing", "routing_drc"] if target == "finish" else [])
            if required_checks is None
            else required_checks
        )
        _validate_objectives(objectives, constraints, required_checks)
        project, base = load_project(Path(config_path))
        config = project.model_dump()
        if verify_first and target != "sim" and not config.get("simulation"):
            raise ValueError("verification-first experiments require simulation configuration")
        if not isinstance(parameters, dict) or not parameters or len(parameters) > 8:
            raise ValueError("provide 1–8 sweep parameters")
        count = 1
        for key, values in parameters.items():
            if not isinstance(values, list) or not values or len(values) > MAX_CANDIDATES:
                raise ValueError("each sweep parameter requires 1–64 choices")
            if len({content_identity(value) for value in values}) != len(values):
                raise ValueError("duplicate parameter choices")
            count *= len(values)
            for value in values:
                _apply(config, key, value)
        if count > MAX_CANDIDATES:
            raise ValueError("sweep is limited to 64 candidate combinations")
        if baseline is not None:
            self.store.get(baseline)
        experiment_id = uuid.uuid4().hex
        directory = self.root / experiment_id
        directory.mkdir(mode=0o700)
        try:
            frozen = self._freeze(config, base, directory)
            save(directory / "baseline.json", frozen)
            candidates = []
            for index, values in enumerate(itertools.product(*parameters.values())):
                selection = dict(zip(parameters, values, strict=True))
                candidate = frozen
                for key, value in selection.items():
                    candidate = _apply(candidate, key, value)
                relative = f"candidate-{index:03d}.json"
                save(directory / relative, candidate)
                candidates.append(
                    {
                        "configuration": relative,
                        "parameters": selection,
                        "run_id": None,
                        "verification_run_id": None,
                    }
                )
            files = {
                str(path.relative_to(directory)): _hash(path)
                for path in directory.rglob("*")
                if path.is_file()
            }
            result = {
                "schema": 1,
                "id": experiment_id,
                "name": name,
                "hypothesis": hypothesis,
                "created": time.time(),
                "status": "draft",
                "baseline_run_id": baseline,
                "target": target,
                "verify_first": verify_first,
                "parameters": parameters,
                "objectives": objectives,
                "constraints": constraints,
                "required_checks": required_checks,
                "candidates": candidates,
                "frozen_files": files,
                "scope": "Finite Cartesian search; results describe measured eligible candidates only.",
                "limitations": [
                    "External toolchain and PDK are not copied; each run records its environment.",
                    "Experiments must be backed up separately from run-history archives.",
                ],
            }
            if len(json.dumps(result).encode()) > 4 * 1024 * 1024:
                raise ValueError("experiment metadata exceeds 4 MiB budget")
            save(directory / "experiment.json", result)
            return result
        except BaseException:
            shutil.rmtree(directory)
            raise

    def _freeze(self, config, base, directory):
        config = json.loads(json.dumps(config))
        copied_bytes = 0
        copied_files = 0
        max_bytes = min(1024**3, shutil.disk_usage(directory).free // 2)

        def snapshot(value, category, index):
            nonlocal copied_bytes, copied_files
            source = (base / Path(value).expanduser()).resolve()
            if not source.exists():
                raise ValueError(f"missing input: {source}")
            if source.is_dir() and directory.resolve().is_relative_to(source):
                raise ValueError("input directory contains experiment store")
            destination = directory / "inputs" / category / str(index) / source.name
            paths = source.rglob("*") if source.is_dir() else iter([source])
            for path in paths:
                if path.is_symlink():
                    raise ValueError("experiment input bundles must not contain symlinks")
                if path.is_file():
                    copied_bytes += path.stat().st_size
                    copied_files += 1
                if copied_bytes > max_bytes or copied_files > 100000:
                    raise ValueError(
                        "experiment snapshot exceeds 1 GiB, half free disk, or 100000 files"
                    )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
            return str(destination)

        for field in ("rtl", "include_dirs"):
            config[field] = [snapshot(value, field, i) for i, value in enumerate(config[field])]
        if config.get("simulation"):
            for field in ("testbench", "cell_models"):
                config["simulation"][field] = [
                    snapshot(value, field, i)
                    for i, value in enumerate(config["simulation"].get(field, []))
                ]
        if config.get("sdc"):
            config["sdc"] = snapshot(config["sdc"], "constraints", 0)
        if config.get("tcl"):
            config["tcl"]["directory"] = snapshot(config["tcl"]["directory"], "tcl", 0)
        for key, values in config.get("orfs_files", {}).items():
            config["orfs_files"][key] = [
                snapshot(value, f"orfs_files/{key}", i) for i, value in enumerate(values)
            ]
        if config.get("physical", {}).get("ADDER_MAP_FILE"):
            config["physical"]["ADDER_MAP_FILE"] = snapshot(
                config["physical"]["ADDER_MAP_FILE"], "adder_map", 0
            )
        config["orfs"] = str((base / Path(config["orfs"]).expanduser()).resolve())
        return config

    def request_stop(self, experiment_id):
        """Stop scheduling and request cancellation of the active run on next poll."""
        (self.directory(experiment_id) / "stop").touch(mode=0o600)
        return {"id": experiment_id, "stop_requested": True}

    def run(
        self,
        experiment_id,
        *,
        poll_seconds: float = 0.2,
        stop_on_failure: bool = True,
        resume: bool = False,
    ):
        """Block while sequential workers execute; holds no Store or run-state locks."""
        if not _finite(poll_seconds) or not 0.01 <= poll_seconds <= 5:
            raise ValueError("poll_seconds must be 0.01–5")
        directory = self.directory(experiment_id)
        # This scheduler-only lock prevents competing experiment controllers from
        # launching in parallel; Store remains responsive for get/cancel/backups.
        with locked(self.root / ".scheduler.lock"):
            experiment = self.get(experiment_id)
            self._verify(experiment, directory)
            if experiment["status"] == "completed":
                return experiment
            if (directory / "stop").exists():
                if not resume:
                    experiment["status"] = "stopped"
                    save(directory / "experiment.json", experiment)
                    return experiment
                (directory / "stop").unlink()
            experiment["status"] = "running"
            save(directory / "experiment.json", experiment)
            try:
                for candidate in experiment["candidates"]:
                    if (directory / "stop").exists():
                        experiment["status"] = "stopped"
                        break
                    config_path = directory / candidate["configuration"]
                    if experiment["verify_first"] and experiment["target"] != "sim":
                        verification = self._execute(
                            experiment,
                            candidate,
                            "verification_run_id",
                            config_path,
                            "sim",
                            directory,
                            poll_seconds,
                        )
                        if verification["status"] != "completed":
                            candidate["blocked"] = "RTL simulation did not complete successfully"
                            experiment["status"] = (
                                "stopped" if (directory / "stop").exists() else "failed"
                            )
                            if stop_on_failure or experiment["status"] == "stopped":
                                break
                            continue
                    result = self._execute(
                        experiment,
                        candidate,
                        "run_id",
                        config_path,
                        experiment["target"],
                        directory,
                        poll_seconds,
                    )
                    candidate["execution_status"] = result["status"]
                    if (directory / "stop").exists():
                        experiment["status"] = "stopped"
                        break
                    if result["status"] != "completed" and stop_on_failure:
                        experiment["status"] = "failed"
                        break
                else:
                    experiment["status"] = "completed"
                experiment["analysis"] = self.analyze(experiment)
            except Exception as exc:
                experiment["status"] = "failed"
                experiment["error"] = str(exc)
                raise
            finally:
                save(directory / "experiment.json", experiment)
            return experiment

    def _verify(self, experiment, directory):
        for relative, digest in experiment["frozen_files"].items():
            path = directory / relative
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(directory.resolve())
                or not path.is_file()
                or _hash(path) != digest
            ):
                raise ValueError(f"experiment frozen-input integrity failure: {relative}")

    def _execute(self, experiment, candidate, field, config_path, target, directory, poll_seconds):
        if candidate.get(field) is None:
            state = self.store.start(config_path, target)
            candidate[field] = state["id"]
            save(directory / "experiment.json", experiment)
        while True:
            result = self.store.get(candidate[field])
            if result["status"] in TERMINAL:
                return result
            if (directory / "stop").exists():
                self.store.cancel(candidate[field])
            time.sleep(poll_seconds)

    def analyze(self, experiment):
        """Unknown/failed required evidence excludes a candidate from the frontier."""
        eligible, excluded = [], []
        for candidate in experiment["candidates"]:
            run_id = candidate.get("run_id")
            if not run_id:
                excluded.append(
                    {"id": None, "parameters": candidate["parameters"], "reason": "not run"}
                )
                continue
            run = self.store.get(run_id)
            reasons = []
            if run["status"] != "completed":
                reasons.append("execution incomplete")
            for key in experiment["required_checks"]:
                if run.get("checks", {}).get(key) != "passed":
                    reasons.append(f"required check {key} did not pass")
            quality = run.get("quality")
            if quality and quality.get("status") not in {"passed", "not_required"}:
                reasons.append("quality gates failed, incomplete, or unknown")
            for key, bounds in experiment["constraints"].items():
                value = run.get("metrics", {}).get(key)
                if not _finite(value):
                    reasons.append(f"constraint metric {key} unavailable")
                elif not bounds.get("min", -math.inf) <= value <= bounds.get("max", math.inf):
                    reasons.append(f"constraint metric {key} outside bounds")
            if reasons:
                excluded.append({"id": run_id, "reason": "; ".join(reasons)})
            else:
                eligible.append(run)
        result = pareto_frontier(eligible, experiment["objectives"])
        result["excluded"] = excluded + result["excluded"]
        result["search_space"] = experiment["parameters"]
        result["constraints"] = experiment["constraints"]
        result["required_checks"] = experiment["required_checks"]
        result["note"] = (
            "Eligibility is limited to declared checks/bounds; no signoff or global optimality claim."
        )
        return result


def _hash(path):
    import hashlib

    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
