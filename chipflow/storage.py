"""Durable run metadata shared by workers and dashboard processes."""

from __future__ import annotations

import codecs
import fcntl
import json
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1


@contextmanager
def locked(path: Path):
    with open(path, "a", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def save(path: Path, value):
    """Atomically replace JSON, including persistence across a host power failure."""
    data = json.dumps(value, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def process_identity(pid: int) -> dict | None:
    """Linux start time and boot ID prevent confusing an unrelated reused PID."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return {
            "pid": pid,
            "started_ticks": stat[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "state": stat[0],
        }
    except (OSError, IndexError, ValueError):
        return None


def process_alive(identity: dict) -> bool:
    current = process_identity(identity["pid"])
    return bool(
        current
        and current["state"] != "Z"
        and current["started_ticks"] == identity["started_ticks"]
        and current["boot_id"] == identity["boot_id"]
    )


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        if value.get("schema_version", 1) != SCHEMA_VERSION:
            raise ValueError("unsupported run schema; upgrade Chipflow before opening this history")
        if value.get("status") not in {
            "queued",
            "running",
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        }:
            raise ValueError("invalid run status")
        if not isinstance(value.get("stages"), list) or not isinstance(value.get("checks"), dict):
            raise ValueError("invalid run stages or checks")
        created = value.get("created")
        if (
            not isinstance(created, (int, float))
            or isinstance(created, bool)
            or not math.isfinite(created)
        ):
            raise ValueError("invalid creation timestamp")
        for key in ("started", "finished"):
            if key in value and (
                not isinstance(value[key], (int, float))
                or isinstance(value[key], bool)
                or not math.isfinite(value[key])
            ):
                raise ValueError(f"invalid {key} timestamp")
        if "metrics" in value and not isinstance(value["metrics"], dict):
            raise ValueError("invalid metrics record")
        identity = value.get("worker_identity")
        if identity is not None and (
            not isinstance(identity, dict)
            or not isinstance(identity.get("pid"), int)
            or identity["pid"] <= 0
            or not isinstance(identity.get("started_ticks"), str)
            or not identity["started_ticks"].isdigit()
            or not isinstance(identity.get("boot_id"), str)
        ):
            raise ValueError("invalid worker identity")
        for stage in value["stages"]:
            if (
                not isinstance(stage, dict)
                or stage.get("name")
                not in {
                    "sim",
                    "synth",
                    "gate_sim",
                    "floorplan",
                    "place",
                    "cts",
                    "route",
                    "finish",
                    "lint",
                    "equivalence",
                }
                or stage.get("status")
                not in {"pending", "running", "completed", "failed", "cancelled", "interrupted"}
            ):
                raise ValueError("invalid stage record")
        return value
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Cannot read run metadata at {path}: {exc}") from exc


def read_log(path: Path, offset=0, limit=256 * 1024, final=False):
    """Bound memory while preserving UTF-8 characters between byte-offset reads."""
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        start = offset if offset <= size else 0
        stream.seek(start)
        data = stream.read(limit)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text = decoder.decode(data, final=final and start + len(data) >= size)
    pending = decoder.getstate()[0]
    position = start + len(data) - len(pending)
    return {"text": text, "offset": position, "more": position < size}
