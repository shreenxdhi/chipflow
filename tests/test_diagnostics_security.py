from pathlib import Path

import pytest

from chipflow.diagnostics import diagnose_log


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("write failed: No space left on device", "disk_exhaustion"),
        ("fatal: Cannot allocate memory", "out_of_memory"),
        ("Chipflow: command exceeded 10s.", "timeout"),
        ("include file defs.vh not found", "missing_include"),
        ("Unknown module type: missing_cell", "missing_module"),
        ("Cannot read liberty file views/missing.lib", "missing_library"),
        ("ERROR create_clock invalid period", "invalid_constraints"),
        ("rtl/uart.sv:42:3: syntax error", "syntax"),
        ("failed to legalize placement", "placement"),
        ("routing congestion detected", "routing"),
        ("clock tree synthesis failed", "clock_tree"),
        ("required output is missing or empty: 6_final.gds", "missing_output"),
        ("assertion failed: fifo underflow", "assertion"),
        ("hold violation detected", "timing"),
        ("[ERROR STA-001] bad pin", "tool_error"),
    ],
)
def test_diagnostic_evidence(tmp_path, text, category):
    path = tmp_path / "console.log"
    path.write_text("tool starting\n" + text + "\n")
    result = diagnose_log(path)
    assert result["advisory_only"] is True
    assert result["findings"][0]["category"] == category
    evidence = result["findings"][0]["evidence"]
    assert evidence["log"] == str(path)
    assert evidence["line"] == 2
    assert evidence["text"] == text
    assert result["findings"][0]["steps"]
    if category == "syntax":
        assert evidence["source"] == {"path": "rtl/uart.sv", "line": 42, "column": 3}


def test_tail_is_bounded_and_never_claims_absolute_line(tmp_path):
    path = tmp_path / "console.log"
    path.write_bytes(b"x" * 5000 + b"\nERROR: problem\n")
    result = diagnose_log(path, max_bytes=128)
    assert result["bytes_read"] == 128
    assert result["truncated"] is True
    assert result["findings"][0]["evidence"]["line"] is None
    assert result["findings"][0]["evidence"]["tail_line"] == 1


def test_findings_and_evidence_are_bounded(tmp_path):
    path = tmp_path / "console.log"
    path.write_text(("ERROR: " + "x" * 10000 + "\n") * 5)
    result = diagnose_log(path, max_findings=2)
    assert len(result["findings"]) == 2
    assert result["findings_truncated"] is True
    assert all(len(item["evidence"]["text"]) <= 1000 for item in result["findings"])


def test_partial_tail_unicode_and_html_are_data(tmp_path):
    path = tmp_path / "console.log"
    path.write_bytes(b"ERROR: old\n" + b"x" * 100 + b"\nERROR: <script>alert(1)</script> \xff\n")
    result = diagnose_log(path, max_bytes=80)
    assert len(result["findings"]) == 1
    assert "<script>" in result["findings"][0]["evidence"]["text"]
    assert "\ufffd" in result["findings"][0]["evidence"]["text"]


def test_no_invented_success_or_failure(tmp_path):
    path = tmp_path / "console.log"
    path.write_text("Flow completed\nWNS 0.2\nPASS\n")
    result = diagnose_log(path)
    assert result["findings"] == []
    assert "passed" not in result
    assert not result["truncated"]
    path.write_text("x" * 500)
    assert diagnose_log(path, max_bytes=10)["findings"] == []


@pytest.mark.parametrize(
    "kwargs",
    [{"max_bytes": 0}, {"max_bytes": 20_000_000}, {"max_findings": 0}, {"max_findings": 1001}],
)
def test_invalid_limits(tmp_path, kwargs):
    with pytest.raises(ValueError):
        diagnose_log(tmp_path / "not-needed.log", **kwargs)


def test_missing_log_is_not_silently_successful(tmp_path):
    with pytest.raises(FileNotFoundError):
        diagnose_log(Path(tmp_path) / "missing.log")


def test_restore_bounds_tar_table_before_publication(tmp_path, monkeypatch):
    import importlib
    import io
    import tarfile

    module = importlib.import_module("chipflow.backup")
    from chipflow.engine import Store

    source = tmp_path / "many.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        for index in range(5):
            info = tarfile.TarInfo("a" * 32 + f"/file{index}")
            archive.addfile(info, io.BytesIO(b""))
    monkeypatch.setattr(module, "MAX_ARCHIVE_MEMBERS", 2)
    monkeypatch.setattr(
        tarfile.TarFile, "getmembers", lambda self: pytest.fail("unbounded table read")
    )
    store = Store(tmp_path / "restored")
    with pytest.raises(ValueError, match="metadata limits"):
        module.restore(store, source)
    assert [path.name for path in store.root.iterdir()] == [".store.lock"]
    assert not list(tmp_path.glob(".chipflow-restore-*"))


@pytest.mark.parametrize("limit", ["MAX_ARCHIVE_METADATA_BYTES", "MAX_MEMBER_PATH_BYTES"])
def test_restore_bounds_archive_path_metadata(tmp_path, monkeypatch, limit):
    import importlib
    import tarfile

    module = importlib.import_module("chipflow.backup")
    from chipflow.engine import Store

    source = tmp_path / "long-path.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        archive.addfile(tarfile.TarInfo("a" * 32 + "/very-long-name"))
    monkeypatch.setattr(module, limit, 8)
    store = Store(tmp_path / "restored")
    with pytest.raises(ValueError, match="metadata limits"):
        module.restore(store, source)
    assert [path.name for path in store.root.iterdir()] == [".store.lock"]


def test_installer_bounds_tar_table_before_extraction(tmp_path, monkeypatch):
    import hashlib
    import io
    import tarfile

    from chipflow import installer

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for index in range(5):
            archive.addfile(tarfile.TarInfo(f"oss-cad-suite/file{index}"))
    content = data.getvalue()
    monkeypatch.setattr(installer, "MAX_ARCHIVE_MEMBERS", 2)
    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(content)
    )
    monkeypatch.setattr(
        tarfile.TarFile, "getmembers", lambda self: pytest.fail("unbounded table read")
    )
    plan = {
        "version": "test",
        "url": "https://example.invalid/",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "directory": str(tmp_path / "suite"),
    }
    with pytest.raises(ValueError, match="extraction limits"):
        installer.install_suite(plan)
    assert not (tmp_path / "suite").exists()
    assert not list(tmp_path.glob(".chipflow-install-*"))
