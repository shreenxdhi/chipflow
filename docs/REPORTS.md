# Engineering reports and comparisons

Reports preserve execution status, quality-gate status, individual check status,
configuration, stage records, real metric keys, toolchain/environment records,
input hashes, diagnostics, and artifact references. Exporting a report does not
execute an additional simulation, timing analysis, or physical check.

```python
from pathlib import Path
from chipflow.engine import Store
from chipflow.reports import build_report, render_report, compare_runs

store = Store(Path.home() / ".chipflow" / "runs")
report = build_report(store, "YOUR_32_CHARACTER_RUN_ID")
Path("run-report.html").write_text(render_report(report, "html"))
Path("run-report.md").write_text(render_report(report, "markdown"))
Path("run-report.json").write_text(render_report(report, "json"))
```

JSON has `report_schema: 1` and is suitable for CI consumers. Markdown includes a
complete indented evidence record. HTML is a standalone, searchable browser
report with a collapsible evidence record, escaped user/tool content, and no
external scripts or network requests. PDF export is not implemented; browser
printing is available using normal browser controls. Artifact paths refer to the
run store; exporting this record does not embed large layouts, logs, or waves.

A run may have `execution_status: completed` and failed timing or unknown overall
quality. These values are deliberately separate. Old histories without quality
metadata are reported as `unknown`. Missing timing, router DRC, or physical
verification evidence defaults to `unknown`. No generated report grants signoff.

## Provenance and limitations

Reports read saved `manifest.json`, `toolchain.json`, `environment-manifest.json`,
and `metrics.json` through Store's artifact boundary. The additive
`run.environment_manifest` object, when present, supplies environment data.
Missing/invalid metadata is recorded as a warning instead of fabricated.
Metadata files exceeding 16 MiB are not loaded. Summary metrics retain the native
final-report key names; source metric documents are retained under
`metric_provenance.sources`. `metric_units` in run metadata, when present, is
retained as the explicit units map. Undeclared units are marked unverified in
comparisons. The exporter does not infer units, corners, activity assumptions,
or a verified power model from a metric name.

Reports can contain proprietary source paths, project configuration, tool output,
and host/environment details. They remain local until you choose to share them.
Review the exported evidence before distributing it. The report does not include
entire source files or upload anything by itself.

## Baselines and compatibility

```python
comparison = compare_runs(store, baseline_run_id, [candidate_a_id, candidate_b_id])
```

Between 1 and 64 candidate run IDs are supported. Each comparison names its
baseline and candidate, records both execution/quality statuses, and provides
numeric deltas as **candidate minus baseline**. Boolean, missing, and nonfinite
values are not treated as numerical measurements.

The comparison checks design name/top, RTL hashes, constraint hashes, complete
input hashes, platform, configuration, and environment/tool identities. Changed
or unavailable identity data produces a warning and `compatible: false`.
Legacy toolchain-only records are partial environment evidence and therefore
produce a compatibility warning. Differences can be intentional during an experiment, but remain visible because
changing a clock target, toolchain, PDK, or design can make an apparent QoR
improvement misleading. This is conservative compatibility checking, not proof
that two signoff analyses use identical corners or assumptions.

Explicit unit mismatches suppress that metric's delta. If neither side declares
a unit, a native numerical delta is retained with `unit_status: native metric;
unit unverified`; check the original report before drawing dimensional conclusions.
There is no automatic unit conversion. Raw metrics and source reports remain
available so an expert can investigate the warning.

`pareto_frontier(runs, objectives)` is a separate numerical helper. It excludes
incomplete executions and absent/nonfinite objective metrics, and describes only
the measured points provided. The experiment controller adds declared engineering
check/bound exclusions before invoking this helper. A standalone numerical
frontier does not imply verification success.

## Testing

Run:

```bash
python -m pytest tests/test_reports_experiments.py -q
```

Application tests cover missing evidence, negative timing, native provenance,
HTML/Markdown injection boundaries, corrupted metadata, incompatible constraints,
toolchains and units, finite Pareto comparisons, frozen candidates, scheduler
failure/stop behavior, configuration traversal, symlink bundles, and unknown
required check/bound exclusions. These scheduler tests use explicit application
doubles and are not EDA acceptance evidence. A separately identified native test
runs two real Icarus simulations when its executables are available; otherwise
pytest records a skip.
