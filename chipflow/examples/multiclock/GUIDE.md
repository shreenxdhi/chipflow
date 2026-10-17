# multiclock reference workload

Run `chipflow run chipflow/examples/multiclock/chipflow.yaml --target sim --wait` from the checkout.
The testbench is self-checking and produces `waveform.vcd`.
Generated clocks-only constraints are simplified; no I/O delays or complete
constraint coverage are implied. Physical execution requires a separately installed
compatible ORFS/Nangate45 environment and has not been qualified by the simulation
tests. No qualified DRC/LVS or physical signoff claim is made.

The two counters have independent clocks. Expected edge counts are checked at a time away from active edges. This does not test clock-domain crossings, asynchronous reset release, CDC/RDC safety or multiple-corner timing. The generated clocks do not establish an external clock relationship.
