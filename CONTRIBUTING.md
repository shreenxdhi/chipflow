# Contributing

Use Linux and Python 3.11 or newer.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test,dev]'
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

Native integration tests require Icarus Verilog and GNU Make. Full physical-flow
verification additionally requires a compatible ORFS installation, Yosys, OpenROAD,
KLayout, and the selected platform data. Run `chipflow setup` for diagnostics.

Include a focused regression test for behavioral fixes. Keep orchestration in the
shared backend so CLI, terminal, and browser behavior agree. Do not commit run
artifacts, private source files, credentials, virtual environments, or machine-local
paths. Describe the trigger, resulting behavior, and validation in pull requests.

Report tool-specific issues with the platform, ORFS revision, tool versions, selected
stage, and relevant error excerpt. Remove confidential RTL and local paths first.
