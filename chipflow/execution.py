"""Command adapters; the existing guardian remains responsible for supervision.

The container adapter is an opt-in execution boundary for trusted projects, not a
multi-tenant sandbox. Images are preinstalled, digest pinned and never pulled by a run.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IMAGE = r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}"


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    backend: Literal["native", "container"] = "native"
    runtime: Literal["docker", "podman"] = "docker"
    image: str | None = None
    cpus: float = Field(default=2, gt=0, le=256)
    memory_mb: int = Field(default=4096, ge=128, le=1048576)
    pids_limit: int = Field(default=512, ge=16, le=65536)
    readonly_mounts: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def pinned_image(self):
        if self.backend == "container" and (not self.image or not re.fullmatch(IMAGE, self.image)):
            raise ValueError("container execution requires an image pinned by @sha256 digest")
        return self

    @field_validator("environment")
    @classmethod
    def explicit_environment(cls, values):
        # These values are deliberately not copied into provenance. Names are recorded.
        for key, value in values.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value:
                raise ValueError("invalid explicitly declared environment variable")
        return values


class ReproducibilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry_mismatch: Literal["warn", "strict", "ignore"] = "warn"


@dataclass
class ExecutionSpec:
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    guardian_args: list[str] = field(default_factory=list)


def runtime_path(config: ExecutionConfig) -> str:
    result = shutil.which(config.runtime)
    if not result:
        raise ValueError(f"Container runtime {config.runtime} is missing; install it first")
    return result


def _mount(path: Path, readonly: bool) -> str:
    path = path.expanduser().resolve(strict=True)
    if not path.is_dir() or path == Path("/") or "," in str(path) or "\n" in str(path):
        raise ValueError(f"Unsafe container mount directory: {path}")
    return f"type=bind,src={path},dst={path}" + (",readonly" if readonly else "")


def prepare(
    config: ExecutionConfig,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    run_dir: Path,
    orfs: Path | None = None,
) -> ExecutionSpec:
    """Prepare an argv-only command; pass guardian_args before the guardian's --.

    The guardian must be used, including for container commands, so orphan cleanup
    survives an abruptly terminated worker. Never invoke the prepared argv directly
    for a long-running job.
    """
    cwd, run_dir = Path(cwd).resolve(), Path(run_dir).resolve()
    if config.backend == "native":
        return ExecutionSpec(list(argv), cwd, {**env, **config.environment})
    runtime = runtime_path(config)
    name = "chipflow-" + uuid.uuid4().hex
    roots = {run_dir: False}
    if orfs is not None and Path(orfs).exists():
        roots[Path(orfs).resolve()] = True
    for item in config.readonly_mounts:
        roots[Path(item).expanduser().resolve()] = True
    # A read-only mount cannot replace the run directory's writable boundary.
    roots[run_dir] = False
    if not any(cwd == root or cwd.is_relative_to(root) for root in roots):
        raise ValueError("Container working directory is outside declared mounts")
    command = [
        runtime,
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cpus",
        str(config.cpus),
        "--memory",
        f"{config.memory_mb}m",
        "--pids-limit",
        str(config.pids_limit),
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m",
        "--workdir",
        str(cwd),
    ]
    for root, readonly in roots.items():
        command.extend(["--mount", _mount(root, readonly)])
    declared = {"QT_QPA_PLATFORM": "offscreen", **config.environment}
    declared.update({key: value for key, value in env.items() if key.startswith("CHIPFLOW_PARAM_")})
    for key, value in declared.items():
        command.extend(["--env", f"{key}={value}"])
    # Override image entrypoints; each invocation is the actual selected EDA command.
    command.extend(["--entrypoint", argv[0], config.image, *argv[1:]])
    return ExecutionSpec(
        command,
        cwd,
        env,
        ["--container-runtime", runtime, "--container-name", name],
    )


def cleanup_container(runtime: str, name: str) -> None:
    """Called by the guardian after its tool process group is terminated."""
    if Path(runtime).name not in {"docker", "podman"} or not re.fullmatch(
        r"chipflow-[0-9a-f]{32}", name
    ):
        raise ValueError("Invalid container cleanup identity")
    try:
        result = subprocess.run(
            [runtime, "rm", "--force", name],
            capture_output=True,
            timeout=15,
        )
        if result.returncode and b"No such container" not in result.stderr:
            print("Chipflow: container cleanup failed; inspect runtime containers.", flush=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Chipflow: container cleanup unavailable: {exc}", flush=True)


def probe_command(config, argv, run_dir, orfs=None, timeout=20):
    """Short capability probe with the same mounts, restrictions and image as jobs."""
    spec = prepare(config, argv, Path(run_dir), os.environ.copy(), Path(run_dir), orfs)
    try:
        result = subprocess.run(
            spec.argv,
            cwd=spec.cwd,
            env=spec.env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        return {
            "status": "available" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "output": (result.stdout + result.stderr)[:8000],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "returncode": None, "output": str(exc)}
    finally:
        if spec.guardian_args:
            cleanup_container(spec.guardian_args[1], spec.guardian_args[3])


def container_identity(config: ExecutionConfig) -> dict:
    """Inspect the local image without pulling or starting any container."""
    runtime = runtime_path(config)
    import json

    try:
        result = subprocess.run(
            [runtime, "image", "inspect", config.image],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        item = json.loads(result.stdout)[0]
        return {
            "status": "available",
            "reference": config.image,
            "id": item["Id"],
            "repo_digests": item.get("RepoDigests", []),
            "os": item.get("Os"),
            "architecture": item.get("Architecture"),
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        return {"status": "unavailable", "reference": config.image, "error": str(exc)}
