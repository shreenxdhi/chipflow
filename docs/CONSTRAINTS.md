# Timing constraints and evidence

Chipflow retains complete expert SDC files and passes them to native ORFS tools.
When a project instead provides `clocks`, the generated SDC contains only
`create_clock` commands. Those are simplified learning constraints. They do not
establish interface delays, safe clock-domain crossings or complete timing
coverage.

For a real design, review the required primary/generated clocks, input and output
delays, clock uncertainty/latency, input transition, output load, false paths,
multicycle paths, clock groups and min/max delay requirements. Their values must
come from the design's actual interfaces, implementation environment and timing
intent. Chipflow does not infer them as engineering facts.

The advisory constraint inventory recognizes top-level statements for those
commands, along with case analysis and disabled timing arcs. It records original
text and line numbers. It flags timing exceptions for review and warns when clock
or I/O-delay statements are absent from the inventory. An absence warning does
not prove a design is missing constraints: sourced files and dynamic Tcl can
provide them. Conversely, a recognized statement does not prove its collections
resolve to real design objects.

The inventory never executes Tcl, expands variables or sources referenced files.
It is deliberately not an SDC parser, elaborator or replacement for OpenSTA.
Dynamic blocks, procedures, external files and unsupported commands require
native timing-engine review. Its delimiter warning is lexical advice rather than
authoritative syntax validation.

Native OpenSTA/OpenROAD analysis on the linked netlist, matching Liberty and
technology data is needed to resolve actual clocks, generated clock relationships,
constrained/unconstrained endpoints, exception applicability, setup/hold analysis
and timing corners. The static report leaves these fields unknown/not run.
Nonnegative setup/hold slack says only that reported paths met the reported
constraints and corner. It does not establish constraint completeness, CDC safety,
all-corner closure or qualified signoff.

## Developer interface

`chipflow.constraints.analyze_constraints(text, generated=False)` returns a
serializable report with `kind: advisory_static_inventory`, `authoritative:
false`, categorized command records, warnings and unknown coverage fields.
`inspect_constraints(path, generated=False)` reads a file and adds its source
path. Set `generated=True` when the manager produced clock-only SDC, so its
simplified status is prominent. Neither function modifies source constraints.

The lexical and negative security tests are in
`tests/test_quality_constraints.py`; tests explicitly ensure suspicious Tcl is
never executed and text inventory never becomes endpoint-coverage evidence.

## Guided explicit constraint generation

`GuidedSDCConfig` and `build_guided_sdc(config)` cover primary clocks, explicit
minimum/maximum I/O delays, optional clock uncertainty/latency, input transition
and output load. Every time/load value is supplied by the user. The model requires
unit labels and a nonempty list of reviewed assumptions. Labels are recorded as
comments and must match the loaded Liberty/native engine; the builder never
silently converts units or claims they were verified.

```yaml
time_unit: ns
capacitance_unit: pF
assumptions:
  - REPLACE with reviewed external-interface requirements and operating conditions
clocks:
  - name: main
    port: clk
    period: 10
    uncertainty: 0.1
io_delays:
  - direction: input
    ports: [data]
    clock: main
    minimum: 0
    maximum: 1
  - direction: output
    ports: [result]
    clock: main
    minimum: 0
    maximum: 2
input_transitions: {data: 0.1}
output_loads: {result: 0.05}
```

These numbers are syntax examples, not suggested design targets. Use project
requirements for actual values. `write_guided_sdc(config, path)` exclusively creates
a new file and returns its advisory inventory; it refuses to overwrite an existing
SDC. Missing I/O delays are explicitly commented. No automatic timing exception
or asynchronous clock group is inferred. Multiple primary clocks are accepted,
but generated clocks, bus collections, advanced exceptions and multiple clock
assignments to an I/O require expert SDC. The builder rejects ambiguous repeated
I/O assignments instead of silently replacing constraints.
