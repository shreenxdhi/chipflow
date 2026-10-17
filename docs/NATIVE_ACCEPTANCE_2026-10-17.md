# Native engineering acceptance — 2026-10-17

These results are **passed with real tools** on Debian GNU/Linux 13, Linux x86-64.
They qualify the named reference workloads and recorded configuration only.
Nangate45 is a research library explicitly described upstream as nonmanufacturable.
No foundry signoff or tapeout-readiness claim is made.

## Results

All four runs executed the actual self-checking Icarus RTL simulation followed by
native ORFS synthesis, floorplan, placement, CTS, routing and GDS generation.
KLayout independently read each final GDS, checked the expected top and nonempty
geometry, and rendered an actual layout PNG. Standard policy evaluated the
recorded metrics; optional unknown/not-run checks remain separate.

| Design | Run ID | Setup slack ns | Hold slack ns | Setup/hold TNS ns | Router DRC | Execution / configured policy |
| --- | --- | ---: | ---: | --- | --- | --- |
| counter | `b3fe6a972fc44cbc8f73facec9da838c` | 9.69676 | 0.0855032 | 0 / 0 | 0 | completed / passed |
| fifo | `061279ffd5ee43b8a59f4645b2381380` | 9.65254 | 0.0975722 | 0 / 0 | 0 | completed / passed |
| tiny_cpu | `ba833774d4b64965a130ab281c894c8a` | 9.48321 | 0.115506 | 0 / 0 | 0 | completed / passed |
| multiclock | `37c6d3db11514d999a0fae35d1d90c5d` | 9.71365 | 0.0856582 | 0 / 0 | 0 | completed / passed |


The generated constraints define the configured primary clocks only. Input/output
delays and complete endpoint coverage are not established. Native ORFS warnings
were preserved (for example, FIFO reported ten unconstrained endpoints). Positive
slack applies to the analyzed constraints and typical library corner; it does not
prove complete timing coverage, CDC safety, multi-corner closure or signoff.

Independent post-run Yosys `hierarchy -check` and `check -assert` checks passed for
all four synthesized netlists with the real Nangate Liberty cell interfaces
explicitly supplied. These structural checks do not simulate cells or establish
functional equivalence. The original run metadata is preserved unchanged.

## Tool identity and scope

- ORFS: `1ff305ccbe694958d0125f0695dceefb58dfb3f6`; actual Nangate45 Liberty/LEF/GDS.
- Yosys: 0.68+post; upstream binary reports Git SHA `UNKNOWN`.
- OpenROAD: upstream tool reports version `unknown`; binary SHA-256 is
  `0a4cdf08482bab2d99543e8b32b4b142e013becc7f2c1598afb39ba058193f53`.
- OpenROAD/Yosys source: official `openroad/orfs` Linux amd64 image manifest
  `sha256:dd9591564dab5f0f3382a147b02ddb2092dfaa9dd5b44b333527cea626132aff`.
  Only its SHA-256-verified tools layer was extracted and executed natively.
- Icarus: 14.0 development build, `s20260301-585-gf190dbb26-dirty`.
- Native KLayout CLI 0.30.0 performed DEF/GDS conversion. Native KLayout Python
  API 0.30.12 performed independent inspection and rendering.

All tool binaries remain external to the source repository. Rootless workspace
extraction of public Debian KLayout/Ruby/Qt dependency packages was used. The
manager did not silently install packages or change the host package database.
The initial full ORFS Docker pull exhausted the daemon's VFS layer storage and
failed; incomplete layers cleaned up and free disk recovered. It is not counted
as a working containerized physical flow.

## Discovered failure and repair

The FIFO, tiny CPU and multiple-clock examples initially passed simulation and
synthesis, then failed native floorplanning: `No floorplan initialization method
specified`. Those failures and logs were retained. The examples now declare the
same explicit tutorial geometry as the counter: 100 × 100 µm die, 80 × 80 µm core,
and placement density 0.3. RTL, testbench expectations and clock targets were
unchanged. These are reference geometry choices, not optimized PPA settings.

## Evidence locations

The execution workspace retains `.chipflow/acceptance/current/runs/RUN_ID/`, with
`console.log`, `run.json`, `project.json`, snapshot manifests, toolchain evidence,
stage-native reports, `inspection-gds.json`, `layout-preview.json`,
`layout-preview.png` and `results/nangate45/TOP/base/6_final.gds`.
`native-baseline-summary.json` records exact final GDS sizes and SHA-256 values.
`native-toolchain-source.json` records download identities and checksums.
`independent-netlist-inspection.json` links the separate structural checks.
The historical failed runs are indexed in `extended-before-floorplan-fix.json`.
These are local generated artifacts, not tracked source or hosted CI uploads.

## Explicit missing acceptance

- Functional gate simulation: no complete matching functional cell model set
  available. Strict Yosys Liberty-to-model import failed at unsupported
  `CLKGATETST_X1` output `IQ`; no ignore flags or placeholder cell models used.
- Formal equivalence: not executed for these mapped native designs.
- Qualified foundry DRC/LVS, antenna and other signoff decks: not executed.
- Sky130 DMA and SRAM/macro physical reference cases: required design/view sets
  unavailable in this environment.
- Containerized physical implementation, ARM64 and macOS host support: not qualified.

See [execution documentation](EXECUTION.md) for the separate real-container RTL
simulation/synthesis/orphan-cleanup acceptance test.

## Negative engineering test: impossible clock target

Run `03d86d86074349b098cb8b833ae48376` used a separate copy of the counter
configuration with clock period **0.001 ns**. The original reference configuration
was preserved. Actual native execution completed through final GDS and native
layout inspection, while configured quality **failed**, as required:

| Measured native result | Value |
| --- | ---: |
| Setup worst slack | -0.265937 ns |
| Setup total negative slack | -1.94579 ns |
| Hold worst slack | 0.0859702 ns |
| Hold total negative slack | 0 ns |

`setup_slack` and `setup_tns` gates failed. No timing target was relaxed, no
verification was disabled, and no expected value changed. This establishes that
successful tool execution/GDS generation does not override failed timing quality.
The local `negative-impossible-clock-result.json`, run metadata and native
`logs/nangate45/counter/base/6_report.json` retain the measured evidence.
