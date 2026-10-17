"""Adapters and identity policies; Docker acceptance is separately recorded."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from chipflow.execution import ExecutionConfig, cleanup_container, prepare, probe_command
from chipflow.provenance import compare, enforce_comparison, sha256


def test_native_preserves_command_and_explicit_environment(tmp_path):
    cfg = ExecutionConfig(environment={"DECLARED": "setting"})
    spec = prepare(cfg, [sys.executable, "-V"], tmp_path, {"PATH": "existing"}, tmp_path)
    assert spec.argv == [sys.executable, "-V"]
    assert spec.env == {"PATH": "existing", "DECLARED": "setting"}
    assert spec.guardian_args == []
    result = probe_command(ExecutionConfig(), [sys.executable, "-V"], tmp_path)
    assert result["status"] == "available"
    assert "Python" in result["output"]


def test_container_requires_digest_and_bounded_resources():
    with pytest.raises(ValidationError, match="digest"):
        ExecutionConfig(backend="container", image="openroad/orfs:latest")
    with pytest.raises(ValidationError):
        ExecutionConfig(cpus=0)
    with pytest.raises(ValidationError):
        ExecutionConfig(environment={"invalid-name": "x"})
    with pytest.raises(ValidationError):
        ExecutionConfig(unrecognized="x")


def test_container_is_restricted_and_has_guardian_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr("chipflow.execution.shutil.which", lambda name: "/usr/bin/" + name)
    orfs = tmp_path / "orfs"
    orfs.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    cfg = ExecutionConfig(backend="container", image="openroad/orfs@sha256:" + "a" * 64)
    spec = prepare(cfg, ["yosys", "-V"], run, {"SECRET": "do-not-forward"}, run, orfs)
    assert "--network=none" in spec.argv and "--read-only" in spec.argv
    assert "--cap-drop=ALL" in spec.argv and "--pull=never" in spec.argv
    assert "--privileged" not in spec.argv and "do-not-forward" not in " ".join(spec.argv)
    assert f"type=bind,src={orfs},dst={orfs},readonly" in spec.argv
    assert f"type=bind,src={run},dst={run}" in spec.argv
    assert spec.argv[-3:] == ["yosys", cfg.image, "-V"]
    assert spec.guardian_args[0] == "--container-runtime"
    assert spec.guardian_args[3].startswith("chipflow-")


def test_mount_boundary_and_cleanup_names(tmp_path, monkeypatch):
    monkeypatch.setattr("chipflow.execution.shutil.which", lambda name: "/usr/bin/" + name)
    cfg = ExecutionConfig(backend="container", image="example/eda@sha256:" + "b" * 64)
    with pytest.raises(ValueError, match="outside declared"):
        prepare(cfg, ["true"], tmp_path.parent, {}, tmp_path)
    with pytest.raises(ValueError, match="cleanup"):
        cleanup_container("/usr/bin/docker", "unrelated-container")
    with pytest.raises(ValueError, match="cleanup"):
        cleanup_container("/bin/arbitrary", "chipflow-" + "a" * 32)


def manifest():
    return {
        "chipflow": {"version": "test"},
        "execution": {"backend": "native"},
        "image": None,
        "tools": {"yosys": {"status": "available", "sha256": "one"}},
        "orfs": {"revision": "revision"},
        "platform": {"files_sha256": {"config.mk": "x"}},
        "settings": {"threads": 2},
        "inputs_sha256": {},
        "external_mounts": {},
        "target": "sim",
    }


def test_reproducibility_differences_and_missing_evidence():
    original = manifest()
    current = manifest()
    assert compare(original, current)["compatible"]
    current["tools"]["yosys"]["sha256"] = "changed"
    result = compare(original, current)
    assert not result["compatible"]
    assert result["differences"][0]["field"] == "tools.yosys.sha256"
    assert enforce_comparison(result, "warn")["policy"] == "warn"
    with pytest.raises(ValueError, match="Strict reproducibility"):
        enforce_comparison(result, "strict")
    assert not compare(None, current)["compatible"]
    current["execution"]["environment_names"] = ["TOKEN"]
    assert any("environment" in x for x in compare(original, current)["unknown"])


def test_hash_detects_modified_content(tmp_path):
    path = tmp_path / "input.v"
    path.write_bytes(b"module a; endmodule")
    original = sha256(path)
    path.write_bytes(b"module b; endmodule")
    assert sha256(path) != original


def test_guardian_cleans_container_on_normal_exit(tmp_path, monkeypatch):
    from chipflow.runner import supervise

    read_fd, write_fd = os.pipe()
    cleaned = []
    monkeypatch.setattr("chipflow.execution.cleanup_container", lambda *args: cleaned.append(args))
    name = "chipflow-" + "a" * 32
    try:
        assert supervise([sys.executable, "-c", "pass"], read_fd, 5, "/usr/bin/docker", name) == 0
    finally:
        os.close(write_fd)
    assert cleaned == [("/usr/bin/docker", name)]


@pytest.mark.skipif(
    not os.environ.get("CHIPFLOW_TEST_CONTAINER_IMAGE"),
    reason="Set a reviewed local digest-pinned EDA image for real container acceptance",
)
def test_real_container_simulation_synthesis_and_orphan_cleanup(tmp_path):
    """Real EDA acceptance; image must provide Icarus/Yosys or an explicit mount."""
    import json
    import subprocess

    image = os.environ["CHIPFLOW_TEST_CONTAINER_IMAGE"]
    tools = os.environ.get("CHIPFLOW_TEST_CONTAINER_TOOLS", "")
    mounts = [str(Path(tools).resolve().parent)] if tools else []
    cfg = ExecutionConfig(backend="container", image=image, readonly_mounts=mounts)
    (tmp_path / "dut.v").write_text("module dut(input a, output y); assign y=~a; endmodule\n")
    (tmp_path / "tb.v").write_text(
        "module tb; reg a=0; wire y; dut d(a,y); initial begin "
        '$dumpfile("wave.vcd"); $dumpvars(0,tb); #1; if(y!==1) $fatal; '
        'a=1; #1; if(y!==0) $fatal; $display("PASS_REAL_CONTAINER"); $finish; end endmodule\n'
    )

    def execute(argv, disconnect=False):
        spec = prepare(cfg, argv, tmp_path, os.environ.copy(), tmp_path)
        read_fd, write_fd = os.pipe()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "chipflow.runner",
                "--parent-fd",
                str(read_fd),
                "--timeout",
                "30",
                *spec.guardian_args,
                "--",
                *spec.argv,
            ],
            cwd=tmp_path,
            env=spec.env,
            pass_fds=(read_fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        os.close(read_fd)
        try:
            if disconnect:
                import time

                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    present = subprocess.run(
                        [spec.guardian_args[1], "inspect", spec.guardian_args[3]],
                        capture_output=True,
                    )
                    if present.returncode == 0:
                        break
                    time.sleep(0.1)
                else:
                    pytest.fail("Container did not start")
                os.close(write_fd)
                write_fd = None
            output, _ = process.communicate(timeout=45)
            return process.returncode, output, spec
        finally:
            if write_fd is not None:
                os.close(write_fd)
            if process.poll() is None:
                process.kill()
                process.wait()

    def tool(name):
        return str(Path(tools) / name) if tools else name

    code, output, _ = execute(
        [tool("iverilog"), "-g2012", "-s", "tb", "-o", "sim.vvp", "dut.v", "tb.v"]
    )
    assert code == 0, output
    code, output, _ = execute([tool("vvp"), "sim.vvp"])
    assert code == 0 and "PASS_REAL_CONTAINER" in output
    assert (tmp_path / "wave.vcd").stat().st_size > 0
    code, output, _ = execute(
        [tool("yosys"), "-p", "read_verilog dut.v; synth -top dut; write_json netlist.json"]
    )
    assert code == 0, output
    assert json.loads((tmp_path / "netlist.json").read_text())["modules"]["dut"]["cells"]
    code, output, spec = execute(["/bin/sleep", "30"], disconnect=True)
    assert code == 125, output
    remaining = subprocess.run(
        [spec.guardian_args[1], "inspect", spec.guardian_args[3]], capture_output=True
    )
    assert remaining.returncode != 0, "Orphaned EDA container remained after guardian cleanup"


def test_explicit_mount_content_changes_are_detected(tmp_path):
    from chipflow.provenance import directory_identity

    (tmp_path / "library.lib").write_text("original")
    original = directory_identity(tmp_path)
    (tmp_path / "library.lib").write_text("different")
    assert original["sha256"] != directory_identity(tmp_path)["sha256"]
    (tmp_path / "cycle").symlink_to(tmp_path, target_is_directory=True)
    assert directory_identity(tmp_path)["status"] == "incomplete"
