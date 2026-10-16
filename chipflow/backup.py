"""Checksummed, portable backups of completed run history."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from .engine import TERMINAL, file_hash, verify_inputs
from .storage import locked, read_state, save


def backup(store, destination: Path):
    destination = destination.expanduser().resolve()
    if destination.is_relative_to(store.root):
        raise ValueError("Save the backup outside the run directory")
    if destination.exists():
        raise ValueError("Backup destination already exists; choose a new file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with locked(store.root / ".store.lock"):
        runs = [store.get(p.parent.name) for p in sorted(store.root.glob("*/run.json"))]
        if any(run["status"] not in TERMINAL for run in runs):
            raise ValueError("Wait for active runs to finish or cancel them before backing up")
        manifest = {
            "version": 1,
            "created": time.time(),
            "source_root": str(store.root),
            "files": {},
        }
        descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".chipflow-backup-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                with tarfile.open(fileobj=stream, mode="w:gz", dereference=False) as archive:
                    for run in runs:
                        directory = store.directory(run["id"])
                        verify_inputs(directory)
                        for path in sorted(directory.rglob("*")):
                            relative = path.relative_to(store.root).as_posix()
                            if (
                                path.is_symlink()
                                or not path.is_file()
                                or path.relative_to(directory).parts[0].startswith(".")
                                or path.name in {"cancel", "run.tmp"}
                            ):
                                continue
                            manifest["files"][relative] = file_hash(path)
                            archive.add(path, arcname=relative, recursive=False)
                    data = json.dumps(manifest).encode()
                    item = tarfile.TarInfo("backup.json")
                    item.size = len(data)
                    item.mode = 0o600
                    archive.addfile(item, io.BytesIO(data))
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link publication refuses to overwrite an existing destination.
            os.link(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"path": str(destination), "runs": len(runs), "sha256": file_hash(destination)}


def restore(store, source: Path):
    try:
        return _restore(store, source)
    except (tarfile.TarError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid backup archive: {exc}") from exc


def _restore(store, source: Path):
    source = source.expanduser().resolve()
    with locked(store.root / ".store.lock"):
        if any(p.name != ".store.lock" for p in store.root.iterdir()):
            raise ValueError(
                "Restore requires an empty run directory; existing history is never overwritten"
            )
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            names = [item.name for item in members]
            if len(names) != len(set(names)):
                raise ValueError("Backup contains duplicate paths")
            metadata = next((item for item in members if item.name == "backup.json"), None)
            if metadata is None or not metadata.isfile() or metadata.size > 16 * 1024 * 1024:
                raise ValueError("Missing or oversized backup metadata")
            manifest = json.load(archive.extractfile(metadata))
            if (
                not isinstance(manifest, dict)
                or manifest.get("version") != 1
                or not isinstance(manifest.get("files"), dict)
                or not isinstance(manifest.get("source_root"), str)
            ):
                raise ValueError("Unsupported backup format")
            files = [item for item in members if item.name != "backup.json"]
            for item in files:
                parts = PurePosixPath(item.name).parts
                if (
                    not item.isfile()
                    or item.issparse()
                    or len(parts) < 2
                    or not re.fullmatch(r"[0-9a-f]{32}", parts[0])
                    or any(part in {".", ".."} for part in parts[1:])
                    or item.name not in manifest["files"]
                ):
                    raise ValueError(f"Unsafe backup entry: {item.name}")
            if set(names) != {*manifest["files"], "backup.json"}:
                raise ValueError("Backup file list does not match its manifest")
            total = sum(item.size for item in files)
            if total > shutil.disk_usage(store.root).free * 0.95:
                raise ValueError("Insufficient free space to restore this backup")
            with tempfile.TemporaryDirectory(
                prefix=".chipflow-restore-", dir=store.root.parent
            ) as temporary:
                stage = Path(temporary)
                for item in files:
                    path = stage / item.name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with path.open("xb") as stream:
                        shutil.copyfileobj(archive.extractfile(item), stream)
                    path.chmod(0o600)
                    if file_hash(path) != manifest["files"][item.name]:
                        raise ValueError(f"Backup checksum mismatch: {item.name}")
                directories = sorted(stage.iterdir())
                original_root = Path(manifest["source_root"])
                if not original_root.is_absolute():
                    raise ValueError("Invalid backup source directory")
                for directory in directories:
                    state = read_state(directory / "run.json")
                    if state["status"] not in TERMINAL or state["id"] != directory.name:
                        raise ValueError("Backup contains an active or mismatched run")
                    verify_inputs(directory)
                    path = directory / "project.json"
                    project = json.loads(path.read_text())

                    def relocate(value):
                        if (
                            isinstance(value, str)
                            and Path(value).is_absolute()
                            and Path(value).is_relative_to(original_root)
                        ):
                            return str(store.root / Path(value).relative_to(original_root))
                        if isinstance(value, list):
                            return [relocate(item) for item in value]
                        if isinstance(value, dict):
                            return {key: relocate(item) for key, item in value.items()}
                        return value

                    save(path, relocate(project))
                    manifest_path = directory / "manifest.json"
                    input_manifest = json.loads(manifest_path.read_text())
                    input_manifest["project_sha256"] = file_hash(path)
                    input_manifest["restored_from"] = str(source)
                    save(manifest_path, input_manifest)
                committed = []
                try:
                    for directory in directories:
                        target = store.root / directory.name
                        directory.rename(target)
                        committed.append(target)
                except BaseException:
                    for directory in committed:
                        shutil.rmtree(directory)
                    raise
    return {"runs": len(directories), "directory": str(store.root)}
