# Environment evidence and retry policies

A run's input snapshot and checksummed `manifest.json` remain the basis for
reproducing its design inputs. The additive environment manifest records:

- Chipflow version and tracked source revision/changes when available.
- Native tool executable paths, resolved identities, SHA-256 and version probes.
- ORFS revision, tracked modifications and checkout path.
- Selected platform regular-file hashes and explicitly reported scope.
- Backend settings, container repository digest and actual image identity.
- Snapshot file hashes, worker settings, host architecture and Python version.
- Names of explicitly declared environment dependencies, without their values.

Container version probes execute in the selected image. The image digest records
its binaries as a unit; native binary hashes do not capture all shared libraries,
dynamic loaders or runtime configuration. Platform symlink targets are outside
the recorded regular-file scope. Untracked ORFS files and submodule contents are
not established by the top-level Git revision. A manifest is recorded evidence,
not a claim of a fully hermetic environment or identical EDA results.

Configure retry behavior:

```yaml
reproducibility:
  retry_mismatch: warn
```

`warn` retains the compatibility report and permits execution. `strict` rejects
changed identities or required missing evidence. `ignore` permits execution but
still records the comparison; it never establishes environment equivalence.
Unavailable manifests from older history remain unknown. Existing history is
read without rewriting its schema or silently assigning modern provenance.
The normal input-integrity checks remain mandatory under every policy.

Explicit environment values are not recorded or hashed, to avoid disclosing
secrets or enabling dictionary attacks on low-entropy values. Consequently a
strict retry with such dependencies requires external review and cannot claim
complete automatic compatibility. Keep secrets out of project configuration,
input snapshots and Tcl logs.

A retry uses the original preserved design inputs. Environment changes must be
shown separately from execution completion, timing quality and verification
status. Compare quality only when design, constraints and toolchain evidence
support that interpretation. Generated GDS and matching manifests do not imply
qualified physical signoff.

Application tests cover identity differences, strict rejection, unknown historic
evidence, protected container arguments and guardian cleanup. Real-tool and
container acceptance results must be listed separately in the implementation
report; test doubles establish application behavior only.
