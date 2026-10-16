# Native flow customization

Start with a working expert counter:

```sh
chipflow init custom-chip --expert
chipflow hooks
chipflow variables --search clock
```

`hooks` reports which native script points the installed ORFS revision exposes.
`variables` reads that installation's variable reference, including descriptions
and defaults. The browser provides the same searchable reference in the project's
advanced settings. Expert mode also exposes the complete YAML copy editor.

Example additions to an existing project:

```yaml
tcl:
  directory: tcl
  hooks:
    POST_FLOORPLAN_TCL: after_floorplan.tcl
    POST_CTS_TCL: after_cts.tcl
  parameters:
    LABEL: my_experiment
orfs_variables:
  RECOVER_POWER: 0
  CTS_CLUSTER_SIZE: 20
orfs_files:
  ADDITIONAL_LEFS: [macros/sram.lef]
```

The example scripts run inside the native OpenROAD steps, with that step's database
and commands available. Use the relevant tool's commands for each point: synthesis
script overrides execute in Yosys, while physical hooks execute in OpenROAD.
Check the installed ORFS scripts when choosing the precise insertion point.
Not all revisions expose every listed hook. Preflight rejects unavailable hooks.

The bundled scripts include a shared helper and print execution markers. Replace
or extend them with the OpenROAD commands appropriate to your design. For example:

```tcl
source [file join [file dirname [info script]] common.tcl]
chipflow_note "custom floorplan for $::env(CHIPFLOW_PARAM_LABEL)"
```

The entire `tcl.directory` is copied and hashed before execution. Keep shared
helpers in that directory and source them relative to the script, or use
`$::env(CHIPFLOW_TCL_DIR)`. Parameters appear as `CHIPFLOW_PARAM_<name>` in the tool
process environment. They do not alter the manager's global environment.

Native replacement policies include `PDN_TCL`, `TAPCELL_TCL`,
`MACRO_PLACEMENT_TCL`, `IO_CONSTRAINTS`, `FASTROUTE_TCL`, `FOOTPRINT_TCL`,
`SYNTH_CANONICALIZE_TCL`, and `SYNTH_SCRIPT`, where supported by the installed flow.
Place these in `tcl.hooks`, with a relative file inside the bundle.

Use `orfs_variables` for literal scalar native settings and `orfs_files` for
explicit additional file lists. Declare each setting in one section only. File
inputs are copied and hashed; retries and restored backups use preserved copies.
Absolute references inside script contents are not rewritten or discovered. Any
external dependencies must be explicitly packaged or remain available. Platform
views and EDA binaries remain part of your installed toolchain.

Manager-owned variables such as `WORK_HOME`, `DESIGN_CONFIG`, output directories,
executables, and `CHIPFLOW_*` cannot be overridden. Scalar settings reject Make
and shell expressions. Custom scripts are trusted executable inputs running with
your account permissions. Full SDC constraints, multiple clocks, timeouts, threads,
and existing physical settings remain available in YAML.

See the [ORFS variable reference](https://openroad-flow-scripts.readthedocs.io/en/latest/user/FlowVariables.html)
and [ORFS flow tutorial](https://openroad-flow-scripts.readthedocs.io/en/latest/tutorials/FlowTutorial.html)
for native flow behavior. Chipflow preserves and schedules the configuration;
the installed tools implement synthesis and physical design.
