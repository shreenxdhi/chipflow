import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from chipflow.inspection import gds_plan, inspect_gds, netlist_plan, netlist_result
from chipflow.physical_verification import (
    PhysicalCheckConfig,
    check_manifest,
    physical_check_plan,
    physical_check_result,
)


def config(tmp_path, backend="klayout_drc"):
    deck = tmp_path / "deck with spaces.drc"
    deck.write_text("# user-supplied technology-specific deck")
    layout = tmp_path / "layout.gds"
    layout.write_bytes(b"not a real gds; plan tests only")
    return dict(
        backend=backend,
        pdk="example-test-pdk",
        qualification_reference="Test fixture; not qualified",
        deck=str(deck),
        deck_sha256=hashlib.sha256(deck.read_bytes()).hexdigest(),
        top="dut",
        layout=str(layout),
    )


def test_no_deck_is_not_run_not_signoff(tmp_path):
    result = physical_check_result(None, tmp_path, None)
    assert result["status"] == "not_run"
    assert not result["evidence"]


def test_deck_identity_and_declared_dependency_integrity(tmp_path):
    data = config(tmp_path)
    dependency = tmp_path / "helper.lydrc"
    dependency.write_text("helper")
    data["dependencies"] = {str(dependency): hashlib.sha256(dependency.read_bytes()).hexdigest()}
    manifest = check_manifest(data)
    assert manifest["deck_sha256"] == data["deck_sha256"]
    assert manifest["qualification_status"] == "user_declared_not_independently_qualified"
    assert manifest["inputs"]["layout"]["sha256"]
    dependency.write_text("changed")
    with pytest.raises(ValueError, match="dependency SHA-256 mismatch"):
        check_manifest(data)
    data["dependencies"] = {}
    Path(data["deck"]).write_text("different deck")
    with pytest.raises(ValueError, match="deck SHA-256 mismatch"):
        check_manifest(data)


@pytest.mark.parametrize(
    "overrides",
    [
        {"deck_sha256": "invalid"},
        {"pdk": " "},
        {"qualification_reference": ""},
        {"backend": "run_shell"},
        {"top": "dut; exec bad"},
        {"layout": None},
        {"backend": "netgen_lvs"},
        {"dependencies": {"a": "bad"}},
    ],
)
def test_invalid_or_unqualified_configuration_rejected(tmp_path, overrides):
    with pytest.raises(ValidationError):
        PhysicalCheckConfig.model_validate({**config(tmp_path), **overrides})


def test_klayout_plan_argv_deck_contract_and_stale_report_cleanup(tmp_path):
    data = config(tmp_path)
    folder = tmp_path / "check"
    folder.mkdir()
    (folder / "physical-check.lyrdb").write_text("stale pass")
    plan = physical_check_plan(data, folder)
    assert plan.commands[0][:3] == ("klayout", "-b", "-r")
    assert plan.commands[0][3] == data["deck"]
    assert "-rd" in plan.commands[0]
    assert not (folder / "physical-check.lyrdb").exists()
    assert (
        json.loads((folder / "physical-check-manifest.json").read_text())["configuration"]["top"]
        == "dut"
    )


@pytest.mark.parametrize("items,status", [("", "passed"), ("<item/>", "failed")])
def test_klayout_native_report_parser(items, status, tmp_path):
    data = config(tmp_path)
    (tmp_path / "physical-check.lyrdb").write_text(
        f"<report-database><cells><cell><name>dut</name></cell></cells><items>{items}</items></report-database>"
    )
    result = physical_check_result(data, tmp_path, 0, tool_version="test fixture")
    assert result["status"] == status
    assert result["violations"] == (1 if items else 0)
    assert result["evidence"][0]["sha256"]
    assert "no full physical signoff" in result["scope"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "<unrelated/>",
        "<report-database><items/></report-database>",
        '<!DOCTYPE foo [<!ENTITY x "bar">]><report-database><items/></report-database>',
    ],
)
def test_missing_coverage_or_invalid_native_report_never_passes(tmp_path, text):
    (tmp_path / "physical-check.lyrdb").write_text(text)
    assert physical_check_result(config(tmp_path), tmp_path, 0)["status"] == "unknown"


def test_magic_plan_has_explicit_tech_and_failure_guard(tmp_path):
    data = config(tmp_path, "magic_drc")
    plan = physical_check_plan(data, tmp_path / "check")
    assert plan.commands[0][:4] == ("magic", "-dnull", "-noconsole", "-rcfile")
    script = (plan.cwd / "physical-check.tcl").read_text()
    assert "tech load" in script
    assert "cellname list all" in script
    assert "catch" in script and "exit 1" in script
    assert "drc catchup" in script and "drc list count total" in script
    (plan.cwd / "physical-check.json").write_text('{"violation_count":0}')
    assert physical_check_result(data, plan.cwd, 0)["status"] == "passed"
    assert physical_check_result(data, plan.cwd, 1)["status"] == "failed"
    (plan.cwd / "physical-check.json").write_text('{"violation_count":true}')
    assert physical_check_result(data, plan.cwd, 0)["status"] == "unknown"


def lvs_config(tmp_path):
    data = config(tmp_path, "netgen_lvs")
    for name in ("layout_netlist", "schematic_netlist"):
        path = tmp_path / f"{name}.spice"
        path.write_text("* plan fixture, not a native LVS validation")
        data[name] = str(path)
    return data


