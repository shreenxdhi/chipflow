# Experiments and bounded parameter sweeps

Chipflow's experiment controller saves a hypothesis, an optional existing baseline
run ID, a finite search space, measured objectives, explicit feasibility bounds,
and required engineering checks. It runs actual Chipflow workers sequentially.
A sweep changes only parameters the user selects. It never relaxes constraints
in response to a failed result.

The Python interface is available without an external service:

```python
from pathlib import Path
from chipflow.engine import Store
from chipflow.experiments import ExperimentStore

store = Store(Path.home() / ".chipflow" / "runs")
experiments = ExperimentStore(store)
experiment = experiments.create(
    Path("my-chip/chipflow.yaml"),
    name="Placement density study",
    hypothesis="Lower density may reduce routing pressure at the same clock target",
    parameters={"physical.PLACE_DENSITY": [0.3, 0.4, 0.5]},
    objectives={"finish__design__instance__area": "min"},
    constraints={"finish__timing__setup__ws": {"min": 0}},
    required_checks=["timing", "routing_drc"],
    target="finish",
    verify_first=True,
)
result = experiments.run(experiment["id"])
print(result["analysis"])
```

Use metric names actually produced by your ORFS revision. An absent metric is
excluded from analysis; no substitute area, timing, or power value is generated.
A `min` objective minimizes a metric; `max` maximizes it. Bounds may specify `min`,
`max`, or both. Units remain the native report units. Confirm unit and timing-corner
compatibility before interpreting comparisons.

## Search limits and configuration preservation

Supported sweep parameters:

| Parameter | Accepted values |
| --- | --- |
| `clock.PORT.period_ns` | Positive finite period for one existing clock |
| `physical.PLACE_DENSITY` | Greater than 0 and at most 1 |
| `physical.CORE_UTILIZATION` | Greater than 0 and at most 100 |
| `physical.CORE_ASPECT_RATIO` | Greater than 0 and at most 100 |
| `physical.CORE_MARGIN` | Greater than 0 and at most 10000, in native units |

These are input-safety bounds, not a guarantee that every combination is physically
feasible. A conflicting native override is rejected. Clock sweeps cannot rewrite
an expert SDC file: the controller rejects that combination. Edit a new expert
configuration explicitly when changing custom timing constraints.

At most 8 parameters and 64 total Cartesian combinations are accepted. Each
parameter needs a nonempty list of unique finite values. Candidate files retain
all other validated configuration fields, including custom Tcl and native ORFS
settings. No random search, ORFS AutoTuner integration, or parallel optimization
is claimed by this controller.

Creation copies RTL, testbenches, cell models, include directories, SDC, Tcl
bundles, declared ORFS files, and an adder-map input into a private local experiment
directory. It writes `baseline.json`, `candidate-NNN.json`, and SHA-256 hashes.
Bundles with symlinks are rejected. Copying is limited to 1 GiB, half the available
disk space, and 100000 files. Original project edits after creation do not change
these copies. The toolchain and PDK remain external dependencies; every actual run
has its own normal run snapshot and environment evidence.

Hashes are verified before execution. Integrity failure stops scheduling. Hashes
protect against accidental corruption, not an attacker who can rewrite the entire
experiment and its manifest. Experiments execute trusted local projects under the
same trust boundary as ordinary Chipflow runs; executable Tcl is still code.

## Execution and recovery

For a physical target, verification-first execution runs the configured RTL
simulation for each candidate and requires successful execution before starting
implementation. The simulation uses the project's configured assertions/pass
marker; it does not prove equivalence or coverage. Projects without a testbench
must configure one or explicitly choose `verify_first=False` for expert use.

`run()` blocks until its candidates finish or scheduling stops. A separate
scheduler lock serializes experiment controllers; it does not hold the run-store
or run-state locks while waiting. The native worker retains physical-flow
serialization, timeouts, cancellation, logs, and crash recovery. Other standalone
runs remain visible through the ordinary Store. Experiment execution is sequential;
this is not a shared-host resource scheduler.

`experiments.request_stop(id)` requests active-run cancellation and prevents
launching further candidates. A stop requested before execution is respected; use `run(id, resume=True)` to
explicitly resume a stopped study. Existing recorded run IDs are reused after a
controller restart rather than deliberately rerunning completed candidates.
A process crash in the small interval between launching a worker and recording its
ID can still leave an unassociated run in ordinary history; inspect that history
before resuming after a host failure. Failed candidates remain evidence and are
not silently replaced by passing retries.

Use `experiments.list()`, `get(id)`, and `analyze(experiment)` to inspect saved
studies and recompute analysis from the recorded runs. Metadata lives under
`RUN_STORE/.experiments`. **Existing run-history backup archives do not include
experiment metadata or its frozen baseline.** Back up that directory separately;
keep it at the same absolute path because saved configurations contain absolute
snapshot paths. Portable experiment relocation is not yet supported.

## Honest result interpretation

For a final physical flow, default required checks are `timing` and `routing_drc`.
Explicit required checks must equal `passed`; missing, failed, or unknown values
exclude a candidate. Declared metric bounds must have finite measurements and
pass. An explicitly failed, incomplete, or unknown quality status also excludes a candidate.
Objective metrics must exist, and execution must have completed.

The Pareto frontier contains only nondominated eligible **measured candidates in
the declared finite search space**. It does not establish a global optimum.
Eligibility means only the declared bounds/checks were satisfied. Router DRC does
not establish foundry DRC/LVS or tapeout readiness. A design with incomplete
verification remains incompletely verified even if it lies on the frontier.

## Validation evidence

`tests/test_reports_experiments.py` separates application-only scheduler doubles
from its native Icarus test. The native test launches real detached workers for two
simulation candidates and generates reports. No physical area is inferred from
simulation: those candidates are correctly excluded from an area objective.
Physical PPA sweep acceptance still requires the matching ORFS tools and PDK and
actual measurements. This module does not claim new platform qualification.
