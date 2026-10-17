# Execution and engineering quality

A tool completing successfully does not establish design correctness. Chipflow
preserves its existing execution statuses and separately evaluates configured
quality requirements. A quality pass means only the listed required checks passed
within their recorded scope. It never establishes tapeout readiness.

Existing projects default to `learning`, preserving the 1.x execution contract.
Learning displays unavailable checks and violations as warnings without inventing
verification evidence. Optional requirements can be added to any policy.

```yaml
quality:
  policy: standard
```

Standard requests an RTL testbench before physical execution. It requires RTL
simulation, successful synthesis and recorded output validation for physical runs.
For `finish`, final setup and hold slack must be nonnegative, setup/hold TNS
must be zero, router DRC must be zero, and native layout inspection must pass. Missing required evidence makes quality `incomplete`; an observed
required violation makes quality `failed`. Execution can still be `completed`.
Standard does not require full foundry verification, which remains separately
unverified unless qualified checks are actually integrated and executed.

Expert settings retain full control:

```yaml
quality:
  policy: expert
  require_simulation: true
  required_checks:
    - setup_slack
    - hold_slack
    - setup_tns
    - router_drc
    - equivalence
  thresholds:
    setup_slack: 0.1
    hold_slack: 0
    setup_tns: 0
    router_drc: 0
```

Supported numerical requirement names are `setup_slack`, `hold_slack`,
`setup_tns`, `hold_tns`, `router_drc`, `transition_violations`,
`capacitance_violations`, and `unconstrained_endpoints`. Other requirement names
are `rtl_simulation`, `synthesis`, `artifacts`, `equivalence`, and
`physical_verification`, `native_layout`, and `netlist_structure`. Unknown names
are rejected. Native layout and netlist checks require real native-tool evidence;
missing optional tools or library models remain unavailable/unsupported.

Slack thresholds are minimum values in the tool's reported timing units. TNS
thresholds are maximum absolute magnitudes; zero requires zero TNS. Violation
thresholds are maximum counts. Native report units and corner context must be
reviewed; Chipflow does not silently infer or convert missing units. Full physical
verification requires explicit evidence from integrated qualified checks, never a
router pass or the existence of GDS.

Threshold settings alone do not add requirements: include the check in
`required_checks`. Explicit `require_simulation: false` disables the standard
simulation prerequisite unless `rtl_simulation` is explicitly required. Expert
projects without requirements have quality `not_required` rather than `passed`.

Only applicable final-report metrics establish final timing. Earlier placement or
routing estimates cannot substitute. Conflicting values across report paths,
aliases or corners remain unknown, with all source paths and keys recorded for
review. Invalid, boolean and nonfinite values never pass. Missing metrics are
unknown, never zero. Unsupported/inconclusive/not-run verification never passes a
required check.

## Developer interface

`chipflow.quality.QualityConfig` validates the additive `quality` project field.
`requires_simulation(config)` determines whether the scheduler should prepend RTL
simulation. `evaluate_quality(config, state, reports=None)` is a pure evaluator:

- `config` is a `QualityConfig` instance or its mapping.
- `state` contains target, execution stages, validated outputs and optional
  `verification` results.
- `reports` maps run-relative native JSON paths to original metric dictionaries.

The returned mapping contains `policy`, independent quality `status`, detailed
`checks`, `warnings`, and a scope disclaimer. Each check records requiredness,
status and evidence; numerical checks include observed value and threshold.
Flattened state metrics and legacy physical-status flags are not accepted as
proof. The evaluator does not run tools or modify execution state.

Test these independent semantics with:

```sh
pytest tests/test_quality_constraints.py
```

The tests use native-report-shaped fixtures to verify application decisions.
They do not certify an EDA tool, timing corner, rule deck or physical design.
