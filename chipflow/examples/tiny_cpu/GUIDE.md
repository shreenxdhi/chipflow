# tiny_cpu reference workload

Run `chipflow run chipflow/examples/tiny_cpu/chipflow.yaml --target sim --wait` from the checkout.
The testbench is self-checking and produces `waveform.vcd`.
Generated clocks-only constraints are simplified; no I/O delays or complete
constraint coverage are implied. Physical execution requires a separately installed
compatible ORFS/Nangate45 environment and has not been qualified by the simulation
tests. No qualified DRC/LVS or physical signoff claim is made.

A minimal instructional accumulator executes a fixed LOAD 5, ADD 3, SUB 1, HALT program. Each architectural transition and halt stability are checked. This is not a complete processor benchmark, RISC-V core, compiler target or software verification environment.
