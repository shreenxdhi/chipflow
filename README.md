# Chipflow

A local RTL-to-GDS workflow manager for Linux, with terminal and browser dashboards.
Chipflow coordinates Icarus Verilog and OpenROAD-flow-scripts (ORFS), preserving
inputs, logs, metrics, and artifacts for each run. It requires no hosted service
or external API during normal operation.

## Install

Python 3.11 or newer is required. Until a public release is published, install from
the repository:

```sh
pipx install git+https://github.com/shreenxdhi/chipflow.git
```

The release package is named `chipflow`; once published to PyPI:

```sh
pipx install chipflow
```

A versioned Homebrew tap is maintained separately. See [release instructions](docs/RELEASING.md)
for its availability and validation requirements. Installing the manager does not
install or modify EDA tools or platform data.

## Check and configure prerequisites

```sh
chipflow setup
chipflow setup --target sim
chipflow setup --orfs /opt/OpenROAD-flow-scripts --bin-dir /opt/eda/bin --save
chipflow setup --json
```

Setup checks tool availability, the ORFS flow, and selected platform views. Missing
items include installation suggestions and links to upstream documentation. ORFS,
Yosys, and OpenROAD should come from a compatible toolchain revision. Suggestions
are displayed for review; the manager does not run package managers or `sudo`.

`ORFS_HOME` overrides saved settings. Otherwise Chipflow discovers common ORFS
locations. Saved settings live in `$XDG_CONFIG_HOME/chipflow/settings.json`, or
`~/.config/chipflow/settings.json`. Saved tool directories apply to Chipflow's own
processes without changing your shell configuration.

Availability is not proof of version compatibility. Exercise the installed tools:

```sh
chipflow verify --target sim
chipflow verify --target finish
```

The second command runs a real Nangate45 physical flow and may take several minutes.

## First design run

```sh
chipflow init counter-demo
chipflow run counter-demo/chipflow.yaml --target sim --wait
chipflow run counter-demo/chipflow.yaml --target finish --wait
```

`init` copies a bundled self-checking counter example into a new directory. It never
overwrites an existing directory. Edit the generated configuration for your own RTL,
constraints, and platform. The example is available after wheel installation, not
only from a source checkout.

## Dashboards

```sh
chipflow                            # interactive terminal dashboard
chipflow dashboard --view logs      # explicit terminal command
chipflow serve --port 8080           # http://127.0.0.1:8080
```

The terminal and browser share `.chipflow/runs` in the working directory. To use a
shared history elsewhere, place `--runs /path/to/runs` before the subcommand.
Workers continue after a browser, server, or terminal dashboard closes.

The terminal supports keyboard selection, progress, live log following and search,
files, metrics, launching projects, reruns, and cancellation. See the
[terminal guide](docs/TERMINAL.md). The browser includes a guided project form,
preflight checks, result downloads, searchable history, and comparisons.

## Commands

```sh
chipflow history
chipflow status RUN_ID
chipflow logs RUN_ID > run.log
chipflow artifacts RUN_ID
chipflow cancel RUN_ID
chipflow retry RUN_ID
chipflow compare LEFT_ID RIGHT_ID
chipflow doctor
```

`run` returns immediately unless `--wait` is supplied. Ctrl+C during `run --wait`
requests cancellation; Ctrl+C or q in the dashboard exits without cancelling jobs.
`retry` creates a new run from preserved inputs, even if the original files changed.

## Project configuration

```yaml
name: counter_demo
top: counter
rtl: [counter.v]
platform: nangate45
clocks:
  - port: clk
    period_ns: 10
simulation:
  top: tb
  testbench: [tb.v]
  pass_marker: 'PASS: counter self-check'
physical:
  DIE_AREA: '0 0 100 100'
  CORE_AREA: '10 10 90 90'
  PLACE_DENSITY: 0.3
threads: 2
timeout_seconds: 3600
```

`orfs` can override the discovered repository/flow path. Input paths resolve relative
to the configuration; `~` is expanded. Explicit `include_dirs`, an `sdc` file,
functional `simulation.cell_models`, and other supported physical settings are
optional. Unknown keys and unsafe Make expressions are rejected.
See the [configuration reference](docs/CONFIGURATION.md).

`sim` passes only when compilation and runtime succeed and the configured marker
appears in runtime output. Functional `gate_sim` synthesizes first, then runs the
same testbench with compatible cell models. It does not apply SDF delays.

Physical targets run their prerequisites:

```text
synth -> floorplan -> place -> cts -> route -> finish
```

RTL simulation is separate from the physical flow. One physical run executes per
run store at a time to limit resource contention; additional physical runs queue
and remain cancellable. Simulations can execute alongside a physical run.

## Results and limitations

Each run records source/configuration snapshots and hashes, tool versions, ORFS
revision, stage timings, command output, metrics, and artifacts. A completed flow
means the requested commands exited successfully. Timing, router DRC, and overall
physical checks are reported separately. Full foundry DRC, LVS, and tapeout readiness
are not certified by Chipflow.

The validated native flows are a Nangate45 counter and a Sky130 dual-channel DMA
controller. Other platforms and designs require their own verification. The
clock-only counter constraints do not establish I/O timing coverage.

The first release is a beta: Linux is the validated runtime, inputs must be trusted,
and paths used by ORFS must avoid spaces and Make/shell metacharacters. Automatic
EDA installation, arbitrary PDK hooks, remote workers, and a container execution
backend are not included. A container is used to test clean manager installation.

## Development and releases

See [contributing](CONTRIBUTING.md), [validation](docs/VALIDATION.md),
[security](SECURITY.md), and [release process](docs/RELEASING.md).

Chipflow is [Apache-2.0 licensed](LICENSE). EDA tools, cell libraries, and PDKs retain
their own licenses and are not distributed in the package.
