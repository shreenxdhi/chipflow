import fcntl
import os
import stat
import subprocess
import sys
import time

import pytest

from chipflow.resources import (
    inventory,
    open_physical_lock,
    physical_lock_path,
    sample_process_tree,
)


def test_inventory_observes_host_without_environment_values(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIPFLOW_AUDIT_SECRET", "must-not-be-recorded")
    result = inventory(tmp_path / "not-created")
    assert result["disk"]["free_bytes"] > 0
    assert result["disk"]["total_bytes"] >= result["disk"]["free_bytes"]
    assert result["cpu_count"] is None or result["cpu_count"] > 0
    assert "must-not-be-recorded" not in str(result)
    assert set(result["container_runtimes"]) == {"docker", "podman"}


def test_private_user_wide_lock_contends_across_openers(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = physical_lock_path()
    with open_physical_lock() as first, open_physical_lock() as second:
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(first, fcntl.LOCK_UN)
        fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_scheduler_rejects_symlink_directory_and_lock(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    (cache / "chipflow").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        open_physical_lock()
    (cache / "chipflow").unlink()
    path = physical_lock_path()
    secret = tmp_path / "secret"
    secret.write_text("private")
    path.symlink_to(secret)
    with pytest.raises(OSError):
        open_physical_lock()
    assert secret.read_text() == "private"


def test_scheduler_rejects_hardlink_and_relative_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = physical_lock_path()
    original = tmp_path / "original"
    original.write_text("private")
    os.link(original, path)
    with pytest.raises(ValueError, match="private regular"):
        open_physical_lock()
    monkeypatch.setenv("XDG_CACHE_HOME", "relative-cache")
    with pytest.raises(ValueError, match="absolute"):
        physical_lock_path()


def test_process_snapshot_samples_real_child_tree():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; data=bytearray(1048576); time.sleep(10)"]
    )
    try:
        time.sleep(0.1)
        result = sample_process_tree(os.getpid())
        assert result["status"] == "sampled"
        assert result["processes"] >= 2
        assert result["rss_bytes"] > 0
        assert result["cpu_seconds"] >= 0
        assert "data=bytearray" not in str(result)
        limited = sample_process_tree(os.getpid(), max_processes=1)
        assert limited["processes"] == 1
        assert limited["truncated"]
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize(
    "pid,kwargs",
    [(0, {}), (True, {}), (os.getpid(), {"max_scan": 0}), (os.getpid(), {"max_processes": 0})],
)
def test_invalid_sampling_limits(pid, kwargs):
    with pytest.raises(ValueError):
        sample_process_tree(pid, **kwargs)


def test_missing_process_never_invents_zero_usage():
    result = sample_process_tree(999999999)
    assert result["status"] == "unavailable"
    assert result["rss_bytes"] is None
    assert result["cpu_seconds"] is None
