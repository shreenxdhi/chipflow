# Engineering workspace implementation and acceptance tracker

Development branch: `feat/chipflow-engineering-workspace`. Baseline: `ce36f86`,
version 1.1.1, clean worktree. No 2.0 stable release is declared by this work.

Baseline: 109 tests passed on Debian 13 x86-64, Python 3.12.14. Resources:
3 CPUs, approximately 9.7 GiB RAM and 27 GiB free disk. Docker daemon 28.4.0
is available. Existing OSS CAD Suite includes real Icarus, Verilator and Yosys;
Pinned ORFS/Nangate45, real OpenROAD and KLayout were acquired for native acceptance; the exact evidence is in IMPLEMENTATION_REPORT.md.

Status vocabulary: implemented and verified; implemented awaiting environment
validation; partially implemented; not implemented; blocked. Evidence categories:
real tools; application tests with doubles; not executed; failed; not applicable.
Missing evidence never establishes an engineering pass.

## Implementation sequence

1. Preserve and audit the existing lifecycle, snapshots, CLI/API and trusted-local boundary.
2. Add independent quality/constraints/provenance/diagnostics modules and integrate P0.
3. Add concrete execution and verification adapters, safe import and engineering reports.
4. Add usable workspace views, experiment/controller interfaces and supported scheduling.
5. Run actual tools, browser journeys, negative/security/recovery tests and independent review.
6. Update this matrix and the implementation report with exact evidence and blockers.

## Feature and acceptance matrix

