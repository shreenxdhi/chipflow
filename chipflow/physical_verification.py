"""Explicit native physical-check adapters with deck identity and scoped evidence.

These plans run trusted user-supplied decks. A check pass is never full physical
signoff; technology/deck qualification is a recorded user declaration, not a
Chipflow certification. No decks or restricted PDK assets are redistributed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .verification import CommandPlan

MAX_REPORT_BYTES = 32 * 1024 * 1024


class PhysicalCheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["klayout_drc", "magic_drc", "netgen_lvs"]
    pdk: str = Field(min_length=1, max_length=200)
    qualification_reference: str = Field(min_length=1, max_length=2000)
    deck: str
    deck_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # All transitive includes, technology views and setup helpers must be declared.
    dependencies: dict[str, str] = Field(default_factory=dict)
    top: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    layout: str | None = None
    layout_netlist: str | None = None
    schematic_netlist: str | None = None
    schematic_top: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    magic_drc_style: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]+$")
    timeout_seconds: int = Field(default=3600, ge=1, le=604800)

    @field_validator("pdk", "qualification_reference")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("PDK and deck qualification reference must be explicit")
        return value

    @field_validator("dependencies")
    @classmethod
    def hashes(cls, value):
        if any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in value.values()):
            raise ValueError("Every deck dependency requires its expected SHA-256")
        return value

    @model_validator(mode="after")
    def required_inputs(self):
        if self.backend in {"klayout_drc", "magic_drc"} and not self.layout:
            raise ValueError("DRC requires an explicit layout")
        if self.backend == "netgen_lvs" and (not self.layout_netlist or not self.schematic_netlist):
            raise ValueError(
                "LVS requires independently provided layout-extracted and schematic netlists"
            )
        return self


def _file(value, base):
    path = (base / Path(value).expanduser()).resolve(strict=True)
    if not path.is_file() or any(char in str(path) for char in "\0\n\r"):
        raise ValueError(f"Expected a regular check input: {path}")
    return path


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _tcl(value):
    # Tcl quoted words escape every substitution/control character. Paths are not code.
    return (
        '"'
        + str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("]", "\\]")
        + '"'
    )


def check_manifest(config: PhysicalCheckConfig | dict, *, base=None):
    config = PhysicalCheckConfig.model_validate(config)
    base = Path(base or Path.cwd()).resolve()
    deck = _file(config.deck, base)
    hashes = {str(deck): _hash(deck)}
    if hashes[str(deck)] != config.deck_sha256:
        raise ValueError(
            "Verification deck SHA-256 mismatch; review and explicitly approve its identity"
        )
    for source, expected in config.dependencies.items():
        path = _file(source, base)
        hashes[str(path)] = _hash(path)
        if hashes[str(path)] != expected:
            raise ValueError(f"Verification deck dependency SHA-256 mismatch: {path}")
    inputs = {}
    for name in ("layout", "layout_netlist", "schematic_netlist"):
        if getattr(config, name):
            path = _file(getattr(config, name), base)
            inputs[name] = {"path": str(path), "sha256": _hash(path)}
    return {
        "backend": config.backend,
        "pdk": config.pdk,
        "qualification_reference": config.qualification_reference,
        "qualification_status": "user_declared_not_independently_qualified",
        "deck": str(deck),
        "deck_sha256": config.deck_sha256,
        "deck_dependencies": hashes,
        "inputs": inputs,
        "configuration": config.model_dump(),
        "scope": "Specified local check and declared deck only; no complete foundry signoff.",
    }


def physical_check_plan(config: PhysicalCheckConfig | dict, directory: Path, *, base=None):
    """Create commands for documented concrete interfaces, not arbitrary shell hooks.

    KLayout decks must accept -rd input/report/top and write a .lyrdb marker
    database. Magic uses a .tech file and native drc count. Netgen consumes
    separately extracted layout SPICE and schematic SPICE plus its setup Tcl.
    """
    config = PhysicalCheckConfig.model_validate(config)
    manifest = check_manifest(config, base=base)
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "physical-check-manifest.json").write_text(json.dumps(manifest, indent=2))
    if config.backend == "klayout_drc":
        report = directory / "physical-check.lyrdb"
        argv = (
            "klayout",
            "-b",
            "-r",
            manifest["deck"],
            "-rd",
            f"input={manifest['inputs']['layout']['path']}",
            "-rd",
            f"report={report}",
            "-rd",
            f"top={config.top}",
        )
    elif config.backend == "magic_drc":
        report = directory / "physical-check.json"
        script = directory / "physical-check.tcl"
        lines = [
            f"tech load {_tcl(manifest['deck'])}",
            "gds readonly true",
            f"gds read {_tcl(manifest['inputs']['layout']['path'])}",
            f'if {{[lsearch -exact [cellname list all] {_tcl(config.top)}] < 0}} {{error "Expected top missing from imported GDS"}}',
            f"load {_tcl(config.top)}",
            "select top cell",
        ]
        if config.magic_drc_style:
            lines.append(f"drc style {_tcl(config.magic_drc_style)}")
        lines.extend(
            [
                "drc on",
                "drc check",
                "drc catchup",
                "set count [drc list count total]",
                'if {![string is integer -strict $count] || $count < 0} {error "Invalid native DRC count"}',
                f"set stream [open {_tcl(report)} w]",
                'puts $stream [format {{"violation_count": %d}} $count]',
                "close $stream",
                "quit -noprompt",
            ]
        )
        # A native error must stop the check before a count can be published.
        body = "\n".join(lines[:-1])
        script.write_text(
            "if {[catch {\n"
            + body
            + "\n} message]} {puts stderr $message; exit 1}\nquit -noprompt\n"
        )
        argv = ("magic", "-dnull", "-noconsole", "-rcfile", "/dev/null", str(script))
    else:
        report = directory / "physical-check.log"
        # Each circuit is one Tcl list argument. Netgen batch parses that list;
        # explicit path quoting prevents whitespace or Tcl metacharacters becoming code.
        layout = _tcl(manifest["inputs"]["layout_netlist"]["path"]) + " " + config.top
        schematic = (
            _tcl(manifest["inputs"]["schematic_netlist"]["path"])
            + " "
            + (config.schematic_top or config.top)
        )
        argv = (
            "netgen",
            "-batch",
            "lvs",
            layout,
            schematic,
            manifest["deck"],
            str(report),
            "-json",
        )
    report.unlink(missing_ok=True)
    return CommandPlan(
        config.backend,
        (argv,),
        directory,
        (report.name, "physical-check-manifest.json"),
        config.timeout_seconds,
    )


def physical_check_result(
    config: PhysicalCheckConfig | dict | None,
    directory: Path,
    returncode: int | None,
    *,
    tool_version=None,
):
    if config is None:
        return {
            "status": "not_run",
            "reason": "No explicit qualified local deck configuration provided.",
            "evidence": [],
        }
    config = PhysicalCheckConfig.model_validate(config)
    result = {
        "status": "unknown",
        "backend": config.backend,
        "tool_version": tool_version,
        "pdk": config.pdk,
        "deck": config.deck,
        "deck_sha256": config.deck_sha256,
        "qualification_reference": config.qualification_reference,
        "qualification_status": "user_declared_not_independently_qualified",
        "checks_performed": "LVS" if config.backend == "netgen_lvs" else "DRC",
        "evidence": [],
        "violations": None,
        "scope": "Specified check only; no full physical signoff or tapeout certification.",
    }
    if returncode is None:
        result.update(status="not_run", reason="Native execution not recorded.")
        return result
    if returncode:
        result.update(status="failed", reason=f"Native tool exited {returncode}; inspect its log.")
        return result
    name = {
        "klayout_drc": "physical-check.lyrdb",
        "magic_drc": "physical-check.json",
        "netgen_lvs": "physical-check.log",
    }[config.backend]
    report = directory / name
    try:
        if not report.is_file() or report.stat().st_size > MAX_REPORT_BYTES:
            raise ValueError("Native report is missing or exceeds the bounded parser size")
        text = report.read_text()
        if config.backend == "klayout_drc":
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ValueError("Report entities and DTDs are not supported")
            root = ElementTree.fromstring(text)
            if root.tag != "report-database" or root.find("items") is None:
                raise ValueError("Expected a KLayout marker report database with items")
            cells = [item.text for item in root.findall("./cells/cell/name")]
            if config.top not in cells:
                raise ValueError(
                    "Expected design cell missing from KLayout report; cannot establish check coverage"
                )
            result["violations"] = len(root.findall("./items/item"))
        elif config.backend == "magic_drc":
            count = json.loads(text).get("violation_count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("Missing or invalid native Magic DRC count")
            result["violations"] = count
        else:
            mismatch = re.search(
                r"(?im)\b(?:circuits (?:do not match|mismatch)|netlists do not match|failed to match)\b",
                text,
            )
            matched = re.search(r"(?im)^\s*Circuits match uniquely\.?\s*$", text)
            if mismatch:
                result.update(status="failed", reason="Netgen reported circuit mismatch.")
            elif matched:
                result.update(
                    status="passed",
                    reason="Netgen reported circuits match uniquely; stated setup only.",
                )
            else:
                result.update(
                    status="inconclusive",
                    reason="No unambiguous native LVS match/mismatch conclusion.",
                )
        if result["violations"] is not None:
            result.update(
                status="passed" if result["violations"] == 0 else "failed",
                reason="Native report marker/count result for declared check scope.",
            )
        result["evidence"] = [{"path": str(report), "sha256": _hash(report)}]
    except (OSError, ValueError, TypeError, ElementTree.ParseError) as exc:
        result.update(status="unknown", reason=str(exc))
    return result
