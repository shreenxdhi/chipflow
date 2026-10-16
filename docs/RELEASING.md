# Releases

The repository is `shreenxdhi/chipflow`; the distribution and executable are named
`chipflow`. PyPI name availability is not a reservation. Do not claim registry
availability until the upload succeeds.

## Build and verify

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test,dev]'
.venv/bin/ruff check chipflow tests scripts
.venv/bin/ruff format --check chipflow tests scripts
.venv/bin/pytest -q
.venv/bin/python -m build
.venv/bin/twine check dist/*
.venv/bin/python scripts/check_distribution.py dist
.venv/bin/python scripts/wheel_smoke.py dist/*.whl --require-sim
.venv/bin/pip-audit -r requirements-runtime.txt --no-deps --disable-pip
```

CI runs Python 3.11–3.14, native simulation/PTY tests, distribution checks, and an
installed-wheel smoke test outside the repository. The clean-container Dockerfile
is installation validation, not a Docker execution backend for user projects.
Only a reviewed, tested version should be tagged. Keep `chipflow.__version__`,
CHANGELOG, tag, wheel, and source distribution consistent.

## GitHub and PyPI

Push the reviewed repository and verify CI. Tag the reviewed commit, attach the
exact wheel/source distribution and checksums to its GitHub release, then publish
through `.github/workflows/publish.yml`.

Configure a pending publisher in the PyPI project/account:

- Owner: `shreenxdhi`
- Repository: `chipflow`
- Workflow: `publish.yml`
- Environment: `pypi`

Configure TestPyPI separately with environment `testpypi`. In GitHub, create the
matching environments and require a maintainer review for production publication.
Dispatch Publish package with a reviewed tag and `testpypi` first. Confirm the
installed TestPyPI package, then dispatch the same tag for `pypi`. The workflow
builds/tests separately from the publishing job and uses OIDC trusted publishing;
no permanent PyPI token belongs in this repository.

PyPI/TestPyPI account setup and trusted-publisher registration are external actions;
a prepared workflow alone does not publish a package.

References: [PyPA trusted-publishing workflow guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

## Homebrew tap

The tap layout is in `packaging/homebrew`. Generate the formula from the **exact**
source archive attached to the GitHub release:

```sh
.venv/bin/python scripts/generate_formula.py --sdist dist/chipflow-0.1.0.tar.gz
ruby -c packaging/homebrew/Formula/chipflow.rb
```

The generator resolves each pinned runtime package's source URL and SHA-256 from
PyPI. It does not execute those packages. Review the URLs, checksums, compiler
requirements, and license changes before committing them. All recursive runtime
resources are declared; the formula installs them into its own virtual environment.

Publish the tap as `shreenxdhi/homebrew-chipflow`. On a supported Linux host with
Homebrew, run a real installation and formula test before advertising availability:

```sh
brew install --build-from-source shreenxdhi/chipflow/chipflow
brew test shreenxdhi/chipflow/chipflow
brew audit --strict shreenxdhi/chipflow/chipflow
```

This is a third-party tap, not a homebrew/core listing. A Ruby syntax check does
not establish installability. The formula currently targets Linux and Python 3.14;
macOS EDA execution has not been validated. Regenerate resources for every release.

References: [tap maintenance](https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap)
and [Python formula resources](https://docs.brew.sh/Language-Specific-Formulae).
