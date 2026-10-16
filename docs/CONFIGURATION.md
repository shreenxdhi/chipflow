# Configuration reference

Project files are YAML. Required fields are `name`, `top`, `rtl`, and either
`clocks` or `sdc`. ORFS is discovered from the environment or saved setup settings
unless an explicit `orfs` path is supplied.

| Field | Purpose |
| --- | --- |
| name | Project identifier: letters, digits, underscores |
| top | Synthesizable top module |
| rtl | Explicit source-file paths; no automatic glob expansion |
| orfs | Optional ORFS repository or flow directory |
| platform | ORFS platform, default nangate45 |
| clocks | List of port/period_ns entries used to generate create_clock constraints |
| sdc | Complete timing constraints; takes precedence over generated clocks |
| include_dirs | Explicit Verilog header directories, copied into the snapshot |
| simulation.top | Testbench top module |
| simulation.testbench | Testbench source files |
| simulation.pass_marker | Required nonempty runtime success message; default PASS |
| simulation.timeout_seconds | Per simulation command timeout; default 60 |
| simulation.cell_models | Functional models matching the synthesis library |
| physical | Common physical variables below |
| tcl.directory | Directory copied as a bundle with its helper files |
| tcl.hooks | Native hook/policy variable to a relative script in the bundle |
| tcl.parameters | Scalar parameters exposed as CHIPFLOW_PARAM_name to tools |
| orfs_variables | Additional scalar variables from the installed ORFS reference |
| orfs_files | Native variable to a list of explicit additional files, copied into each run |
| threads | Native tool thread count, default 2 |
| timeout_seconds | Per physical-stage timeout, default 3600 |
| save_images | Optional ORFS GUI images, default false |

All input paths resolve relative to the project configuration. The guided browser
form resolves entered relative paths from its displayed workspace folder and saves
absolute paths in a new local project version.

Supported physical variables: `CORE_UTILIZATION`, `CORE_ASPECT_RATIO`, `CORE_MARGIN`,
`DIE_AREA`, `CORE_AREA`, `PLACE_DENSITY`, `PLACE_DENSITY_LB_ADDON`, `TNS_END_PERCENT`,
`ABC_AREA`, `ADDER_MAP_FILE`, `SYNTH_HIERARCHICAL`, `OPENROAD_HIERARCHICAL`,
`SYNTH_REPEATABLE_BUILD`, `MIN_ROUTING_LAYER`, `MAX_ROUTING_LAYER`, `IO_PLACER_H`,
and `IO_PLACER_V`.

Use `chipflow variables --search TERM` to search the installed native variable
reference. Preflight rejects unknown overrides, unsupported Tcl hooks, duplicate
settings across sections, and changes to manager-owned run paths and executables.
Scalar values cannot contain Make/shell expressions. Use `orfs_files` for file
inputs and `tcl.hooks` for scripts; see [customization](CUSTOMIZATION.md).

The Tcl bundle is copied in full, including sibling helpers. Resolve helpers from
`[file dirname [info script]]` or `$::env(CHIPFLOW_TCL_DIR)`. Files outside the bundle
and RTL `$readmemh` dependencies are not inferred or rewritten automatically.
Declare extra native library inputs in `orfs_files`. A custom PDK still needs a
compatible installed ORFS platform and complete technology views.

The browser's Expert mode provides the full YAML configuration, including all
clocks and timeouts. Existing input paths resolve before the configuration is
copied; newly entered relative paths resolve from the workspace folder. Saving
preserves the source file and creates a new project version.

The engine calls the installed ORFS Makefile with external `DESIGN_CONFIG` and a
private per-run `WORK_HOME`. It never replaces native physical-design algorithms.
Detected legacy reporting/extraction API differences are adapted in run-local
script overlays, with original and adapted hashes recorded in `compatibility.json`.
The two validated adapters remove Yosys's unsupported `stat -hierarchy` reporting
flag and pass extraction rules through OpenRCX's legacy `-ext_model_file` option.
Other incompatibilities fail with saved logs. Optional GUI images are disabled by
default; the tested tool pairing has a clock-tree screenshot API mismatch.
