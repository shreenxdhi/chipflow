"""Conservative SDC text inventory. Never an SDC interpreter or timing proof.

Authoritative constraint coverage requires OpenSTA/OpenROAD on the linked design.
This inventory never executes Tcl or assumes expressions resolved to real ports.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMMANDS = {
    "create_clock": "primary_clocks",
    "create_generated_clock": "generated_clocks",
    "set_input_delay": "input_delays",
    "set_output_delay": "output_delays",
    "set_clock_uncertainty": "clock_uncertainty",
    "set_clock_latency": "clock_latency",
    "set_input_transition": "input_transition",
    "set_load": "output_load",
    "set_false_path": "false_paths",
    "set_multicycle_path": "multicycle_paths",
    "set_clock_groups": "clock_groups",
    "set_min_delay": "min_delays",
    "set_max_delay": "max_delays",
    "set_disable_timing": "disabled_arcs",
    "set_case_analysis": "case_analysis",
}
DYNAMIC = {
    "source",
    "eval",
    "uplevel",
    "if",
    "foreach",
    "for",
    "while",
    "proc",
    "namespace",
    "subst",
}


def _commands(text: str):
    """Split only lexical top-level command boundaries; no Tcl evaluation."""
    commands = []
    buffer = []
    brace = bracket = 0
    quote = escape = comment = False
    line = start = 1
    malformed = False
    for char in text:
        if comment:
            if char != "\n":
                continue
            comment = False
        if escape:
            buffer.append(char)
            escape = False
            if char == "\n":
                line += 1
            continue
        if char == "\\":
            buffer.append(char)
            escape = True
            continue
        if char == "#" and not "".join(buffer).strip() and not brace and not bracket and not quote:
            comment = True
            continue
        if char == '"' and not brace:
            quote = not quote
        elif not quote:
            if char == "{":
                brace += 1
            elif char == "}":
                brace -= 1
            elif not brace and char == "[":
                bracket += 1
            elif not brace and char == "]":
                bracket -= 1
            if brace < 0 or bracket < 0:
                malformed = True
        if char in {";", "\n"} and brace == bracket == 0 and not quote:
            command = "".join(buffer).strip()
            if command:
                commands.append((start, command))
            buffer = []
            start = line + (char == "\n")
        else:
            if not buffer and not char.isspace():
                start = line
            buffer.append(char)
        if char == "\n":
            line += 1
    if "".join(buffer).strip():
        commands.append((start, "".join(buffer).strip()))
    return commands, malformed or bool(brace or bracket or quote or escape)


def analyze_constraints(text: str, *, generated: bool = False) -> dict:
    """Inventory visible commands and review risks, never claim endpoint coverage.

    Names/ports are text expressions, not resolved design objects. Dynamic Tcl,
    sourced files and nested procedures deliberately remain unexpanded.
    """
    inventory = {category: [] for category in COMMANDS.values()}
    warnings = []
    statements, malformed = _commands(text)
    unknown = []
    for line, statement in statements:
        match = re.match(r"([A-Za-z_][A-Za-z0-9_:]*)\b", statement)
        name = match[1] if match else "<dynamic>"
        item = {"line": line, "command": name, "text": statement}
        if name in COMMANDS:
            inventory[COMMANDS[name]].append(item)
        else:
            unknown.append(item)
        if name in DYNAMIC or name == "<dynamic>":
            warnings.append(
                f"Line {line}: {name} requires native Tcl evaluation; nested or external constraints are not inventoried."
            )
        elif name not in COMMANDS and name not in {"set", "puts", "current_design", "set_units"}:
            warnings.append(
                f"Line {line}: {name} is outside this advisory inventory; verify support with the timing engine."
            )
        if name in {
            "set_false_path",
            "set_multicycle_path",
            "set_clock_groups",
            "set_disable_timing",
        }:
            warnings.append(
                f"Line {line}: review {name} against design intent; inventory does not prove the exception is safe."
            )
    if malformed:
        warnings.append(
            "Unbalanced or incomplete Tcl delimiters detected; validate the SDC with the native timing engine."
        )
    if not inventory["primary_clocks"] and not inventory["generated_clocks"]:
        warnings.append(
            "No top-level clock definition inventoried; clocks may be missing or defined dynamically."
        )
    for category, label in (("input_delays", "input delays"), ("output_delays", "output delays")):
        if not inventory[category]:
            warnings.append(
                f"No top-level {label} inventoried; review applicable I/O timing requirements."
            )
    if generated:
        warnings.insert(
            0,
            "Simplified Chipflow-generated clock-only constraints: I/O timing and clock relationships require engineering review.",
        )
    return {
        "kind": "advisory_static_inventory",
        "authoritative": False,
        "generated_simplified": generated,
        "commands": inventory,
        "other_commands": unknown,
        "warnings": warnings,
        "coverage": {
            "recognized_clock_ports": None,
            "clock_domain_relationships": None,
            "constrained_endpoints": None,
            "unconstrained_endpoints": None,
            "setup_analysis": "not_run",
            "hold_analysis": "not_run",
            "corners": [],
        },
        "scope": "Text inventory only. Native OpenSTA/OpenROAD analysis on the linked design is required to resolve clocks, ports, exceptions and endpoint coverage.",
    }


def inspect_constraints(path: Path | str, *, generated: bool = False) -> dict:
    path = Path(path)
    report = analyze_constraints(path.read_text(), generated=generated)
    report["source"] = str(path)
    return report


# These models cover common explicit user inputs; expert SDC remains unrestricted.


class GuidedClock(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    port: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    period: float = Field(gt=0)
    uncertainty: float | None = Field(default=None, ge=0)
    latency: float | None = Field(default=None, ge=0)


class GuidedIODelay(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    direction: Literal["input", "output"]
    ports: list[str] = Field(min_length=1)
    clock: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    minimum: float
    maximum: float

    @field_validator("ports")
    @classmethod
    def explicit_ports(cls, values):
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in values):
            raise ValueError(
                "Guided ports require simple identifiers; use expert SDC for bus collections or patterns"
            )
        if len(values) != len(set(values)):
            raise ValueError("Duplicate I/O delay port")
        return values

    @model_validator(mode="after")
    def ordered_delays(self):
        if self.minimum > self.maximum:
            raise ValueError("Minimum I/O delay must not exceed maximum")
        return self


class GuidedSDCConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time_unit: str = Field(min_length=1, max_length=40)
    capacitance_unit: str = Field(min_length=1, max_length=40)
    assumptions: list[str] = Field(min_length=1)
    clocks: list[GuidedClock] = Field(min_length=1)
    io_delays: list[GuidedIODelay] = Field(default_factory=list)
    input_transitions: dict[str, float] = Field(default_factory=dict)
    output_loads: dict[str, float] = Field(default_factory=dict)

    @field_validator("assumptions")
    @classmethod
    def nonblank_assumptions(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("Record explicit reviewed design assumptions")
        return values

    @field_validator("input_transitions", "output_loads")
    @classmethod
    def safe_values(cls, values):
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or value < 0
            for key, value in values.items()
        ):
            raise ValueError(
                "Transition/load entries need simple port names and nonnegative explicit values"
            )
        return values

    @model_validator(mode="after")
    def unique_clocks_and_io(self):
        names = {clock.name for clock in self.clocks}
        ports = {clock.port for clock in self.clocks}
        if len(names) != len(self.clocks) or len(ports) != len(self.clocks):
            raise ValueError("Clock names and ports must be unique in guided SDC")
        assignments = set()
        for delay in self.io_delays:
            if delay.clock not in names:
                raise ValueError(f"Undefined I/O delay clock: {delay.clock}")
            for port in delay.ports:
                identity = (delay.direction, port)
                if identity in assignments:
                    raise ValueError(
                        "Repeated I/O port assignment: use expert SDC for multi-clock -add_delay semantics"
                    )
                assignments.add(identity)
        return self


def build_guided_sdc(config: GuidedSDCConfig | dict) -> str:
    """Emit only explicit user values, with assumptions and unit-review warnings.

    Unit labels are declarations, not a conversion or an assertion about Liberty
    units. Native timing-engine validation must confirm they match loaded data.
    """
    config = GuidedSDCConfig.model_validate(config)
    lines = [
        "# Chipflow guided SDC: explicit user assumptions, not timing qualification.",
        "# Confirm time/capacitance units against the linked Liberty/native timing engine.",
    ]
    for label, value in (
        ("time_unit", config.time_unit),
        ("capacitance_unit", config.capacitance_unit),
    ):
        lines.extend(f"# {label}: {part}" for part in value.splitlines())
    for assumption in config.assumptions:
        lines.extend(f"# Assumption: {part}" for part in assumption.splitlines())
    for clock in config.clocks:
        lines.append(
            f"create_clock -name {clock.name} -period {clock.period:.17g} [get_ports {{{clock.port}}}]"
        )
        if clock.uncertainty is not None:
            lines.append(
                f"set_clock_uncertainty {clock.uncertainty:.17g} [get_clocks {{{clock.name}}}]"
            )
        if clock.latency is not None:
            lines.append(f"set_clock_latency {clock.latency:.17g} [get_clocks {{{clock.name}}}]")
    for delay in config.io_delays:
        for flag, value in (("min", delay.minimum), ("max", delay.maximum)):
            lines.append(
                f"set_{delay.direction}_delay -clock [get_clocks {{{delay.clock}}}] -{flag} {value:.17g} [get_ports {{{' '.join(delay.ports)}}}]"
            )
    for port, value in config.input_transitions.items():
        lines.append(f"set_input_transition {value:.17g} [get_ports {{{port}}}]")
    for port, value in config.output_loads.items():
        lines.append(f"set_load {value:.17g} [get_ports {{{port}}}]")
    if not config.io_delays:
        lines.append("# No I/O delays supplied. Review interface timing before interpreting slack.")
    lines.append(
        "# Generated clock relationships, exceptions and complete coverage require expert/native review."
    )
    return "\n".join(lines) + "\n"


def write_guided_sdc(config: GuidedSDCConfig | dict, path: Path | str) -> dict:
    """Create a new SDC exclusively; never overwrite existing engineering inputs."""
    text = build_guided_sdc(config)
    path = Path(path)
    with path.open("x") as stream:
        stream.write(text)
    result = analyze_constraints(text)
    result["source"] = str(path.resolve())
    result["user_assumptions_required"] = True
    return result
