# Changelog

## 1.1.1

- Learning progress counts only the seven lesson stages and follows runs retried
  from saved inputs, including gate simulations, without inflating completion.
- Loading workspace settings preserves a configuration path already entered or
  selected by the user while initialization is in progress.

## 1.1.0

- Beginner quickstart runs a real self-checking counter simulation after checking
  prerequisites; an interactive wizard configures users' own RTL.
- A seven-step browser learning path, shared CLI/terminal guide, and plain-language
  glossary explain outputs and review tasks from simulation to GDSII.
- Expert mode adds a complete YAML copy editor and searchable installed ORFS
  settings, with native Tcl hooks and policy script overrides.
- Tcl bundles, shared helpers, parameters, and declared library files are preserved
  for execution, retry, and backup/restore. Preflight checks native availability.
- Project copies preserve complete configurations and resolve their input paths;
  manager-owned flow paths and unsafe Make expressions remain protected.

## 1.0.0

Stable release for the documented single-user Linux support contract.

- A command guardian stops tool groups when a worker crashes and retains the
  physical-flow lock until cleanup completes.
- Durable atomic metadata, worker identities resistant to PID reuse, persisted
  interruption recovery, and compatibility with version 0.1 run histories.
- Physical stages require expected nonempty outputs and record their hashes;
  saved input/configuration integrity is checked before execution and retry.
- Checksummed backup/restore with safe extraction, input path relocation, and
  protection against overwriting existing history.
- Bounded UTF-8 log reads, bounded browser display memory, and stable paused logs.
- Terminal prerequisite suggestions, executable failure detection, health checks,
  bounded web requests, security headers, and private project files.
- Expanded host/browser/container CI, scheduled dependency audits, SBOM output,
  dependency updates, and operational upgrade/recovery documentation.

## 0.1.0

Initial beta release.

- Local terminal and browser dashboards over a shared persistent run engine.
- Project setup assessment, saved tool directories, and installation suggestions.
- Packaged counter example, project initialization, and native smoke verification.
- RTL simulation, ORFS synthesis through finishing, and optional functional gate simulation.
- Live logs, cancellation, timeouts, immutable input snapshots, artifacts, comparisons, and retries.
- Serialized physical runs per workspace, queued cancellation, and worker signal handling.
- Run-local compatibility adapters for the validated native Yosys/OpenROAD pairing.
