"""Local resource observations and a private user-wide physical scheduler lock.

Native observations are sampled visibility, not cgroup enforcement. Process-tree
snapshots can miss short-lived children and do not establish exact peak usage.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import time
from pathlib import Path


def inventory(path: Path | str = ".") -> dict:
    """Observe available resources without serializing environment variables."""
    directory = Path(path).expanduser().resolve()
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    if not directory.is_dir():
        directory = directory.parent
    memory = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                memory[key] = int(value.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        available_cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available_cpus = os.cpu_count()
    disk = shutil.disk_usage(directory)
    cgroup = {}
    for name in ("memory.max", "memory.current", "cpu.max", "pids.max"):
        try:
            text = (Path("/sys/fs/cgroup") / name).read_text()[:128].strip()
            cgroup[name] = text
        except OSError:
            pass
    return {
        "system": platform.system(),
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "affinity_cpu_count": available_cpus,
        "memory_bytes": memory,
        "disk": {
            "path": str(directory),
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        },
        "container_runtimes": {name: shutil.which(name) for name in ("docker", "podman")},
        "cgroup_v2_observations": cgroup,
        "scope": "Host observations; affinity and visible cgroups may restrict available resources. Runtime presence does not establish daemon access.",
    }


def physical_lock_path() -> Path:
    """Return a private user cache location; reject a replaced final directory.

    Use open_physical_lock() for race-resistant opening of the lock itself.
    XDG_CACHE_HOME must be absolute when supplied.
    """
    configured = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured) if configured else Path.home() / ".cache"
    if not base.is_absolute():
        raise ValueError("XDG_CACHE_HOME must be absolute for the scheduler lock")
    base.mkdir(parents=True, exist_ok=True)
    directory = base / "chipflow"
    directory.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid():
            raise ValueError("Scheduler directory must belong to the current user")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    return directory / "physical.lock"


def open_physical_lock():
    """Open a regular, owned, unlinked-by-others lock file without following links.

    The caller owns the returned stream and applies flock/nonblocking queue logic.
    Pass its descriptor to the command guardian to retain serialization throughout
    child cleanup. This serializes cooperating Chipflow workers for this user only.
    """
    path = physical_lock_path()
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Unsafe scheduler directory permissions")
        descriptor = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Scheduler lock must be a private regular file")
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "a+")
        descriptor = None
        return stream
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def sample_process_tree(pid: int, *, max_processes: int = 4096, max_scan: int = 32768) -> dict:
    """Sample Linux procfs RSS and cumulative user/system CPU for a process tree.

    No command names, arguments or environment values are read or returned. CPU
    seconds are cumulative for live sampled processes, not a utilization percent.
    RSS sums can double-count shared pages. PID changes during sampling are possible.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if not 1 <= max_processes <= 65536 or not 1 <= max_scan <= 262144:
        raise ValueError("invalid process sampling budget")
    if platform.system() != "Linux":
        return {"status": "unsupported", "processes": 0, "rss_bytes": None, "cpu_seconds": None}
    records = {}
    truncated = False
    try:
        with os.scandir("/proc") as entries:
            scanned = 0
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                scanned += 1
                if scanned > max_scan:
                    truncated = True
                    break
                try:
                    with open(f"/proc/{entry.name}/stat", "rb") as stream:
                        raw = stream.read(8192)
                    fields = raw.rsplit(b") ", 1)[1].split()
                    records[int(entry.name)] = (
                        int(fields[1]),
                        int(fields[11]) + int(fields[12]),
                        max(0, int(fields[21])),
                    )
                except (OSError, IndexError, ValueError):
                    continue
    except OSError:
        return {"status": "unavailable", "processes": 0, "rss_bytes": None, "cpu_seconds": None}
    selected = {pid} if pid in records else set()
    children = {}
    for child, (parent, _, _) in records.items():
        children.setdefault(parent, []).append(child)
    queue = list(selected)
    while queue:
        parent = queue.pop()
        for child in children.get(parent, []):
            if child in selected:
                continue
            if len(selected) >= max_processes:
                truncated = True
                break
            selected.add(child)
            queue.append(child)
    return {
        "status": "sampled" if selected else "unavailable",
        "sampled_at": time.time(),
        "processes": len(selected),
        "truncated": truncated,
        "rss_bytes": sum(records[item][2] for item in selected) * os.sysconf("SC_PAGE_SIZE")
        if selected
        else None,
        "cpu_seconds": sum(records[item][1] for item in selected) / os.sysconf("SC_CLK_TCK")
        if selected
        else None,
        "scope": "Live procfs snapshot; shared RSS can be counted more than once; short-lived processes may be missed.",
    }
