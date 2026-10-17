# Chipflow

A local RTL-to-GDS workflow manager for Linux, with terminal and browser dashboards.
Chipflow coordinates Icarus Verilog and OpenROAD-flow-scripts (ORFS), preserving
inputs, logs, metrics, and artifacts for each run. It requires no hosted service
or external API during normal operation.

## Install

On Linux with Homebrew:

```sh
brew install shreenxdhi/chipflow/chipflow
chipflow setup
```

The [1.1.1 release](https://github.com/shreenxdhi/chipflow/releases/tag/v1.1.1)
and [Homebrew tap](https://github.com/shreenxdhi/homebrew-chipflow) are available.
The first Homebrew installation builds dependencies from source and may take
several minutes.

With Python 3.11 or newer, install from [PyPI](https://pypi.org/project/chipflow/)
using pipx:

```sh
pipx install chipflow
chipflow setup
```

Alternatively, install with pip inside a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install chipflow
chipflow setup
```

Version [1.1.1 is published on PyPI](https://pypi.org/project/chipflow/1.1.1/).
See [release instructions](docs/RELEASING.md).
After installing the manager, run `chipflow setup --install` to choose EDA tools
and installation variants. Each plan needs your approval before installation.
See the [tool installation guide](docs/INSTALLATION.md), including a prebuilt
Icarus/Yosys option that needs no administrator access.

## Check and configure prerequisites

```sh
chipflow setup
chipflow setup --target sim --install
chipflow setup --orfs /opt/OpenROAD-flow-scripts --bin-dir /opt/eda/bin --save
chipflow setup --json
```

Setup checks tool availability, the ORFS flow, and selected platform views. Missing
items include installation suggestions and links to upstream documentation. ORFS,
Yosys, and OpenROAD should come from a compatible toolchain revision. Interactive
setup offers installation choices and runs only the plan you approve.
Use `chipflow setup --check` for a read-only report or `--options` to list variants.
The browser lists the same choices and provides commands to run in a terminal.

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

## Start learning

```sh
chipflow quickstart first-chip
chipflow guide
```

`quickstart` checks Icarus Verilog, creates a self-checking counter, and runs a
real RTL simulation. It prints the result and where to find the outputs. It never
overwrites an existing directory. The generated `GUIDE.md` explains the files,
flow stages, and what to inspect at each stage.

In the browser, choose **Learning** and click **Start learning with a counter**.
The seven-step path explains simulation through GDSII, tracks completed stages,
and offers the next run. Missing prerequisites appear before execution. Each
physical target includes its earlier prerequisites in a new independent run.

For your own RTL, use the interactive configuration wizard:

```sh
chipflow wizard chipflow.yaml
chipflow run chipflow.yaml --target sim --wait
```

After configuring physical-design tools with `chipflow setup`, continue with
`chipflow run first-chip/chipflow.yaml --target finish --wait`, or create and run
another full example with `chipflow quickstart another-chip --target finish`.
See the [beginner guide](docs/GETTING_STARTED.md).

## Configure an experienced workflow

```sh
chipflow init custom-chip --expert
chipflow hooks
chipflow variables --search clock
```

Expert projects support native ORFS Tcl hooks, replacement policy scripts, typed
flow-variable overrides, additional library files, complete SDC constraints,
multiple clocks, and configurable resource/time limits. Scripts, sibling helpers,
and declared file inputs are copied and hashed with each run, so retries use the
preserved configuration.

Choose **Expert** in the browser for the complete YAML editor and searchable
installed-flow reference. Saving creates a project copy. The guided form also
supports Tcl bundles and native settings. See [custom Tcl](docs/CUSTOMIZATION.md)
and the [configuration reference](docs/CONFIGURATION.md).

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
chipflow backup chipflow-history.tar.gz
chipflow --runs /safe/path/restored-runs restore chipflow-history.tar.gz
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
means the requested commands exited successfully and expected physical outputs exist.
Saved inputs and configuration are integrity-checked before execution and retry.
Timing, router DRC, and overall
physical checks are reported separately. Full foundry DRC, LVS, and tapeout readiness
are not certified by Chipflow.

The validated native flows are a Nangate45 counter and a Sky130 dual-channel DMA
controller. Other platforms and designs require their own verification. The
clock-only counter constraints do not establish I/O timing coverage.

The 1.x release supports single-user Linux operation with trusted inputs and
toolchains. Paths used by ORFS must avoid spaces and Make/shell metacharacters.
Tool installation requires an explicit choice and approval. Automatic PDK
integration, remote workers, and a container execution backend are not included.
A container is used to test clean manager installation.
See [operations](docs/OPERATIONS.md) for the supported host/toolchain matrix,
upgrades, backups, crash recovery, monitoring, and maintenance.

## Development and releases

See [contributing](CONTRIBUTING.md), [validation](docs/VALIDATION.md),
[security](SECURITY.md), and [release process](docs/RELEASING.md).

Chipflow is [Apache-2.0 licensed](LICENSE). EDA tools, cell libraries, and PDKs retain
their own licenses and are not distributed in the package.
