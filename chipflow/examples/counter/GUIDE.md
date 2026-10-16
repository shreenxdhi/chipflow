# Your first chip project

`counter.v` is the hardware design (RTL). `tb.v` checks its behavior (a testbench).
`chipflow.yaml` tells Chipflow what to run. Begin with simulation:

```sh
chipflow run chipflow.yaml --target sim --wait
```

Look for `PASS: counter self-check`. The waveform records how signals changed.
A passing simulation tests the cases in this testbench; it does not prove every
possible behavior. Then check physical-design prerequisites:

```sh
chipflow setup
chipflow run chipflow.yaml --target synth --wait
chipflow run chipflow.yaml --target floorplan --wait
chipflow run chipflow.yaml --target place --wait
chipflow run chipflow.yaml --target cts --wait
chipflow run chipflow.yaml --target route --wait
chipflow run chipflow.yaml --target finish --wait
```

Each physical target runs its prerequisites in a new run. `synth` creates gates,
`floorplan` plans their area and power, `place` positions them, `cts` builds the
clock distribution, `route` connects them, and `finish` writes GDSII. These runs
are learning examples, not incremental resumes of an earlier run.

Use `chipflow guide` for terms and expected outputs, `chipflow` for the terminal
dashboard, or `chipflow serve` for the browser's learning path. The counter uses
an educational platform and clock-only constraints; it is not a tapeout example.

## Customization

`chipflow init another-project --expert` enables the included Tcl examples. The
`tcl` directory contains floorplan/clock-tree hooks and a shared helper. Copy your
own scripts into this directory and edit `tcl.hooks` in the configuration. Use
`source [file join [file dirname [info script]] common.tcl]` for sibling helpers.
`chipflow hooks` lists available hook names; `chipflow variables --search clock`
searches the variable reference in your installed ORFS revision. Script code runs
with your user permissions, just like a native ORFS project.
