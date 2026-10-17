# fifo reference workload

Run `chipflow run chipflow/examples/fifo/chipflow.yaml --target sim --wait` from the checkout.
The testbench is self-checking and produces `waveform.vcd`.
Generated clocks-only constraints are simplified; no I/O delays or complete
constraint coverage are implied. Physical execution requires a separately installed
compatible ORFS/Nangate45 environment. The simulation tests alone do not qualify
physical implementation. No qualified DRC/LVS or physical signoff claim is made.

The two ordered RTL files instantiate a register-array storage module, not an SRAM macro.
The default four-entry synchronous FIFO tests ordering, full/empty behavior, ignored overflow/underflow and pointer wrap. Parameter combinations other than the default are not qualified; depth must be a power of two matching ADDR. The test is directed, not exhaustive coverage.

The reference physical configuration uses a 100 × 100 µm die and an 80 × 80 µm core,
with target placement density 0.3. These are explicit tutorial geometry choices,
not an optimized floorplan or manufacturing qualification. Existing clock targets
are unchanged. The generated clock-only SDC omits input/output delays and can
leave endpoints unconstrained; inspect the constraint report before interpreting
timing as complete. Full foundry DRC/LVS is not established by this example.

A real native Nangate45 run of this default example completed on the documented
Linux x86-64 reference environment; see [recorded acceptance evidence](../../../docs/NATIVE_ACCEPTANCE_2026-10-17.md).
This does not qualify other parameter sets, hosts, PDKs or complete timing/signoff.
