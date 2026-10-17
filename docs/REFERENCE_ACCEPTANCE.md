# Reference design acceptance procedure

These are executable acceptance requirements, not claims that the workloads
already passed. Historical evidence in [VALIDATION.md](VALIDATION.md) remains
historical. Every new host/toolchain revision needs a new evidence record.

## Common run procedure

Use an isolated workspace. Record Chipflow revision, toolchain manifest, source
license/revision, platform identity and hashes, input hashes, effective YAML,
constraints, native logs, metrics and artifact hashes. Retain run IDs. For each
available trusted project configuration:

```sh
chipflow setup --check
chipflow doctor
chipflow --runs acceptance/runs run /absolute/path/chipflow.yaml --target sim --wait
chipflow --runs acceptance/runs run /absolute/path/chipflow.yaml --target synth --wait
chipflow --runs acceptance/runs run /absolute/path/chipflow.yaml --target finish --wait
chipflow --runs acceptance/runs history
chipflow --runs acceptance/runs artifacts RUN_ID
```

Check `chipflow --help` and command help for additions in the installed version.
The existing `run --wait` exit status reports execution; inspect separate quality
and verification results before calling a design accepted. Do not interpret
unknown results as passing. Use the native timing engine to establish timing
coverage; static SDC inventory cannot determine constrained endpoints.

For the bundled beginner project, create real sources first:

```sh
chipflow init acceptance/counter
```

A source/configuration supplied from outside this repository must be trusted and
licensed for use. Do not silently download arbitrary benchmark RTL, memory views,
or PDK data. Missing assets mean **not executed**, rather than a substitute mock
success. This development branch packages counter, FIFO, multiclock and tiny-CPU references.
Historical DMA configuration and outputs are not a portable fixture in this checkout.

## Required evidence by workload

| Workload | Concrete verification | Physical and analysis requirements | Missing prerequisite to record |
| --- | --- | --- | --- |
| A: counter | Packaged self-checking testbench passes using actual simulator; waveform exists and changes; gate-functional simulation with matching cell models | Real Nangate45 synthesis through GDS; expected counter top via KLayout; setup/hold and router DRC values with corners and native report locations; state that generated clock-only SDC is simplified | Matching ORFS, tools, platform, cell models, KLayout |
| B: FIFO or UART | Multi-file licensed RTL; self-checking boundary cases, reset, overflow/underflow or TX/RX ordering; intentional incorrect expected output fails | Complete source/include dependencies; real synthesis and implementation; constraints and top identification | Versioned design, self-checking testbench, SDC, platform |
| C: dual-channel AXI DMA | Licensed exact DMA RTL revision; both channels, reset/backpressure/error cases checked by actual testbench | Native Sky130 flow; timing corner and constraints; router checks; actual GDS top validation; reproducible baseline comparison | Exact historical or newly supplied DMA sources/configuration, matching Sky130 assets |
| D: processor | Licensed versioned core plus a self-checking program/testbench; hierarchy resolves; intentional failed check is recognized | Measured runtime and memory, tool compatibility, real implementation artifacts, setup/hold and constraints | Core revision/license, program/image, testbench, platform |
| E: multiple clocks | Both domains exercised; configured clock relationships retained; report distinguishes simulation from CDC proof | Native timing engine lists clocks/generated clocks; a deliberately unconstrained endpoint is detected; review setup/hold per relationship/corner | Multi-clock RTL/testbench, authoritative timing script/reports; CDC engine if safety is claimed |
| F: SRAM/macro | Validated behavioral model and interface testbench; memory behavior checked | Legally available consistent LEF/Liberty/GDS; explicit macro placement, supply integration, route and native output validation; qualified DRC/LVS only with actual decks | Macro views and license, platform setup, pin/power/placement constraints, qualified decks |

## Negative and recovery acceptance

For every workload run an independent failing configuration and retain its real
log: syntax error, missing include/module, failed assertion, invalid SDC, missing
library, impossible timing target, and unavailable signoff deck where relevant.
Require explicit failures/unknowns appropriate to the check. An impossible timing
target can complete execution while quality fails. Never modify a target to make
the negative case pass.

Cancel a queued run, cancel a running tool, restart the browser server during a
run, kill a worker, and retry after changing the original source and after changing
a tool version. Verify no orphan tools or unsafe cache reuse, retained original
snapshot hashes, explicit environment drift, and independent retry IDs. Test
backup/restore with completed runs and reject corrupted/traversal/link archives.

For a browser acceptance session, create/import the project through the real UI,
inspect preflight errors, start a run, examine live logs and artifacts, correct a
real error, retry, compare runs and export available reports. Test keyboard-only
navigation and narrow viewport. Downloading VCD/GDS is not evidence of an embedded
viewer; label the actual interaction performed.

## Evidence record

For each design write a record containing: project/configuration path, source
revision/license, run IDs and run directories, commands, dates, execution status,
functional status, synthesis status, setup/hold/WNS/TNS with units/corners,
constraint coverage, router DRC, qualified DRC/LVS/deck identity, GDS parser/top,
artifact paths/hashes, tool manifest and reproducibility mismatches. Store the
underlying native reports. Mark every unavailable datum `unknown` or
`not executed: <specific prerequisite>`.

Use these separate evidence classes: **passed with real tools**, **passed with
test doubles (application behavior only)**, **not executed due to unavailable
dependencies**, **failed**, and **not applicable**. A generated GDS, a successful
router check and a functional simulation do not together establish foundry
signoff or tapeout readiness.
