"""Independent example adapter: Yosys hierarchy checking, not functional proof.

Load explicitly from this trusted Python file; no automatic plugin discovery runs
arbitrary installed code. This example is outside the packaged manager.
"""

import re
from pathlib import Path

from chipflow.verification import Capability, CommandPlan

CAPABILITY = Capability(
    "yosys-hierarchy",
    ("yosys",),
    ("Verilog", "Yosys SystemVerilog subset"),
    (),
    "No coverage",
    "Structural hierarchy only; not simulation, proof, CDC or signoff.",
)


def plan(top: str, sources: list[str], directory: Path, timeout_seconds=60):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top) or not sources:
        raise ValueError("explicit sources and a simple top identifier required")
    quoted = []
    for source in sources:
        if any(char in source for char in "\n\r\0"):
            raise ValueError("invalid source path")
        source = str(Path(source).resolve()).replace("\\", "\\\\").replace('"', '\\"')
        quoted.append('"' + source + '"')
    script = (
        "read_verilog -sv " + " ".join(quoted) + f"; hierarchy -check -top {top}; check -assert"
    )
    return CommandPlan(
        CAPABILITY.name,
        (("yosys", "-p", script),),
        directory.resolve(),
        (),
        timeout_seconds,
    )
