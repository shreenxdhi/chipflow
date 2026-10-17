# Security and acceptance review

Review baseline: `ce36f8671b05845975b4e8a419c2aed4ea3baf2f`.
This document records the independent review conducted during the engineering
upgrade. It distinguishes observed behavior from deployment assumptions. It is
not a penetration-test certificate or ASIC signoff statement.

## Trust boundary

Chipflow executes trusted local projects as the current user. RTL testbenches,
SDC, Tcl, Makefiles, technology scripts, native tools, and configured adapters can
execute code. Project validation is not a sandbox. Native execution can access
that user's files and inherited environment. Do not execute unfamiliar projects
merely because their YAML passes validation. Do not mount private credentials or
unrelated user directories into a container used for project execution.

The browser API has no user authentication or authorization model. Bind it to
loopback and do not publish it through a reverse proxy. Host and Origin checks
protect browser requests within this deployment model; they do not authenticate
remote workers or provide isolation from other processes running as the user.
Container execution, when configured, must have its own reviewed mount, user,
network, capability, and resource policies. An execution container alone is not a
multiuser service.

Explicit project `execution.environment` values are ordinary project configuration:
they are preserved in snapshots, YAML/API project responses, backups, and reports.
The dedicated environment identity records their names and omits their values;
this is not secret storage or global redaction. Tool output can echo configuration
values too. Use this mechanism only for nonsecret settings. Ambient environment
variables are not dumped into manifests, but native tools inherit the process
environment and can read it. Credential/secret references are not implemented.

Inputs, backups, reports, logs, waveforms, and layout artifacts can disclose
proprietary information. Checksums detect accidental changes; a checksum stored
alongside its data does not authenticate a maliciously modified archive. Obtain
backups and tool releases from trusted sources and retain independently obtained
hashes when transferring them.

## Audited protections and evidence

| Boundary | Implementation inspected | Existing regression evidence |
| --- | --- | --- |
| Browser request isolation | `web_security.py`: local Host, same Origin, cross-site rejection, CSP, frame denial | `test_web_request_bounds_security_headers_and_log_symlinks`, API tests in `test_engine.py` |
| Request size | POST/PUT/PATCH body limited to 1 MiB while streaming | Oversized request regression in `test_reliability.py` |
| Artifact access | `Store.directory` validates run IDs; `Store.artifact` resolves paths and rejects escapes | Traversal, file symlink, log symlink, run-directory symlink tests |
| Configuration execution | Strict Pydantic models, safe YAML loading, argv-based subprocess calls, protected ORFS variables and safe Make paths | `test_validation`, customization and installer rejection tests |
| Snapshot integrity | SHA-256 input and configuration verification before retry/backup | Saved-input/configuration tampering tests |
| Backup restoration | Regular files only; links, sparse entries, traversal and duplicate names rejected; checksums verified before publication | Restore link/traversal/corruption/truncation and relocated retry tests |
| Tool installation | Approval before execution; argv plans; downloaded suite size and SHA-256; Python tar data filter | Installer consent, identity, checksum, extraction tests |
| Process lifecycle | Guardian pipe, process groups, Linux boot/start identity, inherited physical lock | SIGKILL, descendants, timeout, cancellation, PID-reuse tests |
| Durable metadata | Atomic rename, file and directory synchronization | Atomic-write failure and schema tests |
| Frontend rendering | Reviewed application constructs DOM with `textContent`; CSP disallows inline scripts | Browser smoke is complementary; it is not exhaustive XSS analysis |

An existing store with externally changed permissions is not automatically
repaired. New run directories explicitly use mode 0700; the baseline review did
not establish disclosure of new run contents merely from a permissive parent.
The process guardian is cleanup for cooperating local tools, not containment of
malicious processes that deliberately escape their process group.

## Findings requiring tracking

1. **Simulation false-positive marker (confirmed with real Icarus).** At the
   baseline, a zero-exit testbench printing `FAIL: expected PASS but got 0` was
   accepted because the default marker `PASS` matched a substring. Execution
   completion must not imply functional success. The regression procedure is to
   run that testbench, require verification failure, then separately run the
   packaged self-checking counter and require its real pass evidence. Exact
   marker matching and explicit structured failures should share one parser
   across simulator adapters. Track the fix and its tests in the implementation
   report; this finding describes the baseline, not an assertion about later code.
