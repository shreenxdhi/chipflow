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

The release remains beta until the documented release checks pass on supported
hosts. New designs, tool revisions, and platform integrations require validation.
