import json

import pytest

from chipflow.cli import main
from chipflow.config import load_project
from chipflow.environment import (
    bundled_example,
    discover_orfs,
    init_project,
    read_settings,
    settings_path,
    write_settings,
)
from chipflow.setup import assess, format_report


@pytest.fixture
def local_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ORFS_HOME", raising=False)
    return tmp_path


def test_missing_tools_produce_actions_without_installing(local_settings, monkeypatch):
    monkeypatch.setattr("chipflow.setup.find_tool", lambda *a: None)
    monkeypatch.setattr(
        "chipflow.setup.host_info",
        lambda: dict(system="Linux", distribution="ubuntu", version="24.04", architecture="x86_64"),
    )
    report = assess(str(local_settings / "missing"), versions=False)
    assert not report["ready"]
    assert any("apt install klayout" in check["action"] for check in report["checks"])
    assert any("same ORFS toolchain revision" in check["action"] for check in report["checks"])
    assert "FIX" in format_report(report)
    assert not settings_path().exists()


def test_simulation_does_not_require_physical_tools(local_settings, monkeypatch):
    monkeypatch.setattr(
        "chipflow.setup.find_tool",
        lambda name, *a: "/usr/bin/" + name if name in {"iverilog", "vvp"} else None,
    )
    monkeypatch.setattr(
        "chipflow.setup.host_info",
        lambda: dict(system="Linux", distribution="debian", version="12", architecture="x86_64"),
    )
    assert assess(str(local_settings / "missing"), target="sim", versions=False)["ready"]
    assert not assess(str(local_settings / "missing"), target="finish", versions=False)["ready"]


def test_settings_validate_paths_permissions_and_env_precedence(local_settings, monkeypatch):
    root = local_settings / "orfs"
    flow = root / "flow"
    (flow / "scripts").mkdir(parents=True)
    (flow / "Makefile").touch()
    bindir = local_settings / "bin"
    bindir.mkdir()
    saved = write_settings(str(root), [str(bindir)])
    assert saved.stat().st_mode & 0o777 == 0o600
    assert read_settings()["bin_dirs"] == [str(bindir)]
    assert discover_orfs() == str(root)
    monkeypatch.setenv("ORFS_HOME", str(local_settings / "override"))
    assert discover_orfs() == str(local_settings / "override")
    with pytest.raises(ValueError):
        write_settings("/missing/orfs")
    assert read_settings()["orfs"] == str(root)


def test_bundled_example_initializes_outside_source_checkout(local_settings, monkeypatch):
    monkeypatch.chdir(local_settings)
    assert bundled_example().is_file()
    config = init_project(local_settings / "project", orfs="/opt/eda/orfs")
    project, base = load_project(config)
    assert project.orfs == "/opt/eda/orfs"
    assert (base / project.rtl[0]).is_file()
    assert (base / project.simulation.testbench[0]).is_file()
    with pytest.raises(ValueError, match="already exists"):
        init_project(base)


def test_setup_json_exit_status_and_no_workspace_side_effects(local_settings, monkeypatch, capsys):
    monkeypatch.chdir(local_settings)
    monkeypatch.setattr("chipflow.setup.find_tool", lambda *a: None)
    assert main(["setup", "--target", "sim", "--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["ready"]
    assert not (local_settings / ".chipflow").exists()


def test_invalid_saved_settings_are_explained(local_settings):
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"version":1,"bin_dirs":"not-a-list"}')
    with pytest.raises(ValueError, match="bin_dirs"):
        read_settings()


def test_broken_executable_is_not_reported_ready(local_settings, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("chipflow.setup.find_tool", lambda name, *a: "/bin/" + name)
    monkeypatch.setattr(
        "chipflow.setup.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="", stderr="missing shared library", returncode=127),
    )
    report = assess(target="sim")
    assert not report["ready"]
    assert all(
        check["status"] == "broken"
        for check in report["checks"]
        if check["name"] in {"iverilog", "vvp"}
    )
    assert "Repair" in report["checks"][0]["action"]
