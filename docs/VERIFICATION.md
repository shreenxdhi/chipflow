# Verification adapters

Chipflow keeps tool execution, verification, timing and physical signoff separate.
A simulator exiting zero does not establish functional correctness. A generated GDS
is not qualified physical signoff. Formal equivalence is distinct from simulation.

`chipflow.verification` provides supervised command plans and evidence readers.
The engine owns process lifetime, cancellation, timeouts and log storage. Adapters
return explicit argument lists and never invoke a shell or launch a detached worker.
The same plans can be tested using real tools without replacing the supervisor.

## Simulator contract

`SimulationRequest(top, sources, testbench, include_dirs=(), pass_marker="PASS",
timeout_seconds=60)` requires explicit, ordered source paths. Names and paths are
validated. Relative paths resolve from the caller's current directory; project
integration should pass resolved snapshot paths. No source-order or top-module
inference occurs.

`simulation_plan(request, run_directory, backend="icarus", prefix="rtl_sim")`
returns a `CommandPlan` containing `commands`, `cwd`, expected executable artifacts
and per-command timeout. `plan.execute(worker.command)` uses the existing worker
supervisor. Read runtime output separately from compilation output and echoed
command lines. Call `simulation_result(runtime_output, returncode, pass_marker)`;
nonzero exit, missing marker, or explicit line-leading FAIL/FAILED/FAILURE/ERROR/FATAL
messages are failures. A marker must start an output line after optional whitespace
and be followed by a colon, whitespace or end of line; `NOT PASS` cannot pass.
An absent execution is `not_run`. Preserve the actual logs.
The pass marker is a simple-testbench convention; custom 1.x benches that print
the marker embedded inside another sentence must put it at the start of a line.
It is not
coverage evidence or a substitute for a self-checking testbench.

| Adapter | Actual command | Qualification and limitations |
| --- | --- | --- |
| Icarus | `iverilog -g2012` then `vvp` | Existing Verilog/SystemVerilog subset; requires explicit testbench. VCD supported. No universal UVM/SDF guarantee. |
| Verilator | `verilator --binary --timing --trace` then generated executable | Verilator 5.x, Make and C++ compiler required. Self-checking timed testbenches and VCD tested; two-state semantics differ from Icarus. Warnings remain enabled and can fail compilation. |
| Cocotb | Optional installed Cocotb Python runner with Icarus or Verilator | Python tests are trusted executable code. A real nonempty JUnit report is required. No bundled commercial simulator or license. |

No coverage values are synthesized. The simulator capability records explicitly
state that coverage is not enabled/collected by these adapters. Functional
coverage, UVM compatibility and SDF timing simulation remain unqualified.

## Cocotb

Install a compatible Cocotb into the same Python environment as Chipflow. The
adapter does not install anything automatically. Cocotb is optional; ordinary
Icarus/Verilator workflows do not import it.

`cocotb_plan(top, sources, test_modules, directory, simulator="icarus",
include_dirs=(), timeout_seconds=60, test_dir=None, seed=None)` writes the auditable
`cocotb-request.json`. `top` identifies the DUT, not a Verilog testbench. Modules
are explicit Python module names without `.py`; their directory must be preserved
in the run snapshot and passed as `test_dir`. The directory defaults to the run
directory. Transitive Python dependencies must be available to the selected Python
interpreter. The plan executes `python -m chipflow.verification cocotb REQUEST`
under the normal guardian and saves `cocotb-results.xml` in the run directory.

`junit_results(path)` records real test names, status, durations and failure
messages. Missing reports, empty suites and all-skipped suites are `not_run`.
Suite-level failures/errors cannot be hidden by passing individual tests. Reports
over 16 MiB, malformed XML, non-JUnit roots and DTD/entity declarations are rejected;
UTF-16 XML is unsupported. Return-code success never replaces the JUnit verdict.

The native acceptance exercise uses Cocotb 2.1.0 with Icarus, a real inverter,
explicit seed 42, a passing assertion and an intentionally failing assertion.
Cocotb/Verilator execution is not qualified by that exercise.

## RTL lint

`lint_plan(top, sources, directory, include_dirs=(), timeout_seconds=60)` runs
Verilator `--lint-only --timing`. `lint_diagnostics(log)` preserves actual severity,
warning code, source path, line, column and message. Width/latch/driver and other
categories are the tool's diagnostics, not independently inferred claims. Summary
errors without source locations are retained. Default warning behavior is not
silenced. A lint pass is not functional verification or CDC/RDC proof.

