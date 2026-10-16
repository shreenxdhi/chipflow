# Operations and supported use

Chipflow 1.x supports a single user running trusted RTL and toolchains on Linux.
The browser binds to loopback; the terminal and browser share one run store. It is
not a hosted service or a multiuser execution boundary. Those deployment models
require a separate design rather than exposing this server on a network.

## Installation and acceptance

```sh
brew install shreenxdhi/chipflow/chipflow
chipflow setup
chipflow verify --target sim
chipflow verify --target finish
```

Homebrew source builds can take several minutes. Manager installation does not
install EDA binaries or licensed platform data. Setup suggests missing tools;
select OpenROAD and Yosys from the same ORFS toolchain revision, then save paths:

```sh
chipflow setup --orfs /opt/OpenROAD-flow-scripts --bin-dir /opt/eda/bin --save
```

Run both verification commands on each execution host and again after changing
EDA tools. A tool appearing on PATH does not establish compatibility. Physical
verification consumes CPU, memory, and disk space; run it before depending on a
new host for project work. Source and work paths used by ORFS must avoid spaces
and Make/shell metacharacters.

The Python CI matrix covers 3.11–3.14 on Ubuntu 22.04 and 24.04. A clean Debian 12
container tests installed-wheel simulation. Native physical-flow acceptance uses
the revisions and designs in [validation](VALIDATION.md). The validated
architecture is x86_64. Other tool revisions, architectures, and PDK integrations
need their own acceptance runs. macOS and remote execution are outside the 1.x
support contract.

## Upgrade and rollback

Wait for jobs to finish before upgrading the manager. Detached workers retain
their interpreter and files; replacing a virtual environment during a job is not
a supported upgrade procedure.

```sh
chipflow backup chipflow-history-before-upgrade.tar.gz
brew update
brew upgrade shreenxdhi/chipflow/chipflow
chipflow --version
chipflow setup
chipflow verify --target sim
```

Run schema 1 is retained through 1.x. Version 0.1 histories are read in place;
new metadata fields are additive, and unknown future schemas are rejected rather
than overwritten. Stable releases use semantic versioning. Breaking CLI,
configuration, or history changes require a major version and migration guidance.
Keep the previous release wheel/source archive if rollback is required, and
restore the pre-upgrade backup into a separate directory before using older code.

## Back up and restore

```sh
chipflow backup chipflow-history.tar.gz
chipflow --runs /safe/path/restored-runs restore chipflow-history.tar.gz
chipflow --runs /safe/path/restored-runs history
```

Backups require all runs to be terminal and include logs, reports, artifacts, input
snapshots, and checksums. They exclude internal locks and links into the EDA
installation. They contain proprietary source data: keep them private and copy
them to your normal backup storage. The command prints the archive's SHA-256;
store that value separately when transferring a backup.

Restore requires an empty destination. It rejects links, traversal paths,
duplicate members, invalid schemas, insufficient disk space, and checksum
mismatches before publishing restored runs. Saved input paths are relocated so
retries can use the new history directory. Original ORFS paths and provenance are
retained; cross-machine EDA migration requires configuring and validating the
matching toolchain. Corrupted saved inputs or configuration block retries.

## Failure recovery

Closing either dashboard or restarting the browser server does not cancel workers.
Use `chipflow cancel RUN_ID` to stop a run. The command guardian terminates the
native tool process group on cancellation, timeout, or loss of its worker. It
retains the physical-flow lock during cleanup so another flow cannot overlap that
cleanup. A worker identity includes its Linux boot ID and start time to guard
against PID reuse.

After a worker crash or host reboot, `chipflow history` or either dashboard
records unfinished runs as interrupted. Inspect `worker.log` and `console.log`,
fix the cause, and use `chipflow retry RUN_ID` to start an independent run from
verified saved inputs. An interrupted stage is never resumed from partial tool
outputs. A successful physical stage also requires nonempty expected artifacts;
final GDS has a stream-header sanity check. Command completion and design timing,
router DRC, and foundry signoff are separate results.

JSON metadata uses atomic replacement with file/directory synchronization. If
storage fills up, free space and inspect the interrupted job before retrying.
No automatic history deletion occurs. Archive completed runs before manually
removing data, and never remove an active run directory.

## Monitoring and maintenance

```sh
curl --fail http://127.0.0.1:8080/api/health
chipflow history
chipflow doctor
```

Health reports server version and run schema; it does not certify EDA tool health
or design correctness. Browser and terminal logs use bounded display buffers;
download the full log or use `chipflow logs RUN_ID` for older output. Terminal
users can press `s` for prerequisite suggestions.

CI exercises installation, native Icarus, browser interaction, PTY behavior,
crash/cancellation cleanup, backup restoration, output validation, and history
compatibility. A weekly dependency audit generates an SBOM, and dependency updates
are reviewed through pull requests. Use [private vulnerability reporting](../SECURITY.md)
for security issues. Every release must pass the checks in [releasing](RELEASING.md).
