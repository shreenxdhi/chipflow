# Engineering workspace architecture and compatibility

This development branch extends the 1.1.1 manager. It does not declare a new stable
major release. ORFS remains the physical implementation engine; Chipflow does not
replace synthesis, timing, routing or foundry verification algorithms.

## Execution and evidence

`Store` validates a project, snapshots declared inputs and launches the detached
`Worker`. `Worker.command` remains the process-supervision boundary. Concrete
verification and inspection adapters return `CommandPlan` objects: argument arrays,
a working directory, expected artifacts and a timeout. The worker executes these
plans through the same guardian used by ORFS. Native execution preserves existing
behavior; `execution.prepare` maps that contract to restricted pinned containers.

A run has separate execution, verification and quality results. `completed` means
the requested commands and stage-output checks completed. It does not establish
quality, timing closure, functional completeness or physical signoff. `quality.py`
evaluates recorded stage/report evidence against explicitly selected requirements.
Missing, nonfinite, conflicting or unavailable required evidence prevents a pass.
Learning is the backward-compatible default; standard enables verification-first
physical work. Expert users choose requirements without rewriting native reports.

The engine saves `quality.json`, `constraints.json`, `verification.json` when
applicable, `environment-manifest.json`, native reports and the original logs.
The constraint report is a lexical advisory inventory, not Tcl execution or linked
OpenSTA constraint coverage. Environment manifests record tool/platform/input
identities; retries compare them and apply warn, strict or ignore policy.

`reports.py` builds JSON, Markdown and escaped standalone HTML from stored evidence.
`experiments.py` freezes inputs and executes bounded sequential candidates using
the existing store. Comparison warnings expose incompatible constraints/platforms/
toolchains. Pareto results describe the evaluated feasible candidates, not a global
optimum. Stage caching is disabled; no intermediates are silently reused.

## Workspace and security boundary

The browser remains lightweight static JavaScript. Real stage detail, logs, quality,
reports and bounded PyVCD waveform data come from the existing local API. Source
import inventories files without guessing source order or top-module selection.
User selections remain explicit. Themes and presentation modes do not remove
advanced YAML settings.

LocalBoundary host/origin checks remain in place. Projects, Tcl, Python testbenches,
Make configuration and tool scripts are trusted executable inputs. Native execution
is not an untrusted-code sandbox. Container restrictions limit mounts, privileges,
networking and resources, but are not a qualified hostile-project isolation service.
No remote service, telemetry or cloud design upload is introduced. Explicit project
environment values are project data and can appear in snapshots, APIs and backups;
do not use project configuration as a secret store.

Physical jobs retain per-store serialization and also acquire a private user-wide
lock to prevent different workspaces from unintentionally running competing flows.
Guardian descendants inherit the lock. Container cleanup has a longer termination
grace than native cleanup so runtime removal can finish before the lock is released.
CPU/RSS measurements are sampled observations; container limits are actual runtime
limits. Native hard resource enforcement is not implemented.

## Existing data and projects

No migration or in-place rewrite of existing history is required. Run schema remains
1 and new metadata is additive. Existing YAML defaults to native execution, Icarus
and learning quality. Existing run/retry/report/backup commands remain available.
Older runs without environment manifests have unknown reproducibility; strict retry
rejects missing identity evidence rather than claiming compatibility. Default warn
policy preserves access to historical workflows while explaining this limitation.
Generated clock-only SDC remains identified as simplified, including after retry.
Snapshots preserve original inputs and retries do not pick up changed live RTL.

Pass markers now must begin a trimmed output line and end or be followed by a colon
or whitespace. Explicit line-leading failure diagnostics always reject success.
Testbenches that previously embedded PASS inside unrelated prose should print a
clear marker line or configure their marker explicitly. This intentional correctness
change prevents `NOT PASS` and error messages containing PASS from becoming success.

New optional extras are `verification` (Cocotb) and `physical` (KLayout Python module).
Ordinary installation does not install the complete EDA stack. Setup offers opt-in,
auditable choices; it does not silently run sudo or modify an existing installation.

## Extension examples

See [VERIFICATION.md](VERIFICATION.md), [EXECUTION.md](EXECUTION.md),
[QUALITY.md](QUALITY.md), [REPRODUCIBILITY.md](REPRODUCIBILITY.md),
[TIMING.md](TIMING.md), [REPORTS.md](REPORTS.md), [EXPERIMENTS.md](EXPERIMENTS.md) and
[WORKSPACE.md](WORKSPACE.md). `examples/plugins/yosys_hierarchy.py` is a separate
concrete adapter tested against real Yosys. A universal plugin registry, remote
worker protocol and general parallel DAG scheduler are not shipped.
