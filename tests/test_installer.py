"""Installation choices must remain read-only until the terminal user approves."""

import json
import subprocess
import sys

import pytest

from chipflow import installer
from chipflow.cli import main
from chipflow.environment import read_settings, settings_path
from chipflow.setup import assess


@pytest.fixture
def missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ORFS_HOME", raising=False)
    monkeypatch.delenv("FLOW_HOME", raising=False)
    monkeypatch.setattr("chipflow.setup.find_tool", lambda *a: None)
    monkeypatch.setattr(
        "chipflow.setup.host_info",
        lambda: dict(system="Linux", distribution="ubuntu", version="24.04", architecture="x86_64"),
    )
    monkeypatch.setattr(installer.shutil, "which", lambda name, **kw: "/usr/bin/" + name)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    return assess(orfs=str(tmp_path / "absent"), versions=False)


def terminal(monkeypatch, answers):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    responses = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))


def test_catalog_lists_alternatives_without_executing(missing, monkeypatch):
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *a, **kw: pytest.fail("must not execute")
    )
    options = {item["id"]: item for item in installer.installation_options(missing)}
    assert options["system-iverilog"]["provides"] == ["iverilog", "vvp"]
    assert options["brew-iverilog"]["available"]
    assert options["orfs-source"]["available"]
    assert options["prebuilt-openroad"]["available"]
    assert options["oss-cad-suite"]["available"]
    assert not settings_path().exists()


@pytest.mark.parametrize(
    ("distro", "manager", "flags"),
    [
        ("ubuntu", "apt-get", ["install"]),
        ("debian", "apt-get", ["install"]),
        ("fedora", "dnf", ["install"]),
        ("arch", "pacman", ["-S", "--needed"]),
        ("opensuse-leap", "zypper", ["install"]),
    ],
)
def test_distribution_plan_uses_argument_lists(missing, distro, manager, flags):
    missing["host"]["distribution"] = distro
    plan = installer.build_plan(missing, "system-iverilog")
    assert plan["steps"][-1]["argv"] == ["sudo", "--", manager, *flags, "iverilog"]
    assert not settings_path().exists()


def test_unknown_choices_and_unsupported_hosts_cannot_install(missing):
    with pytest.raises(ValueError, match="Unknown"):
        installer.build_plan(missing, "system-iverilog; touch /tmp/injected")
    missing["host"]["system"] = "Darwin"
    assert not any(o["available"] for o in installer.installation_options(missing))
    with pytest.raises(ValueError, match="Linux"):
        installer.build_plan(missing, "system-iverilog")


def test_declining_and_eof_do_not_execute_or_save(missing, monkeypatch):
    terminal(monkeypatch, ["", ""])
    monkeypatch.setattr(installer, "execute_plan", lambda *a: pytest.fail("declined"))
    installer.interactive_setup(missing, option_id="system-iverilog")
    assert not settings_path().exists()
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
    assert installer.ask("anything") == ""


def test_approved_install_reassesses_and_retains_explicit_paths(missing, monkeypatch):
    terminal(monkeypatch, ["yes"])
    expected_orfs = missing["orfs"]
    missing["bin_dirs"] = ["/opt/custom/bin"]
    executed = []
    monkeypatch.setattr(installer, "execute_plan", lambda plan: executed.append(plan))

    def reassess(**kw):
        assert executed
        assert kw["orfs"] == expected_orfs
        assert kw["bin_dirs"] == ["/opt/custom/bin"]
        return {**missing, "ready": True}

    monkeypatch.setattr("chipflow.setup.assess", reassess)
    assert installer.interactive_setup(missing, option_id="system-iverilog")["ready"]
    assert len(executed) == 1


def test_failed_command_stops_without_saving_paths(missing, monkeypatch):
    calls = []

    def fail(argv, **kw):
        calls.append(argv)
        raise subprocess.CalledProcessError(2, argv)

    plan = installer.build_plan(missing, "orfs-source")
    monkeypatch.setattr(installer.subprocess, "run", fail)
    with pytest.raises(ValueError, match="Installation stopped"):
        installer.execute_plan(plan)
    assert len(calls) == 1
    assert not settings_path().exists()


