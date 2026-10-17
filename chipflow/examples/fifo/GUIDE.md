# fifo reference workload

Run `chipflow run chipflow/examples/fifo/chipflow.yaml --target sim --wait` from the checkout.
The testbench is self-checking and produces `waveform.vcd`.
Generated clocks-only constraints are simplified; no I/O delays or complete
constraint coverage are implied. Physical execution requires a separately installed
compatible ORFS/Nangate45 environment and has not been qualified by the simulation
tests. No qualified DRC/LVS or physical signoff claim is made.

The two ordered RTL files instantiate a register-array storage module, not an SRAM macro.
The default four-entry synchronous FIFO tests ordering, full/empty behavior, ignored overflow/underflow and pointer wrap. Parameter combinations other than the default are not qualified; depth must be a power of two matching ADDR. The test is directed, not exhaustive coverage.
