# Chipflow Homebrew tap

This directory is the source for the `shreenxdhi/homebrew-chipflow` tap. The formula
installs the manager into an isolated Python environment; EDA tools and platform
data remain separately installed and are assessed with `chipflow setup`.

The formula's release URL must exist, checksums must match the exact release assets,
and an installed formula test must pass before the tap is advertised as supported.
See the main repository's release guide for generation and verification.