@pytest.mark.parametrize(
    "text,status",
    [
        ("Circuits match uniquely.\n", "passed"),
        ("Circuits do not match.\n", "failed"),
        ("No execution summary\n", "inconclusive"),
        ("Circuits match uniquely.\nNetlists do not match.\n", "failed"),
    ],
)
def test_netgen_explicit_match_mismatch_and_inconclusive(tmp_path, text, status):
    data = lvs_config(tmp_path)
    plan = physical_check_plan(data, tmp_path / "check")
    assert plan.commands[0][:3] == ("netgen", "-batch", "lvs")
    (plan.cwd / "physical-check.log").write_text(text)
    assert physical_check_result(data, plan.cwd, 0)["status"] == status


def test_native_yosys_structural_inspection_and_negative_top(tmp_path):
    yosys = shutil.which("yosys")
    if not yosys:
        pytest.skip("native Yosys unavailable")
    source = tmp_path / "netlist.v"
    source.write_text("module dut(input a, output y); assign y = a; endmodule\n")
    plan = netlist_plan(source, "dut", tmp_path / "good")
    process = subprocess.run(plan.commands[0], cwd=plan.cwd, capture_output=True, text=True)
    result = netlist_result(plan.cwd / "inspection-netlist.json", "dut", process.returncode)
    assert result["status"] == "passed", process.stdout + process.stderr
    plan = netlist_plan(source, "missing_top", tmp_path / "bad")
    process = subprocess.run(plan.commands[0], cwd=plan.cwd, capture_output=True, text=True)
    assert (
        netlist_result(plan.cwd / "inspection-netlist.json", "missing_top", process.returncode)[
            "status"
        ]
        == "failed"
    )


def test_native_yosys_unresolved_cell_fails(tmp_path):
    if not shutil.which("yosys"):
        pytest.skip("native Yosys unavailable")
    source = tmp_path / "unresolved.v"
    source.write_text("module dut(input a, output y); missing cell(.a(a),.y(y)); endmodule")
    plan = netlist_plan(source, "dut", tmp_path / "check")
    process = subprocess.run(plan.commands[0], cwd=plan.cwd, capture_output=True, text=True)
    assert process.returncode != 0
    assert (
        netlist_result(plan.cwd / "inspection-netlist.json", "dut", process.returncode)["status"]
        == "failed"
    )


def test_netlist_paths_cannot_escape_yosys_quoted_string(tmp_path):
    source = tmp_path / 'quoted"; bad.v'
    source.write_text("module dut; endmodule")
    plan = netlist_plan(source, "dut", tmp_path / "check")
    assert '\\"; bad.v"' in (plan.cwd / "inspect-netlist.ys").read_text()
    with pytest.raises(ValueError):
        netlist_plan(source, "dut; shell bad", tmp_path / "bad")


def test_native_klayout_layout_read_top_bbox_and_corruption(tmp_path):
    db = pytest.importorskip("klayout.db", reason="optional native KLayout package unavailable")
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("dut")
    top.shapes(layout.layer(1, 0)).insert(db.Box(0, 0, 1000, 2000))
    path = tmp_path / "real.gds"
    layout.write(str(path))
    result = inspect_gds(path, "dut")
    assert result["status"] == "passed", result
    assert result["top_cells"] == ["dut"]
    assert result["cell_count"] == 1
    assert result["top_metadata"][0]["bbox_dbu"] == [0, 0, 1000, 2000]
    assert inspect_gds(path, "wrong_top")["status"] == "failed"
    bad = tmp_path / "broken.gds"
    bad.write_bytes(b"\x00\x06\x00\x02fake header is insufficient")
    assert inspect_gds(bad, "dut")["status"] == "failed"


def test_native_klayout_empty_geometry_fails(tmp_path):
    db = pytest.importorskip("klayout.db", reason="optional native KLayout package unavailable")
    layout = db.Layout()
    layout.create_cell("dut")
    path = tmp_path / "empty.gds"
    layout.write(str(path))
    assert inspect_gds(path, "dut")["status"] == "failed"


def test_missing_klayout_reports_unavailable_not_pass(monkeypatch, tmp_path):
    source = tmp_path / "anything.gds"
    source.write_bytes(b"data")
    monkeypatch.setattr("chipflow.inspection.importlib.util.find_spec", lambda name: None)
    monkeypatch.setattr("chipflow.inspection.find_tool", lambda name: None)
    assert gds_plan(source, tmp_path / "check") is None
    assert inspect_gds(source)["status"] == "unavailable"


def test_native_layoutview_preview_real_png_top_and_source_coordinates(tmp_path):
    db = pytest.importorskip("klayout.db")
    pytest.importorskip("klayout.lay")
    from chipflow.inspection import preview_gds

    layout = db.Layout()
    top = layout.create_cell("dut")
    top.shapes(layout.layer(10, 0)).insert(db.Box(-10000, -2000, 3000, 8000))
    source = tmp_path / "source.gds"
    layout.write(str(source))
    image = tmp_path / "preview.png"
    result = preview_gds(source, image, "dut")
    assert result["status"] == "passed", result
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result["renderer"] == "KLayout LayoutView"
    assert result["rendered_top"] == "dut"
    assert result["top_metadata"][0]["bbox_dbu"] == [-10000, -2000, 3000, 8000]
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert preview_gds(source, tmp_path / "wrong.png", "unknown_top")["status"] == "failed"
    assert not (tmp_path / "wrong.png").exists()
    with pytest.raises(ValueError):
        preview_gds(source, source, "dut")