| Requirement | Capability | Status | Source / tests / evidence |
| --- | --- | --- | --- |
| 1 | Modular architecture and stable serialization | Partially implemented | config.py, engine.py, execution.py, verification.py; additive schema-1 fields, concrete supervised command plans; no generic remote/plugin registry |
| 2.1 | Quality policies and explicit execution versus engineering status | Implemented and verified | quality.py, engine.py, CLI/API/TUI/browser; test_quality_constraints.py; default learning preserves legacy exits |
| 2.2 | Constraint inventory, coverage and authoritative timing evidence | Partially implemented | constraints.py and constraints.json; advisory lexical inventory only; native endpoint/corner coverage not implemented |
| 2.3 | RTL/netlist formal equivalence | Partially implemented | verification.py, engine.py; real Yosys matching and mismatching examples; mapped-cell libraries must be supplied |
| 2.4 | Native output/netlist/GDS validation | Partially implemented | inspection.py, engine.py; real KLayout and Yosys tests; mapped netlist inspection needs explicit libraries |
| 2.5 | Environment manifests and retry mismatch policy | Implemented and verified | provenance.py and environment-manifest.json; warn/strict/ignore retry comparison; test_execution_provenance.py |
| 3.1 | Native, guided and pinned container installation approaches | Partially implemented | installer.py existing opt-in native choices; execution.py pinned image execution; full bundled EDA image unavailable |
| 3.2 | First-launch setup, resources and actionable choices | Partially implemented | setup.py, resources.py and existing setup wizard; version qualification remains environment-specific |
| 3.3 | Container EDA execution, isolation, cancellation and limits | Partially implemented | execution.py and runner.py; restricted mounts/network/resources; real Docker smoke evidence in EXECUTION.md; full physical container acceptance pending |
| 3.4 | Meaningful dependency doctor smoke tests | Partially implemented | existing verify reference flow plus real verification tests; setup itself remains availability/version diagnostics |
| 4.1 | Icarus, Verilator, Cocotb and commercial adapter contracts | Partially implemented | verification.py Icarus/Verilator/Cocotb plans; real native tests; commercial adapter not implemented |
| 4.2 | RTL lint and source diagnostics | Implemented and verified | verification.py lint_plan/lint_diagnostics; real Verilator positive/negative tests and source locations |
| 4.3 | Structured verification, seeds, assertions and coverage evidence | Partially implemented | verification.py and engine.py; real JUnit/assertions/seeds/pass markers; functional coverage collection not implemented |
| 4.4 | Functional versus SDF gate simulation | Partially implemented | existing functional gate simulation retained; SDF timing simulation not implemented |
| 4.5 | CDC/RDC integrations and honest capability status | Not implemented | Not implemented; no CDC/RDC proof or structural safety claim |
| 5.1 | Native ORFS, constraints, macros and advanced inputs | Partially implemented | existing native ORFS customization preserved; counter/FIFO/tiny-CPU/multiclock native physical acceptance passed; macros/Sky130 pending; SRAM/macro acceptance absent |
| 5.2 | Qualified physical-verification plugins and evidence | Partially implemented | physical_verification.py explicit tool/deck/hash/PDK plans; parser/application tests only; qualified decks unavailable |
| 5.3 | STA, corners, TNS/WNS and constraint coverage | Partially implemented | quality.py final-report timing evidence and conflicts; timing.py explicit-input native single-corner reports/check_setup; no automatic MCMM or exact endpoint coverage |
| 5.4 | Power-estimate assumptions and provenance | Partially implemented | native raw power metrics preserved in reports; timing.py native power report with explicit assumptions; optional activity paths await qualification |
| 6.1 | Learning, standard and expert modes | Partially implemented | static workspace learning/standard/expert modes; quality controls and existing YAML retained; browser acceptance pending |
| 6.2 | Source/project import with explicit ordering and top | Implemented and verified | importing.py and API/browser; explicit source ordering/top; symlink/FIFO/bounds negative tests |
| 6.3 | Interactive engineering stages and evidence | Implemented and verified | static stage detail interactions expose real stages/logs/artifacts and quality |
| 6.4 | Usable waveform inspection | Implemented and verified | waveforms.py maintained PyVCD; bounded real VCD data, search/zoom/cursors; large traces truncated explicitly |
| 6.5 | Layout previews and native inspection | Partially implemented | inspection.py native GDS metadata; richer native layout previews under validation |
| 6.6 | Engineering metrics dashboard | Partially implemented | existing metrics plus quality/evidence views; comprehensive charts not implemented |
| 6.7 | Actionable log-backed diagnostics | Implemented and verified | diagnostics.py log-backed classifications, API/CLI; advisory causes never silently alter design |
| 6.8 | Keyboard, search, persistence, logs and accessibility | Partially implemented | static themes, responsive views, filters and persistence; full WCAG/manual screen-reader qualification pending |
| 7.1 | Compatible baseline and multi-run comparisons | Partially implemented | reports.py compatibility warnings, multi-run deltas; legacy Store.compare shape preserved |
| 7.2 | First-class experiments and preserved hypotheses | Partially implemented | experiments.py frozen inputs/hypotheses/baselines, CLI; browser experiment editor not implemented |
| 7.3 | Bounded real PPA sweeps and objectives | Partially implemented | bounded sequential Cartesian experiment controller; real two-run simulation test; physical PPA optimum not demonstrated |
| 7.4 | Pareto/regression analytics and exports | Partially implemented | reports.py Pareto frontier and experiment analysis; full interactive analytics not implemented |
| 8.1 | Explicit workflow dependencies and failures | Partially implemented | engine.py explicit ordered dependencies and accurate partial failures; general parallel DAG scheduler not implemented |
| 8.2 | Validated stage caching | Not implemented | Not implemented; state explicitly records cache disabled; no incompatible intermediate reuse |
| 8.3 | Resources, serialization and scheduling | Partially implemented | resources.py user-wide physical serialization, sampled CPU/RSS and container limits; native hard limits/priorities not implemented |
| 8.4 | Progressive large-design qualification | Partially implemented | small reference RTL/synthesis tests; large-design and long-duration qualification not executed |
| 9.1 | Traceable JSON/Markdown/HTML engineering reports | Partially implemented | reports.py JSON/Markdown/escaped standalone HTML, API/CLI; PDF export not implemented |
| 9.2 | Machine-readable CLI and GitHub Actions examples | Partially implemented | CLI --json/--require-quality and native-verification/browser CI jobs; hosted CI execution pending |
| 9.3 | Authenticated secure optional remote execution | Not implemented | Not implemented; authenticated remote service absent and localhost boundary retained |
| 9.4 | Concrete plugin interfaces and example adapter | Partially implemented | concrete CommandPlan and execution contracts; examples/plugins/yosys_hierarchy.py real independent adapter test; general SDK not shipped |
| 9.5 | Compatible CLI/developer workflows | Partially implemented | cli.py working sim/lint/equivalence/report/quality/timing/constraints/import/experiments commands; existing commands preserved |
| 10 | Security review and data-integrity boundaries | Partially implemented | SECURITY_REVIEW.md and adversarial tests; trusted-local Tcl/Python/Make boundary; no untrusted-project sandbox claim |
| 11.1 | Unit tests | Implemented and verified | test_quality_constraints.py, test_execution_provenance.py and other unit suites; exact run count in implementation report |
| 11.2 | Real native/container tool integration | Partially implemented | real Icarus/Verilator/Yosys/KLayout/Cocotb tests; Docker restricted sim/synthesis; native counter full physical acceptance passed |
| 11.3 | Counter, FIFO/UART, DMA, processor, multiclock, macro acceptance | Partially implemented | counter real RTL-to-GDS plus KLayout inspection; FIFO/multiclock/tiny CPU real simulation/synthesis; DMA/SRAM physical qualification not executed |
| 11.4 | Negative engineering tests | Partially implemented | negative simulation/lint/proof/artifact/quality/constraints/security tests; impossible-timing physical benchmark absent |
| 11.5 | Reliability and recovery tests | Partially implemented | existing lifecycle plus cleanup/retry/global-lock tests; host-power-loss/disk-full campaign not executed |
| 11.6 | Three persona usability journeys | Partially implemented | browser beginner/import/expert interactions under acceptance; independent human persona study not performed |
| 11.7 | Desktop/tablet/mobile browser interaction | Partially implemented | browser_smoke.py and browser_workspace.py; actual results recorded separately; additional browser engines not qualified |
| 11.8 | Accessibility review | Partially implemented | keyboard/labels/theme/responsive improvements; no full WCAG 2.2 AA certification |
| 11.9 | Measured application performance | Partially implemented | scripts/benchmark_manager.py; 1000-run app-only baseline; no large-EDA/container overhead qualification |
| 11.10 | Security tests and dependency audit | Partially implemented | workspace security, archive, origin/host/path tests; dependency audit result recorded separately |
| 11.11 | 1.x compatibility and migrations | Partially implemented | baseline existing CLI/YAML/schema preserved; regression suite; real Nangate45 counter native acceptance passed; Sky130 DMA pending |
| 12 | Operate, break, fix and retry actual user workflows | Partially implemented | real browser and CLI journeys; counter full physical execution and real layout preview passed; broader journey qualification pending |
| 13 | Independent adversarial quality review | Partially implemented | independent security specialist found retry/marker/cleanup/VCD defects; regression fixes and tests recorded |
| 14 | Product-quality acceptance | Partially implemented | Engineering integrity prioritized; complete platform/product acceptance not established |
| 15 | Complete shipped-feature documentation | Partially implemented | new focused guides for shipped modules; broad migration/API/tutorial documentation remains partial |
| 16 | Evidence-based prerelease/release qualification | Partially implemented | development branch only; no major version/release declaration; missing signoff evidence remains a release limitation |
| 18 | Final implementation report and evidence matrix | Partially implemented | tracker plus implementation/evidence report; feature matrix intentionally distinguishes absent capabilities |

## Baseline architecture findings

The existing engine already snapshots declared inputs, hashes saved configuration,
launches detached workers, serializes physical jobs per store, supervises process
groups and persists schema-1 state atomically. The primary integration seams are
`Worker.command`, `Worker.run`, `inspect_project`, and additive run artifacts.
Quality must remain separate from execution status, and old projects retain the
learning policy and native backend unless explicitly configured otherwise.

Existing final-metric collection silently merges report files and can retain stale
pass states; P0 will reset unavailable results and preserve conflicting evidence.
A security review reproduced a simulation pass-marker false positive; shared
verification parsing must reject explicit failure evidence. Current SDC text
inspection cannot establish endpoint coverage; authoritative STA remains a real
OpenSTA/OpenROAD operation.
