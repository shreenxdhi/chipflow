# Native output inspection and explicit physical checks

A nonempty file or GDS header does not establish a valid implementation. Chipflow
can inspect GDS with native KLayout, including its actual top cell, cell count,
layer metadata, database units and top-level bounding boxes. Expected top-cell
mismatch, unreadable data and empty geometry fail inspection. Install the optional
`physical` extra or make the KLayout executable available. A missing native reader
is unavailable, never passed.

KLayout's native `LayoutView` renderer produces `layout-preview.png` with
`layout-preview.json` recording the rendered top, source hash, bounding boxes and
scope. This is a full-hierarchy static preview of the selected top using native
layout data, rather than a hand-parsed approximation. It is not a layout editor,
DRC result or proof of routing correctness. The default render is 1024 by 768;
large designs can still take time, so worker plans retain timeouts/cancellation.

Netlist inspection uses actual Yosys `hierarchy -check` and `check -assert`, with
native JSON statistics and explicit top identity. Mapped cell definitions require
matching user-provided Liberty/Verilog library models. Missing models do not
establish malformed RTL; the integration marks inspection unsupported when it
cannot link the selected netlist. Structural checking is distinct from simulation,
formal equivalence and timing analysis.

## Explicit local verification deck contract

Physical-check adapters require a local deck path and expected SHA-256, a PDK
identifier and an explicit qualification reference. Record all transitive deck
includes and technology/setup dependencies with expected hashes. Input layout and
netlist hashes and configuration are saved before execution. The qualification
reference is a **user declaration**, not an independently audited Chipflow
certification. A different deck hash blocks execution until the user reviews and
explicitly updates the configuration.

No deck is bundled. Restricted PDK data is not downloaded or redistributed. Tcl,
Ruby, Python, Magic technology files and Netgen setup files are trusted executable
inputs and run with the user's privileges. Do not load untrusted designs/decks in
native mode. These adapters do not provide a multiuser sandbox.

The concrete adapters are:

| Backend | Supported local interface | Result |
| --- | --- | --- |
| `klayout_drc` | KLayout batch deck accepts `input`, `report`, `top` runtime variables and writes a `.lyrdb` marker database containing the checked top cell | Count actual report items; missing cells/items/schema is unknown |
| `magic_drc` | Explicit Magic `.tech` file; import GDS, select existing top, run native DRC and count reported violations | Native integer count; tool/script errors fail |
| `netgen_lvs` | Separate layout-extracted and schematic SPICE netlists, selected tops and explicit Netgen setup Tcl | Unique native match, explicit mismatch, or inconclusive |

Other deck interfaces are unsupported unless adapted and validated. A KLayout
deck using different variable names needs an explicit user wrapper implementing
the documented interface; merely pointing to an arbitrary foundry deck does not
establish compatibility. Magic DRC is restricted to the selected technology and
style. Netgen integration does **not** extract the layout; provide a genuinely
extracted netlist and retain extraction evidence. Comparing a schematic with
itself is not layout-versus-schematic validation.

The example below describes a configuration contract, not a shipped qualified
deck or a supported technology qualification:

```yaml
backend: klayout_drc
pdk: YOUR_PDK_AND_REVISION
qualification_reference: YOUR_QUALIFICATION_RECORD
# Paths resolve relative to this check configuration.
deck: decks/chipflow-compatible.lydrc
deck_sha256: REPLACE_WITH_ACTUAL_64_CHARACTER_SHA256
layout: outputs/final.gds
top: my_chip
dependencies: {}
timeout_seconds: 3600
```

A successful command with an absent, empty, malformed or ambiguous report cannot
pass a check. Nonzero native return codes fail. No configured deck means not run.
Every reported pass applies only to that specific check/deck/input combination.
Router DRC, foundry-rule DRC, LVS, antenna, density, extraction and timing are
separate acceptance categories. These adapters do not declare full foundry
signoff or tapeout readiness.

## Developer interfaces and evidence

`inspection.gds_plan`, `preview_gds_plan` and `netlist_plan` return supervised
`CommandPlan` objects. Each command must execute through the worker guardian.
`inspect_gds` and `preview_gds` are bounded convenience wrappers for local commands.
`netlist_result(report, top, returncode)` requires native JSON statistics as well as
successful execution.

`physical_verification.PhysicalCheckConfig` validates explicit check inputs.
`physical_check_plan(config, directory, base=None)` verifies deck/dependency hashes,
records the manifest and returns a supervised plan. Read the result with
`physical_check_result(config, directory, returncode, tool_version=...)`. Record
the actual version probe with the native log; an unknown version is not a tested
pairing. Return status, violations, report SHA-256, tool/deck/PDK/configuration and
scope remain separate from overall run execution and quality requirements.

## Validation limits

`tests/test_inspection_physical.py` executes actual KLayout layout creation,
reading, invalid-top/corrupt/empty-data failures and native PNG rendering when the
optional package is installed. It also executes real Yosys structural inspection
and missing-module/top failures when Yosys is present.

Physical deck plans and report decisions are covered with application fixtures.
Those fixtures do not establish that Magic, Netgen or any technology-specific DRC
or LVS deck works. Native technology/deck acceptance remains pending until those
tools and legally usable qualified views/decks are available. Preserve the native
commands, input identity, reports and tool versions for each real qualification.
