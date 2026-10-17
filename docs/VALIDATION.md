# Validation

Validated native flows:

| Design | Platform | Final setup slack | Final hold slack | Router DRC |
| --- | --- | --- | --- | --- |
| Self-checking counter | Nangate45 typical | 9.70379 ns | 0.0834147 ns | 0 |
| Dual-channel DMA | Sky130 HD | 3.43035 ns | 0.474559 ns | 0 |

Both designs passed RTL simulation and completed the real ORFS flow through GDSII.
The counter also passed functional gate simulation with models generated from its
synthesis Liberty. KLayout loaded both final GDS files. Full foundry physical
signoff was not performed. The counter uses clock-only constraints.

The native test pairing was ORFS `1ff305ccbe694958d0125f0695dceefb58dfb3f6`,
OpenROAD `fbffde0b7a27df2a898164c9a7a522e0537afd6a`, Yosys 0.52, Icarus 12.0,
and KLayout 0.30.0. Run-local API adapters were required as documented in the
configuration reference; these versions are a tested pairing, not a universal
compatibility guarantee.

Automated tests cover setup assessments and saved settings, package resources,
project validation, preflight, input hashes, API request boundaries, artifact
traversal, failure propagation, queued physical flows, cancellation, signals,
timeouts, final metrics, retries, and terminal behavior. PTY tests exercise real
keyboard navigation, resize, monochrome output, and an Icarus simulation that
finishes after its dashboard exits.

CI tests supported Python versions, builds and checks the distributions, and
smokes the installed wheel outside the repository. The container test installs
Icarus and the wheel in a fresh Linux image, runs prerequisite checks and a real
simulation, and serves the installed dashboard. Homebrew has its own formula
validation; a Ruby syntax check alone is not equivalent to an installed formula test.

Stable status applies to the [documented operations contract](OPERATIONS.md).
New designs, tool revisions, and platform integrations require validation.

## Release candidate checks

The 0.1.0 candidate passed 43 tests on each of Python 3.11–3.14 in GitHub CI.
The isolated wheel test ran a real Icarus simulation and served the installed web
application outside the checkout. A fresh Debian/Python 3.12 container also passed
simulation and installed-dashboard checks. The pinned runtime dependency audit
reported no known vulnerabilities at release preparation.

A Homebrew source installation and functional `brew test` passed in the official
Linuxbrew container, including compiled native Python dependencies. The tested
archive matches the published GitHub release SHA-256. Source builds may take
several minutes; a prebuilt Homebrew bottle is not included in this release.

The public tap also passed a fresh source installation, `brew test`, and version
check on a [GitHub Linux runner](https://github.com/shreenxdhi/homebrew-chipflow/actions/runs/38921259283).

## 1.0.0 stable acceptance

The expanded [release CI](https://github.com/shreenxdhi/chipflow/actions/workflows/ci.yml)
passed all 66 tests on Python 3.11–3.14 across Ubuntu 22.04 and 24.04, distribution
validation, installed-wheel simulation/server checks, browser interaction, and a
clean Debian 12 container installation. The browser test covers fresh-workspace
setup, missing physical tools, real simulation, waveform downloads, comparisons,
retry/cancellation, pause/resume, bounded large logs, and mobile overflow. The
runtime audit reported no known vulnerabilities and generated a dependency SBOM.

Fresh native acceptance runs completed the counter and dual-channel DMA flows
through GDSII using the revisions above, with the timing and router results in the
table unchanged. KLayout read both outputs and confirmed their expected top cells:
33 cells for the counter and 129 for the DMA. Final GDS SHA-256 values:

- Counter: `628630f6148e805fa0ef36525094925294a8399ec507bd3ffc75bee767554d89`
- DMA: `cd08392361c576b633b391347e53eaafe414e7b4039e8e47e865be92294de3ae`

All 25 existing version-0.1 history entries were backed up and restored into a
separate directory. Automated restoration also retries a real simulation after
removing the original run directory. Fault tests cover worker SIGKILL, inherited
physical locks, signal/cancel cleanup, PID reuse, atomic-write failures, corrupted
saved inputs/configuration, archive corruption/truncation/traversal/links, future schemas,
UTF-8 log boundaries, and missing physical outputs.

## 1.1 learning and customization acceptance

The local suite passed 84 tests, including Tcl bundle preservation through retry
and backup/restore, scoped script parameters, installed-variable/hook discovery,
unsafe/protected overrides, full YAML copies, and noncanonical flow-directory paths.
The expanded browser smoke passed a real beginner simulation, persisted learning
progress (including gate simulations and retry ancestry) and workspace mode, malformed YAML errors, complete YAML saves, guided
copy preservation, and the existing run/log/download/cancellation/mobile checks.
The installed wheel passed from outside the checkout, including its learning guide,
Tcl/helper resources, expert initialization, quickstart simulation, and server.

A real expert Nangate45 counter flow completed through GDSII. Its native
POST_FLOORPLAN_TCL and POST_CTS_TCL scripts executed, including a shared helper and
the configured LABEL parameter. Timing and router checks passed; overall physical
signoff remains unknown. A fresh Sky130 DMA run also completed with the timing
and router results in the table unchanged. KLayout opened the expert counter and
DMA layouts and confirmed the expected top cells (33 and 129 cells respectively).
The native toolchain is described above. Release CI repeats the manager tests across supported hosts
and Python versions; the public tap validates the installed formula separately.
