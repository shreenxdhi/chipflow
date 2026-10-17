"""Verification adapters: supervised command plans and evidence-based result readers.

Adapters never spawn tools themselves. The engine executes each argv with its existing
supervisor, preserving cancellation, timeouts and local-project trust boundaries.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
MAX_RESULT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Capability:
    name: str
    binaries: tuple[str, ...]
    languages: tuple[str, ...]
    waveforms: tuple[str, ...]
    coverage: str
    limitations: str


SIMULATORS = {
    "icarus": Capability(
        "icarus",
        ("iverilog", "vvp"),
        ("Verilog", "SystemVerilog subset"),
        ("vcd",),
        "No built-in functional coverage collected",
        "Icarus -g2012 support is incomplete; no universal UVM or SDF guarantee.",
    ),
    "verilator": Capability(
        "verilator",
        ("verilator", "make", "c++"),
        ("Verilog", "SystemVerilog supported subset"),
        ("vcd",),
        "Not enabled or collected by this adapter",
        "Requires Verilator 5.x --binary --timing and a C++ toolchain; two-state semantics.",
    ),
}


@dataclass(frozen=True)
class CommandPlan:
    backend: str
    commands: tuple[tuple[str, ...], ...]
    cwd: Path
    artifacts: tuple[str, ...]
    timeout_seconds: int = 60

    def execute(self, command):
        """Execute through Worker.command (or an equivalent supervisor callback)."""
        for argv in self.commands:
            command(list(argv), self.cwd, self.timeout_seconds)


@dataclass(frozen=True)
class SimulationRequest:
    top: str
    sources: tuple[str, ...]
    testbench: tuple[str, ...]
    include_dirs: tuple[str, ...] = ()
    pass_marker: str = "PASS"
    timeout_seconds: int = 60

    def __post_init__(self):
        _identifier(self.top)
        if not self.sources or not self.testbench:
            raise ValueError("explicit RTL and testbench sources are required")
        _validate_marker(self.pass_marker)
        if not 1 <= self.timeout_seconds <= 86400:
            raise ValueError("simulation timeout must be between 1 and 86400 seconds")
        for path in (*self.sources, *self.testbench, *self.include_dirs):
            _path(path)


def _validate_marker(value):
    if not value.strip() or len(value) > 4096 or any(char in value for char in "\n\r\0"):
        raise ValueError("runtime pass marker must be one nonempty line of at most 4096 characters")


def _identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("top must be a simple HDL identifier")


def _path(value):
    # Tool response/config parsers can interpret newlines, NUL and initial '-' specially.
    if not value or any(char in value for char in "\n\r\0"):
        raise ValueError("invalid source path")
    return str(Path(value).resolve())


def simulation_plan(
    request: SimulationRequest, directory: Path, backend="icarus", prefix="rtl_sim"
):
    """Create explicit argv; source ordering is retained and no shell is involved."""
    if backend not in SIMULATORS:
        raise ValueError(f"unsupported simulator: {backend}")
    if not IDENTIFIER.fullmatch(prefix):
        raise ValueError("artifact prefix must be a simple identifier")
    directory = directory.resolve()
    sources = tuple(_path(path) for path in (*request.sources, *request.testbench))
    includes = tuple(arg for path in request.include_dirs for arg in ("-I", _path(path)))
    if backend == "icarus":
        output = str(directory / f"{prefix}.vvp")
        commands = (
            ("iverilog", "-g2012", "-s", request.top, "-o", output, *includes, *sources),
            ("vvp", output),
        )
        artifacts = (f"{prefix}.vvp",)
    else:
        obj = directory / f"{prefix}_obj"
        output = f"{prefix}_bin"
        commands = (
            (
                "verilator",
                "--binary",
                "--timing",
                "--trace",
                "--top-module",
                request.top,
                "--Mdir",
                str(obj),
                "-o",
                output,
                "-j",
                "1",
                *includes,
                *sources,
            ),
            (str(obj / output),),
        )
        artifacts = (f"{prefix}_obj/{output}",)
    return CommandPlan(backend, commands, directory, artifacts, request.timeout_seconds)


def simulation_result(runtime_output: str, returncode: int | None, pass_marker="PASS"):
    """Inspect runtime output only, never compilation output or echoed commands."""
    _validate_marker(pass_marker)
    if returncode is None:
        return {"status": "not_run", "reason": "No completed simulation was recorded"}
    if returncode:
        return {"status": "failed", "reason": f"Simulator exited {returncode}"}
    if re.search(r"(?im)^\s*(?:%Error|FAIL(?:ED|URE)?|ERROR|FATAL)(?:[\s:\[]|$)", runtime_output):
        return {"status": "failed", "reason": "Runtime emitted explicit failure evidence"}
    marker_pattern = re.compile(r"^\s*" + re.escape(pass_marker) + r"(?=$|[:\s])", re.MULTILINE)
    if not marker_pattern.search(runtime_output):
        return {"status": "failed", "reason": f"Runtime pass marker {pass_marker!r} not found"}
    return {"status": "passed", "reason": "Simulation exited zero and emitted the pass marker"}


def lint_plan(top: str, sources, directory: Path, include_dirs=(), timeout_seconds=60):
    _identifier(top)
    if not sources:
        raise ValueError("lint requires explicit RTL sources")
    includes = tuple(arg for path in include_dirs for arg in ("-I", _path(path)))
    return CommandPlan(
        "verilator-lint",
        (
            (
                "verilator",
                "--lint-only",
                "--timing",
                "--top-module",
                top,
                *includes,
                *(_path(path) for path in sources),
            ),
        ),
        directory.resolve(),
        (),
        timeout_seconds,
    )


DIAGNOSTIC = re.compile(
    r"^%(?P<severity>Error|Warning)(?:-(?P<code>[A-Za-z0-9_]+))?: "
    r"(?:(?P<file>.+?):(?P<line>\d+):(?:(?P<column>\d+):)?\s*)?(?P<message>.*)$"
)


def lint_diagnostics(output: str):
    """Preserve actual Verilator messages and source locations, including summary errors."""
    diagnostics = []
    for line in output.splitlines():
        match = DIAGNOSTIC.match(line)
        if match:
            item = match.groupdict()
            item["severity"] = item["severity"].lower()
            for field in ("line", "column"):
                item[field] = int(item[field]) if item[field] else None
            diagnostics.append(item)
    return diagnostics


def junit_results(path: Path):
    """Read simulator/Cocotb JUnit evidence; absent or empty suites are not passes."""
    if not path.is_file():
        return {"status": "not_run", "tests": [], "reason": "JUnit report missing"}
    if path.stat().st_size > MAX_RESULT_BYTES:
        raise ValueError("JUnit report exceeds 16 MiB")
    data = path.read_bytes()
    if b"\0" in data or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("DTD/entity declarations are not supported in JUnit reports")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid JUnit XML") from exc
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("expected a JUnit testsuite or testsuites root")
    tests = []
    for case in root.iter("testcase"):
        failed = case.find("failure")
        error = case.find("error")
        problem = failed if failed is not None else error
        status = (
            "failed"
            if problem is not None
            else ("skipped" if case.find("skipped") is not None else "passed")
        )
        tests.append(
            {
                "name": case.get("name", "unnamed"),
                "classname": case.get("classname"),
                "status": status,
                "duration_seconds": case.get("time"),
                "message": (problem.get("message") or problem.text)
                if problem is not None
                else None,
            }
        )
    # Suite-level errors can occur without a testcase. Never ignore them.
    suite_failed = any(
        suite.get(key, "0") not in {"0", "0.0", ""}
        for suite in root.iter("testsuite")
        for key in ("errors", "failures")
    )
    status = (
        "failed"
        if suite_failed or any(t["status"] == "failed" for t in tests)
        else ("passed" if any(t["status"] == "passed" for t in tests) else "not_run")
    )
    return {"status": status, "tests": tests, "report": str(path)}


def cocotb_available():
    return importlib.util.find_spec("cocotb") is not None


def cocotb_plan(
    top,
    sources,
    test_modules,
    directory: Path,
    simulator="icarus",
    include_dirs=(),
    timeout_seconds=60,
    test_dir=None,
    seed=None,
):
    """Use an optional user-installed Cocotb runner; Python tests are trusted code."""
    _identifier(top)
    if simulator not in SIMULATORS:
        raise ValueError("Cocotb adapter supports icarus and verilator only")
    if not sources or not test_modules:
        raise ValueError("Cocotb requires RTL sources and explicit Python test modules")
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module) for module in test_modules):
        raise ValueError("invalid Python test module")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    request = {
        "top": top,
        "sources": [_path(path) for path in sources],
        "test_modules": list(test_modules),
        "simulator": simulator,
        "include_dirs": [_path(path) for path in include_dirs],
        "directory": str(directory),
        "test_dir": _path(str(test_dir)) if test_dir is not None else str(directory),
        "seed": seed,
    }
    request_path = directory / "cocotb-request.json"
    request_path.write_text(json.dumps(request, indent=2))
    return CommandPlan(
        f"cocotb-{simulator}",
        ((sys.executable, "-m", "chipflow.verification", "cocotb", str(request_path)),),
        directory,
        ("cocotb-results.xml", "cocotb-request.json"),
        timeout_seconds,
    )


def _run_cocotb(path):
    # Executed inside the existing guardian process, not the API/server process.
    request = json.loads(Path(path).read_text())
    try:
        from cocotb_tools.runner import get_runner
    except ImportError:
        try:
            from cocotb.runner import get_runner
        except ImportError as exc:
            raise RuntimeError(
                "Install compatible Cocotb in the Chipflow Python environment"
            ) from exc
    directory = Path(request["directory"])
    runner = get_runner(request["simulator"])
    runner.build(
        sources=[Path(source) for source in request["sources"]],
        includes=[Path(source) for source in request["include_dirs"]],
        hdl_toplevel=request["top"],
        build_dir=directory / "cocotb_build",
        always=True,
    )
    runner.test(
        hdl_toplevel=request["top"],
        test_module=",".join(request["test_modules"]),
        test_dir=Path(request["test_dir"]),
        seed=request["seed"],
        results_xml=str(directory / "cocotb-results.xml"),
    )
    result = junit_results(directory / "cocotb-results.xml")
    if result["status"] != "passed":
        raise RuntimeError(f"Cocotb verification {result['status']}; inspect cocotb-results.xml")


def _yosys_string(value):
    return '"' + _path(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def equivalence_plan(top: str, rtl, netlist, directory: Path, include_dirs=(), timeout_seconds=60):
    """Yosys equiv_* proof for synthesizable RTL and generic, unmapped netlists.

    Technology-mapped cells need explicit functional models in netlist; black boxes
    are rejected. Memories/async logic may be inconclusive. This is not a full EQY
    partitioning flow, sequential retiming proof, or four-state simulator equivalence.
    """
    _identifier(top)
    if not rtl or not netlist:
        raise ValueError("equivalence requires RTL and netlist inputs")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Yosys's Verilog frontend does not reliably unquote -I flags with spaces.
    # Relative aliases avoid changing source contents or dropping include paths.
    include_flags = []
    for index, path in enumerate(include_dirs):
        target = Path(_path(path))
        if not target.is_dir():
            raise ValueError(f"include directory missing: {target}")
        alias = directory / f".equivalence_include_{index}"
        if alias.exists() or alias.is_symlink():
            raise ValueError("equivalence include scratch path already occupied")
        if any(item.is_symlink() for item in target.rglob("*")):
            raise ValueError("equivalence include trees must not contain symbolic links")
        # Real copies keep backups self-contained and avoid symlinks in artifacts.
        shutil.copytree(target, alias)
        include_flags.append(f"-I{alias.name}")
    includes = " ".join(include_flags)
    lines = []
    for name, sources in (("gold", rtl), ("gate", netlist)):
        lines.append(f"read_verilog -sv {includes} " + " ".join(_yosys_string(p) for p in sources))
        lines.extend(
            [
                f"hierarchy -check -top {top}",
                f"prep -top {top} -flatten",
                "check -assert",
                f"rename {top} {name}",
                f"design -stash {name}",
            ]
        )
    lines.extend(
        [
            "design -copy-from gold -as gold gold",
            "design -copy-from gate -as gate gate",
            "equiv_make gold gate equiv",
            "hierarchy -check -top equiv",
            "equiv_simple",
            "equiv_induct -seq 5",
            "equiv_status -assert",
        ]
    )
    script = directory / "equivalence.ys"
    script.write_text("\n".join(lines) + "\n")
    return CommandPlan(
        "yosys-equivalence",
        (("yosys", "-s", str(script)),),
        directory,
        ("equivalence.ys",),
        timeout_seconds,
    )


ProofStatus = Literal["proven", "failed", "inconclusive", "unsupported", "not_run"]


@dataclass(frozen=True)
class EquivalenceResult:
    status: ProofStatus
    reason: str
    evidence: tuple[str, ...] = ()

    def to_dict(self):
        return asdict(self)


def equivalence_result(output: str, returncode: int | None, evidence=None):
    evidence = (evidence,) if isinstance(evidence, str) else tuple(evidence or ())
    if returncode is None:
        return EquivalenceResult("not_run", "No completed proof was recorded", evidence)
    if returncode == 0 and "Equivalence successfully proven!" in output:
        return EquivalenceResult("proven", "Yosys proved every equivalence cell", evidence)
    if re.search(
        r"(?:syntax error|not part of the design|not supported|Can't find|not found)", output
    ):
        return EquivalenceResult(
            "unsupported", "Tool rejected the design or required models", evidence
        )
    # An unproven equiv cell is not a counterexample. Do not label it disproven.
    return EquivalenceResult(
        "inconclusive",
        "Proof did not establish equivalence; inspect the original Yosys log",
        evidence,
    )


def main():
    parser = argparse.ArgumentParser(description="Internal supervised verification adapter")
    parser.add_argument("adapter", choices=["cocotb"])
    parser.add_argument("request")
    args = parser.parse_args()
    _run_cocotb(args.request)


if __name__ == "__main__":
    main()
