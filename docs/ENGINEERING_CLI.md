# Engineering evidence workflows

The commands below extend the existing CLI; use `chipflow --help` and command
`--help` for complete argument details. Paths are local, trusted project inputs.

```sh
chipflow setup --options
chipflow setup --target sim
chipflow doctor
chipflow import ./rtl-project
chipflow constraints ./chipflow.yaml
chipflow sdc reviewed-timing.json --output reviewed.sdc
chipflow sim ./chipflow.yaml --wait --json --require-quality
chipflow lint ./chipflow.yaml --wait
chipflow equivalence ./chipflow.yaml --wait
chipflow run ./chipflow.yaml --target finish --wait --require-quality
chipflow quality RUN_ID
chipflow timing RUN_ID
chipflow diagnostics RUN_ID
chipflow environment RUN_ID
chipflow report RUN_ID --format html --output report.html
chipflow compare RUN_A RUN_B
chipflow experiments --help
```

`--json` with `--wait` on run/sim/lint/equivalence emits a single final run object.
Execution failure exits 1. `--require-quality` exits 2 when execution completed but
required quality evidence did not pass. Without that flag, successful execution
retains the existing zero exit contract; inspect `quality.status` separately.
Formal proof must be proven when equivalence is requested with quality enforcement.
Learning policy may have no required checks (`not_required`), which is not a quality
pass. Select a policy and requirements intentionally in YAML.

```yaml
quality:
  policy: standard
reproducibility:
  retry_mismatch: strict
simulation:
  top: counter_tb
  testbench: [counter_tb.v]
  backend: icarus
  pass_marker: PASS
```

Use the complete project template rather than the fragment above as a standalone
configuration. Verilator and Cocotb require their declared tools and dependencies;
no backend is silently substituted. Lint is not simulation, equivalence is not
simulation, router DRC is not foundry DRC, and none implies tapeout readiness.

The CI native-verification job exercises available open-source adapters. It does
not establish a qualified PDK/signoff platform. Consumer projects can use the same
machine-readable invocation and upload the entire run directory, subject to their
RTL/PDK confidentiality and licensing requirements.

Standalone native timing analysis accepts an explicit JSON specification:

```sh
chipflow sta timing-inputs.json --output timing-analysis --executable openroad
```

This creates a new evidence directory, snapshots declared inputs and runs the
native tool through Chipflow's process guardian. It does not mutate an existing
managed run or establish complete MCMM/signoff coverage. Inputs include top,
netlist, explicit technology/cell LEFs, Liberty files, SDC and corner label;
optional SPEF and activity annotation must correspond to the actual design.
Missing parasitics means cell-delay-only analysis. Read the native constraint
warnings and reports. Exit 2 means timing or constraint checks did not establish a
pass even if tool execution completed. Existing output directories are rejected.
