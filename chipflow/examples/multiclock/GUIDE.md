# multiclock reference workload

Run `chipflow run chipflow/examples/multiclock/chipflow.yaml --target sim --wait` from the checkout.
The testbench is self-checking and produces `waveform.vcd`.
Generated clocks-only constraints are simplified; no I/O delays or complete
constraint coverage are implied. Physical execution requires a separately installed
compatible ORFS/Nangate45 environment. The simulation tests alone do not qualify
physical implementation. No qualified DRC/LVS or physical signoff claim is made.

The two counters have independent clocks. Expected edge counts are checked at a time away from active edges. This does not test clock-domain crossings, asynchronous reset release, CDC/RDC safety or multiple-corner timing. The generated clocks do not establish an external clock relationship.

The reference physical configuration uses a 100 × 100 µm die and an 80 × 80 µm core,
with target placement density 0.3. These are explicit tutorial geometry choices,
not an optimized floorplan or manufacturing qualification. Existing clock targets
are unchanged. The generated clock-only SDC omits input/output delays and can
leave endpoints unconstrained; inspect the constraint report before interpreting
timing as complete. Full foundry DRC/LVS is not established by this example.

A real native Nangate45 run of this default example completed on the documented
Linux x86-64 reference environment; see [recorded acceptance evidence](../../../docs/NATIVE_ACCEPTANCE_2026-10-17.md).
This does not qualify other parameter sets, hosts, PDKs or complete timing/signoff.
