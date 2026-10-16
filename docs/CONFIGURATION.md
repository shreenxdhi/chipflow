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
| physical | Supported ORFS variables below |
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

Relative `$readmemh` data files and Tcl `source` dependencies are not discovered
or rewritten automatically. Package their dependencies explicitly and use paths
that remain valid during a run. Custom macro/PDK hooks are outside this version.

The engine calls the installed ORFS Makefile with external `DESIGN_CONFIG` and a
private per-run `WORK_HOME`. It never replaces native physical-design algorithms.
Detected legacy reporting/extraction API differences are adapted in run-local
script overlays, with original and adapted hashes recorded in `compatibility.json`.
The two validated adapters remove Yosys's unsupported `stat -hierarchy` reporting
flag and pass extraction rules through OpenRCX's legacy `-ext_model_file` option.
Other incompatibilities fail with saved logs. Optional GUI images are disabled by
default; the tested tool pairing has a clock-tree screenshot API mismatch.