## Optional Yosys equivalence

`equivalence_plan(top, rtl, netlist, directory, include_dirs=(), timeout_seconds=60)`
saves `equivalence.ys` and invokes real Yosys. Separate gold/gate designs are
prepared, checked, flattened, combined with `equiv_make`, then checked with
`equiv_simple`, `equiv_induct -seq 5`, and `equiv_status -assert`. Inputs are original
RTL and an independently generated synthesized netlist; source ordering is explicit. Include directories are copied to safe relative
scratch names because Yosys frontend `-I` handling cannot reliably quote spaces.
Symlink-containing include trees and occupied scratch paths are rejected.

`equivalence_result(log, returncode, evidence=log_path)` returns a dataclass with
`to_dict()`. Outcomes are `proven`, `failed`, `inconclusive`, `unsupported` and
`not_run`. This adapter only emits `proven` after exit zero **and** Yosys's explicit
successful-proof message. Unproven cells are `inconclusive`, not failed: they do
not establish a counterexample. The current adapter does not produce a definitive
`failed` counterexample result. Parsing/frontend/model errors can be `unsupported`.
Evidence references must be saved with the run; caller quality policies should
require persistent proof evidence before accepting the result.

Qualification is limited to Yosys-supported synthesizable RTL and generic netlists.
Mapped netlists need explicitly supplied correct functional cell models. Black
boxes or unresolved cells fail hierarchy checks. Complex memories, asynchronous
logic, retiming and unsupported constructs can be inconclusive or unsupported.
This is not EQY's full partitioning flow, a four-state simulation equivalence
claim, or a substitute for qualified commercial equivalence. No proof claim is
made for a design/tool pairing until its real proof completes.

## Trust boundary and extension interface

RTL elaboration can read auxiliary files; Cocotb Python and native project scripts
execute code. These adapters retain Chipflow's trusted-local-project boundary and
are not an untrusted-project sandbox. They do not provide remote execution,
commercial simulator integration or physical DRC/LVS qualification.

Additional adapters should return `CommandPlan` and persistent evidence with an
explicit unknown/not-run state. Declare required binaries, supported features,
waveforms, coverage and limitations. Do not suppress diagnostics by default, infer
coverage from test counts or infer proof from an exit code alone. Test actual
positive and negative tool runs independently of plan-construction unit tests.

## Executable acceptance

```bash
python -m pytest -q tests/test_verification_adapters.py
```

Tests explicitly skip native cases when their required binaries or optional
Cocotb are unavailable. They exercise real Icarus/Verilator simulation and failed
assertions, VCD generation, Verilator width diagnostics, Yosys proof against both a
matching/mismatching design and a real synthesized sequential netlist, and optional
Cocotb pass/fail JUnit evidence. Application-only tests cover input validation,
marker failure semantics, explicit command plans and hostile/empty report handling.
No test doubles are used as evidence of an EDA adapter working.

## Independent example adapter

[examples/plugins/yosys_hierarchy.py](../examples/plugins/yosys_hierarchy.py) is a
separate, explicitly loaded adapter using the public `Capability` and `CommandPlan`
interfaces. Its `plan(top, sources, directory, timeout_seconds=60)` runs real Yosys
hierarchy/structural checks. It neither modifies the built-in simulator registry
nor requires a plugin framework. Missing modules must fail. Both real positive
and negative behavior are tested in `tests/test_reference_designs.py`.

Loading adapter Python is execution of trusted code. There is no automatic import
of arbitrary installed entrypoints, no remote execution, and no inferred commercial
simulator support. A separately configured licensed-tool adapter must declare its
own capabilities and evidence contract and undergo real licensed-host acceptance.

## Additional reference workloads

The packaged `fifo`, `multiclock` and `tiny_cpu` examples each include real RTL,
a self-checking testbench, configuration and limitations guide. Their acceptance
command is:

```bash
python -m pytest -q tests/test_reference_designs.py
```

This exercises actual Icarus tests and VCD outputs and actual Yosys synthesis.
The FIFO covers directed ordering/full/empty/overflow/underflow/wrap scenarios.
The multi-clock example checks independent counters, not clock-domain safety.
The tiny CPU is an instructional fixed-program accumulator, not a representative
large processor benchmark. These tests do not imply successful placement, routing,
timing closure, foundry DRC/LVS or GDS qualification. The example clocks-only
constraints deliberately do not claim complete I/O timing coverage.