def test_source_plan_pins_revision_and_never_overwrites_checkout(missing, tmp_path):
    root = tmp_path / "new" / "orfs"
    plan = installer.build_plan(missing, "orfs-source", directory=root)
    commands = [step["argv"] for step in plan["steps"]]
    assert ["git", "checkout", "--detach", installer.ORFS_REVISION] in commands
    assert ["sudo", "--", "./setup.sh"] in commands
    assert any(c[:2] == ["./build_openroad.sh", "--local"] for c in commands)
    assert not any("--latest" in c for c in commands)
    assert not root.exists()
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="already exists"):
        installer.build_plan(missing, "orfs-source", directory=root)
    assert (root / "keep.txt").read_text() == "keep"


def test_resume_checks_existing_revision_and_dirty_sources(missing, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(installer, "probe", lambda argv: "another-revision")
    with pytest.raises(ValueError, match="another revision"):
        installer.build_plan(missing, "orfs-source", directory=root)
    monkeypatch.setattr(installer, "probe", lambda argv: installer.ORFS_REVISION)
    plan = installer.build_plan(missing, "orfs-source", directory=root)
    assert plan["steps"][0]["argv"] == ["git", "diff", "--exit-code", "HEAD", "--"]
    assert not any("clone" in s["argv"] for s in plan["steps"])


def test_local_package_checks_format_identity_and_quoted_path(missing, tmp_path, monkeypatch):
    package = tmp_path / "openroad $(touch nope).deb"
    package.touch()
    monkeypatch.setattr(installer, "probe", lambda argv: "unrelated-package")
    with pytest.raises(ValueError, match="expected 'openroad'"):
        installer.build_plan(missing, "prebuilt-openroad", package=package)
    monkeypatch.setattr(installer, "probe", lambda argv: "openroad")
    plan = installer.build_plan(missing, "prebuilt-openroad", package=package)
    assert plan["steps"][0]["argv"][-1] == str(package)
    assert "'" in installer.format_plan(plan)
    assert not (tmp_path / "nope").exists()
    wrong = tmp_path / "wrong.rpm"
    wrong.touch()
    with pytest.raises(ValueError, match=".deb"):
        installer.build_plan(missing, "prebuilt-openroad", package=wrong)


def test_existing_tools_can_be_saved_without_installation(missing, tmp_path, monkeypatch):
    root = tmp_path / "custom-orfs"
    (root / "flow").mkdir(parents=True)
    (root / "flow" / "Makefile").touch()
    bins = tmp_path / "my tools"
    bins.mkdir()
    terminal(monkeypatch, [str(root), str(bins), "yes"])
    monkeypatch.setattr("chipflow.setup.assess", lambda **kw: {**missing, "ready": True})
    installer.interactive_setup(missing, option_id="existing")
    assert read_settings()["orfs"] == str(root)
    assert str(bins) in read_settings()["bin_dirs"]


def test_noninteractive_install_rejected_and_dry_run_read_only(missing, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("must not prompt"))
    monkeypatch.setattr(installer, "execute_plan", lambda *a: pytest.fail("must not install"))
    with pytest.raises(ValueError, match="interactive terminal"):
        installer.interactive_setup(missing, option_id="system-iverilog")
    installer.interactive_setup(missing, option_id="system-iverilog", dry_run=True)
    assert "apt-get install iverilog" in capsys.readouterr().out
    assert not settings_path().exists()
    assert main(["setup", "--json"]) == 1
    assert "installation_options" in json.loads(capsys.readouterr().out)
    assert main(["setup", "--install", "--json"]) == 1
    assert main(["setup", "--install", "--dry-run", "--save", "--option", "system-iverilog"]) == 1
    assert not settings_path().exists()


def test_fake_package_manager_installs_tools_and_real_probes_verify_them(tmp_path, monkeypatch):
    """Exercise the real subprocess executor and assessment, with no system changes."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    apt = bin_dir / "apt-get"
    apt.write_text(
        '#!/bin/sh\nif [ "$1" = install ]; then\n'
        "for tool in iverilog vvp; do\n"
        "printf '#!/bin/sh\\necho Icarus-test-12\\n' > \"$TEST_TOOLS/$tool\"\n"
        '/bin/chmod +x "$TEST_TOOLS/$tool"\ndone\nfi\n'
    )
    apt.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("TEST_TOOLS", str(bin_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ORFS_HOME", raising=False)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "chipflow.setup.host_info",
        lambda: dict(system="Linux", distribution="ubuntu", version="24.04", architecture="x86_64"),
    )
    report = assess(target="sim")
    assert not report["ready"]
    terminal(monkeypatch, ["yes"])
    assert installer.interactive_setup(report, option_id="system-iverilog")["ready"]
    assert not settings_path().exists()


def test_prebuilt_suite_pins_download_metadata_before_approval(missing, tmp_path, monkeypatch):
    release = dict(
        version="2026-10-17", url="https://example.invalid/suite.tgz", sha256="a" * 64, size=1234
    )
    monkeypatch.setattr(installer, "suite_release", lambda: release)
    plan = installer.build_plan(missing, "oss-cad-suite", directory=tmp_path / "suite")
    assert plan["download"]["sha256"] == "a" * 64
    assert "2026-10-17" in installer.format_plan(plan)
    assert "SHA-256" in installer.format_plan(plan)
    assert plan["bin_dirs"] == [str(tmp_path / "suite" / "bin")]
    assert not (tmp_path / "suite").exists()


def suite_archive(entries):
    import io
    import tarfile

    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
    return result.getvalue()


def download_fixture(tmp_path, monkeypatch, entries):
    import hashlib
    import io

    content = suite_archive(entries)
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(content))
    return dict(
        version="2026-10-17",
        url="https://example.invalid/archive",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        directory=str(tmp_path / "suite"),
    )


def test_verified_suite_installs_to_dedicated_directory(tmp_path, monkeypatch):
    data = download_fixture(
        tmp_path,
        monkeypatch,
        {
            "oss-cad-suite/bin/" + name: b"#!/bin/sh\necho tool\n"
            for name in ["iverilog", "vvp", "yosys"]
        },
    )
    installer.install_suite(data)
    assert (tmp_path / "suite" / "bin" / "iverilog").is_file()
    assert not list(tmp_path.glob(".chipflow-install-*"))
    with pytest.raises(ValueError, match="appeared"):
        installer.install_suite(data)


@pytest.mark.parametrize("attack", ["digest", "size", "traversal", "missing"])
def test_unverified_or_unsafe_suite_is_not_installed(tmp_path, monkeypatch, attack):
    entries = {"oss-cad-suite/bin/iverilog": b"tool"}
    if attack == "traversal":
        entries["oss-cad-suite/../../outside"] = b"bad"
    data = download_fixture(tmp_path, monkeypatch, entries)
    if attack == "digest":
        data["sha256"] = "0" * 64
    if attack == "size":
        data["size"] = 1
    with pytest.raises(ValueError, match="installation failed"):
        installer.install_suite(data)
    assert not (tmp_path / "suite").exists()
    assert not (tmp_path / "outside").exists()
    assert not list(tmp_path.glob(".chipflow-install-*"))


def test_cli_check_only_never_prompts_even_in_terminal(missing, monkeypatch, capsys):
    terminal(monkeypatch, [])
    assert main(["setup", "--check"]) == 1
    assert "FIX" in capsys.readouterr().out
    assert main(["setup", "--options"]) == 1
    assert "oss-cad-suite" in capsys.readouterr().out
    assert not settings_path().exists()


def test_default_setup_offers_choices_and_can_be_declined(missing, monkeypatch, capsys):
    terminal(monkeypatch, ["no"])
    monkeypatch.setattr(installer, "execute_plan", lambda *a: pytest.fail("declined"))
    assert main(["setup"]) == 1
    assert "FIX" in capsys.readouterr().out
    assert not settings_path().exists()


def test_new_toolchain_precedes_preserved_tool_paths(missing, tmp_path, monkeypatch):
    from chipflow.environment import write_settings

    old, new = tmp_path / "old-bin", tmp_path / "new-bin"
    old.mkdir()
    new.mkdir()
    write_settings(bin_dirs=[str(old)])
    plan = installer.build_plan(missing, "system-iverilog")
    plan["steps"] = []
    plan["bin_dirs"] = [str(new)]
    installer.execute_plan(plan)
    assert read_settings()["bin_dirs"] == [str(new), str(old)]
    source = installer.build_plan(missing, "orfs-source", directory=tmp_path / "orfs")
    assert source["bin_dirs"][0].endswith("tools/install/OpenROAD/bin")
    assert source["bin_dirs"][1].endswith("tools/install/yosys/bin")
