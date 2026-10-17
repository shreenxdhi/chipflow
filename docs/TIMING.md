# Explicit native timing and power analysis

Chipflow's normal ORFS run records stage/final timing metrics. Standalone analysis
can additionally load an explicitly selected netlist, technology views, Liberty
and SDC into real OpenROAD/OpenSTA:

```sh
chipflow sta timing-spec.json --output new-timing-analysis --executable openroad
```

The output directory must be new. The JSON specification resolves paths relative
to its own directory:

```json
{
  "top": "my_chip",
  "netlist": "implementation/final.v",
  "lef": ["pdk/technology.lef", "pdk/cells.lef"],
  "liberty": ["pdk/typical.lib"],
  "sdc": "constraints.sdc",
  "corner_name": "explicit typical library configuration",
  "spef": "implementation/final.spef",
  "power": true,
  "timeout_seconds": 600
}
```

These paths are examples, not bundled platform data. OpenROAD requires technology
and cell/macro LEF before it can link the netlist. Missing/incompatible views fail
with the native log, rather than silently choosing another platform. The analysis
snapshots explicit inputs, records hashes and uses the existing command guardian
for execution and timeout handling. `timing-result.json` records independent
execution, constraint and timing statuses with report references.

Generated reports contain native clock properties, verbose `check_setup`, setup
and hold critical paths, worst slack, total negative slack and max-transition/
capacitance violation text. `sta-native.log` records native units. Path reports
retain startpoints, endpoints and expanded clock paths. Native coverage warnings
remain visible; the application does not invent endpoint counts or infer complete
coverage from a text SDC inventory. A constraint check reporting missing I/O or
unconstrained endpoints prevents overall acceptance even when reported path slack
is nonnegative.

The command returns zero only when native execution completes, required numeric
timing results pass and native constraint checks pass. It returns one on tool
failure and two for incomplete/failing engineering checks. Unknown or ambiguous
values, missing clocks and rounded negative-zero slack cannot become a timing
pass. Native reports remain the authority; changing precision is not constraint
closure.

Each analysis uses one explicit library configuration with a user-declared corner
label. To compare corners, run separate analyses with their actual matching
libraries and parasitics. This is not automatic MCMM signoff. Without `spef`, the
manifest prominently says `none_cell_delay_only`: the analysis has no extracted
wire parasitics and cannot establish final implementation timing. Explicit SPEF
reading is supported, but complete parasitic annotation and extraction quality
still require native report review and independent acceptance.

## Power estimates and activity

With `power: true`, the native power report supplies internal, switching, leakage
and total estimates when its unit heading explicitly identifies watts. No power
value is invented if the report cannot be parsed. The result always identifies
power as an estimate, never a measurement. Without activity input, it explicitly
labels tool-default activity assumptions.

Optional activity inputs are explicit:

```json
{
  "activity": "simulation/design.vcd",
  "activity_format": "vcd",
  "activity_scope": "tb/dut"
}
```

Add these fields to the full specification; `saif` is the other supported native
reader. Their availability depends on the selected native tool. Providing a file
does not establish complete annotation: review the native activity-annotation
summary, hierarchy mapping and unannotated pins. Liberty models, operating corner,
clock/frequency constraints, voltage assumptions and propagated activity govern
the estimate. A wrong scope or missing activity must never be described as
measured power.

## Evidence and limitations

`tests/test_native_timing.py` runs real OpenROAD against a Nangate45 DFF fixture
when `ORFS_HOME` and compatible tools are available. It exercises positive setup/
hold/TNS, a genuinely impossible clock producing negative slack/TNS, clock-only
constraints producing native missing-I/O/unconstrained warnings, invalid SDC,
changed snapshots, and the actual supervised CLI including its quality-failure
exit code. Tests without those dependencies explicitly skip native execution.

Example invocation for an installed toolchain:

```sh
ORFS_HOME=/path/to/OpenROAD-flow-scripts PATH=/path/to/eda/bin:$PATH \
  pytest tests/test_native_timing.py
```

The validated standalone fixture has no SPEF and uses default activity. VCD/SAIF
annotation and arbitrary SPEF/library combinations are implemented but require
their own environment/design acceptance. No qualified signoff, CDC/RDC safety or
all-corner timing closure is claimed. External files sourced from SDC or included
from libraries are trusted dependencies; this standalone helper snapshots declared
inputs, not an inferred transitive execution environment.
