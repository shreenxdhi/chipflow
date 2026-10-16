# Your first RTL-to-GDS run

Install Chipflow using the README, then run:

```sh
chipflow quickstart first-chip
```

This checks Icarus Verilog before creating files. If a prerequisite is missing,
follow the printed installation suggestion and run the same command again.
On success, you have a counter, its testbench, a configuration, a guide, and a
saved simulation with a waveform. Read `first-chip/counter.v` and `tb.v`: the
hardware counts, and the testbench checks reset and counting behavior.

Open a dashboard to inspect the result:

```sh
chipflow
# Or start a local browser dashboard:
chipflow serve --port 8080
```

In the terminal, press `l` for the learning guide, `s` for prerequisites, and `?`
for keys. In the browser, Learning mode offers a counter starter and seven steps.
Each step describes the tool, expected output, and what to inspect. Expand the
VLSI glossary for unfamiliar terms. You can follow the selected project instead
of creating a counter; it must have its own RTL and testbench for simulation.

Physical design needs a compatible ORFS installation, Yosys, OpenROAD, platform
views, and KLayout for final GDS. Check them before continuing:

```sh
chipflow setup
chipflow setup --orfs /opt/OpenROAD-flow-scripts --bin-dir /opt/eda/bin --save
chipflow run first-chip/chipflow.yaml --target synth --wait
chipflow run first-chip/chipflow.yaml --target finish --wait
```

The physical sequence is synthesis, floorplanning, placement, clock tree,
routing, and finishing. Each target includes earlier physical prerequisites in
an independent new run. You can explore one stage at a time or go directly to
`finish`. The learning dashboard tracks stages completed for its selected project;
it is a record of learning progress, not verification of later edits to that RTL.

Logs show actual tool output. Files contains the netlist, waveform, reports,
databases, and final layout when available. Review setup/hold slack and router
checks separately from command completion. The educational counter uses
clock-only constraints; generating GDS does not complete full foundry signoff.

For your own design:

```sh
chipflow wizard my-design.yaml
chipflow run my-design.yaml --target sim --wait
```

Have your top-module name, RTL paths, testbench, testbench top, clock, and period
ready. Your testbench must print an explicit success marker after its checks.
Paths in a manually written configuration resolve relative to that file. The
wizard creates a new file and refuses to overwrite one. Use the browser project
form if you prefer fields, or Expert mode for the complete YAML configuration.