2. **Archive enumeration resource exhaustion (fixed; regression tested).** Baseline
   restore calls `tarfile.getmembers()` before any entry-count limit. The suite
   installer checks count only after `getmembers()` has materialized the complete
   table. Highly compressible archives with many entries can consume substantial
   memory before rejection. Restore and installation now iterate entries and
   reject before exceeding configurable module constants: 200000 entries, 4096
   bytes per member path and 64 MiB of member-table metadata. Restore also checks
   available staging space during enumeration, and suite extraction retains its
   8 GiB expanded-data budget. Real low-count crafted-archive tests lower those
   constants and verify rejection without publishing a run or tool installation.
   The tests prohibit `getmembers()` so materializing the entire table cannot
   silently return. This was identified by inspection; a host-exhaustion exploit
   was intentionally not run. Limits apply to parsed entries; Python tarfile
   processes extension headers internally before yielding an entry, so they are
   not a guarantee against every hostile parser/allocation input.
3. **Bounded frontend output does not bound storage or listing cost.** Native
   tools can grow logs/artifacts until disk exhaustion. Artifact listing and
   history APIs enumerate all records without pagination. Qualify large histories,
   add measured limits where necessary, and preserve explicit disk-error behavior.
4. **Resource and isolation scope remains local.** Locks serialize physical work
   only inside one store at the baseline; the new user-wide scheduler lock can
   serialize cooperating workers across stores when integrated. It does not
   coordinate unrelated tools or different users. Native
   projects inherit the user's privileges. Do not advertise host-wide resource
   isolation, remote authorization, or safe untrusted-project execution without
   separate implemented and tested controls.

No high-impact remote exploit within the documented single-user loopback trust
model was demonstrated by this review. That statement is limited to inspected
paths and executed tests, not proof that no vulnerabilities exist.

## Executed checks in this review

Environment: Linux, Python 3.12 virtual environment at
`/workspace/chipflow-venv`; repository baseline above. These paths identify this
review environment and are not installation defaults.

```sh
python -m pytest -q tests/test_engine.py tests/test_reliability.py \
  tests/test_lifecycle.py tests/test_installer.py \
  -k 'traversal or symlink or unsafe or boundary or request or guardian or archive or backup or tamper or timeout or cancellation or identity or protected or signal'
python -m pytest -q tests/test_diagnostics_security.py
ruff check chipflow/diagnostics.py tests/test_diagnostics_security.py
```

Results: 21 selected existing tests passed, 45 deselected; the combined
diagnostic/archive, installer and reliability suite passed 75 tests; Ruff passed. The tests exercise both application behavior and native
process cleanup; none prove physical signoff. The real Icarus negative marker
reproduction completed with the incorrect baseline acceptance described above.

`diagnostics.py` is advisory: it reads at most a configured byte budget, limits
findings and evidence size, and reports tail-relative rather than invented
absolute line numbers for truncated logs. Findings retain tool text and source
locations where present. Missing evidence is not converted into a pass. Log
content remains untrusted text and must use escaped/text rendering in viewers.

The existing dependency-audit workflow uses pip-audit and emits an SBOM. Its
presence is not a fresh audit result. Browser smoke, dependency audits, native
physical qualification and container isolation need their own run-specific
results in the implementation report.

## Integration review regression cases

The independent integration review added `tests/test_workspace_security.py`:
source FIFO rejection, external source symlink handling, unterminated comments,
API waveform escape/origin/host boundaries, report markup escaping, explicit
configuration-value export transparency, failure-marker handling, and malformed
VCD errors. Run this suite against the final revision; its presence alone is not
a passing result.

Two additional realistic reproductions were reported to the integrator:

- An unchanged strict retry of a real Icarus counter initially failed because a
  generated SDC snapshot changed from `inputs/clocks.sdc` to
  `inputs/constraints/0/clocks.sdc`. Retry identity must remain content-equivalent
  and preserve the simplified/generated constraint classification. A worker-side
  strict rejection must also update stored comparison evidence before raising.
- A real command guardian with a local fake runtime performing a seven-second
  cleanup was killed by the worker after five seconds, despite cleanup's
  fifteen-second allowance. Container cancellation must allow the guardian's
  terminate-plus-cleanup budget and retain scheduler locks through cleanup. This
  is an application process-supervision test, not native container/EDA evidence.

These findings describe the versions exercised during integration. The final
implementation report and executed regression results determine fix acceptance.

### Accepted integration regressions

After the integration fixes, the workspace-security, resource and diagnostic
suites passed 48 tests, including actual guardian delayed-cleanup supervision,
malformed VCD returning a client error, and rejection of `NOT PASS`. This count
precedes the additional persistent real-tool strict-retry test.

An independent real Icarus run and unchanged strict retry both completed. The
retry recorded `compatible: true`, no differences or unknown identities, and
retained `generated_simplified: true`. The persistent regression
`test_real_simulation_strict_retry_preserves_generated_constraint_identity`
executes both real simulations, checks the preserved `inputs/clocks.sdc`
manifest identity and the simplified-constraint classification, and skips
explicitly if Icarus binaries are unavailable. This validates retry identity for
that configuration, not complete environmental hermeticity or physical signoff.
