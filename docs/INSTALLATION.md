# Choose and install your EDA tools

Chipflow can discover missing tools, list installation variants, show a plan, and
run the installer you approve. Installing the Python package itself installs the
manager and its Python dependencies; run setup afterward to choose the EDA tools.

For a first simulation:

```sh
pipx install chipflow
chipflow setup --target sim --install
chipflow quickstart first-chip
```

For synthesis through GDS:

```sh
chipflow setup --install
```

Interactive `chipflow setup`, first launch with `chipflow`, and a quickstart
blocked by missing tools offer to open installation choices. Press Enter to
decline or leave the menu. Each choice explains what it provides, its source,
requirements, and commands. Nothing installs until you approve that plan. Native
package managers may also request confirmation or your administrator password.
After each installation, setup rechecks executables and platform files and lets
you choose another missing tool. It reports failures without claiming success.

## Available variants

| Choice | Provides | Requirements |
| --- | --- | --- |
| Distribution packages | Icarus (`iverilog` and `vvp`), GNU Make, KLayout, Git as needed | apt-get on Debian/Ubuntu derivatives, dnf on Fedora/Enterprise Linux, pacman on Arch/Manjaro, or zypper on openSUSE; root or sudo |
| Linux Homebrew | Icarus, GNU Make, KLayout, Git as needed | Existing Homebrew installation and a non-root user; formula availability varies |
| OSS CAD Suite prebuilt release | Icarus and Yosys, plus the upstream suite's other tools | Linux x86-64, updated Python with safe tar extraction, network and enough disk space; no sudo |
| Pinned ORFS source build | ORFS, Yosys, OpenROAD, KLayout and bundled platform data | Supported 64-bit Ubuntu 20.04/22.04/24.04 or Enterprise Linux 8/9; Git, sudo, build resources |
| ORFS checkout only | Flow scripts and bundled platform data | Linux and Git; install compatible binaries separately |
| Local upstream package | OpenROAD or KLayout from a downloaded `.deb`/`.rpm` | Matching OS/architecture and apt-get or dnf with administrator access |
| Existing tools | Saved executable directories and optional ORFS path | An existing installation, including site-managed toolchains |

The menu marks unavailable choices and explains their prerequisites. Package
availability varies by distribution; a failed package-manager command remains a
failure. Chipflow does not silently substitute another installer.

The OSS CAD Suite option resolves the latest release to a specific dated asset
and displays its size, URL and upstream SHA-256 before approval. The installer
verifies the digest, restricts archive extraction, and saves the executable path
after success. It never overwrites an existing destination. This is a large
installation (roughly 750 MB compressed), not just the two executables. If the
release has no digest or suitable asset, select another variant. A newer Yosys
release does not establish compatibility with an older ORFS flow.

The ORFS source option pins the revision documented in [validation](VALIDATION.md):
`1ff305ccbe694958d0125f0695dceefb58dfb3f6`. It uses a dedicated user directory,
initializes the pinned submodules, runs the upstream `setup.sh` with sudo, and
builds natively with at most four compilation threads. Review the linked upstream
setup script: it installs system build dependencies and KLayout, can add package
repositories or replace existing packages, and may download/build dependencies.
Allow substantial RAM, several GB of disk space, and a long build. It does not
install Icarus; select Icarus separately if you want simulation. A checkout-only
installation does not build binaries.

For prebuilt OpenROAD/KLayout packages, download from the menu's upstream link,
check the upstream checksum, and choose the local file. Chipflow checks its package
name and format before invoking your package manager. Select a package matching
your distribution and CPU. It does not guess a compatible binary from an unrelated
OS. These packages still need a compatible ORFS checkout and other tools.

These are installation variants of the supported tools. The current execution
backend uses Icarus for simulation and ORFS/Yosys/OpenROAD for physical design;
selecting Verilator, LibreLane, OpenLane or a commercial tool as a replacement is
not implemented. Other simulator backends, remote workers and multiuser operation
remain separate future work.

## Inspect or preselect a choice

```sh
# Read-only: list choices, including unavailable variants and their guides.
chipflow setup --target sim --options
chipflow setup --json
chipflow setup --check

# Still asks for approval before installation.
chipflow setup --target sim --install --option system-iverilog
chipflow setup --target sim --install --option oss-cad-suite
chipflow setup --install --option orfs-source --install-dir ~/eda/orfs-pinned
chipflow setup --install --option prebuilt-openroad --package ~/Downloads/openroad.deb

# Print the selected plan without executing it or saving settings.
chipflow setup --target sim --install --option system-iverilog --dry-run
```

`--install` requires an interactive terminal. JSON, `--check`, and `--options`
never prompt. Redirected/noninteractive default setup also checks only. This
keeps scripts and CI from hanging or modifying the host unexpectedly. `--dry-run`
can resolve release metadata or inspect local packages but does not install,
extract, or write settings. It cannot be combined with `--save`.

For an existing toolchain, choose **Use existing tools**, or configure it directly:

```sh
chipflow setup --orfs /opt/OpenROAD-flow-scripts --bin-dir /opt/eda/bin --save
```

Newly selected executable directories take precedence; previous saved directories
are retained as fallbacks. A source-built ORFS toolchain takes precedence over a
previously selected suite so its matching Yosys is used.
`ORFS_HOME` takes precedence over saved settings. If it points at another checkout,
update or unset it before using a newly installed flow.

## Browser and terminal dashboard

In the browser, open **Installed tools**, choose a variant, and use **Copy
installation command**. Run that command in a terminal on the machine hosting
Chipflow; it shows the plan and asks for approval. Return to the browser and click
**Recheck installed tools**. The browser never starts a privileged installer or
collects passwords. The terminal dashboard's prerequisite report points to
`chipflow setup --install`; leave curses to run it in your shell.

## Platforms, unsupported hosts and verification

Windows users can run Chipflow inside a Linux WSL2 distribution; macOS users can
use a Linux VM. Native macOS/Windows execution remains unvalidated. Other Linux
distributions and CPU architectures can use matching distribution packages or
existing tool paths; the automated prebuilt suite currently targets Linux x86-64.
An installer being available does not expand the validated host/toolchain matrix.

ORFS supplies some platform files, including learning platforms, but not every
PDK or license. If the selected platform lacks Liberty, LEF or GDS views, setup
continues to report the missing data. Obtain it from the platform provider and
follow its license and ORFS integration instructions; Chipflow does not accept
licenses or download proprietary platform data for you.

After setup reports tools available, exercise them:

```sh
chipflow verify --target sim
chipflow verify --target finish
```

Availability and successful version probes are prerequisites, not proof of
compatible tool versions or foundry signoff. See [operations](OPERATIONS.md) and
[validation](VALIDATION.md) for the supported contract.

On failure, completed package-manager operations are retained. No automatic
uninstall or rollback runs. Fix the reported error and rerun setup. A pinned ORFS
checkout with matching revision and unchanged tracked sources can be reused with
the same `--install-dir` to retry a build. An unrelated or modified checkout is
not reset. Temporary failed suite downloads are removed and no suite paths are
saved; choose a fresh directory to install another suite release.
