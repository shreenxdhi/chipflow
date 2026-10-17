# Execution backends

The native backend remains the default. Existing project configurations retain
native execution, detached workers, bounded subprocess supervision, input
snapshots and one physical implementation at a time per workspace.

Container execution is opt-in. Docker or Podman and a locally installed Linux
EDA image are required. Chipflow does not download images while running designs.
Choose an image with compatible ORFS, Yosys, OpenROAD, simulation tools and PDK
assets, review its licensing and supply-chain origin, pull it yourself, and use
its immutable repository digest. A moving tag such as `latest` is rejected.

```yaml
execution:
  backend: container
  runtime: docker
  image: your-registry/your-reviewed-image@sha256:YOUR_64_HEXADECIMAL_DIGEST
  cpus: 2
  memory_mb: 4096
  pids_limit: 512
  readonly_mounts: []
```

The example digest is a field to replace, not a published image or qualified
platform. Set `orfs` to a host checkout compatible with the tools in your image.
The checkout is mounted read-only at the same absolute path; the run directory
is writable and inputs, outputs and reports retain their existing paths. Extra
PDK directories must be explicitly listed under `readonly_mounts`; they are
mounted read-only at their existing paths. Libraries addressed by Tcl outside
these mounts remain unavailable. Include required native library and tool paths
in the image rather than depending on ambient host installations.

Each tool command really executes inside the selected container. Chipflow
replaces its entrypoint with the tool command and uses the image's PATH. Choose
an image that does not require an entrypoint script to establish its toolchain.
An explicitly declared `execution.environment` mapping can configure paths and
other dependencies. Values must be treated as project configuration and are not
copied into the environment manifest. Avoid placing secrets in project files.

Containers run with networking disabled, the host user's UID/GID, read-only root
filesystem, temporary `/tmp`, dropped capabilities, no privilege escalation and
configured CPU/memory/process limits. The runtime socket is never mounted inside
an EDA container. Run output persists in the host run directory.

The worker's existing guardian supervises the runtime client. On cancellation,
timeout or worker disconnect it terminates the client process group and forcibly
removes that run's uniquely named container. This cleanup also executes after
normal completion. If the runtime daemon itself fails, cleanup may fail and an
operator must inspect remaining `chipflow-*` containers. Killing the guardian
itself with SIGKILL or host failure cannot guarantee immediate cleanup.

This is a boundary for trusted local projects, not an authenticated remote or
multiuser service. A user with Docker socket access already has powerful host
access. Trusted Make/Tcl and user-selected images execute code. Do not mount
sensitive host directories or run untrusted designs without a separately
reviewed sandbox. The API remains localhost-only.

The adapter contract is `prepare(config, argv, cwd, env, run_dir, orfs)`, returning
`ExecutionSpec(argv, cwd, env, guardian_args)`. Always pass its cleanup arguments
to `chipflow.runner` before `--`; bypassing the guardian loses orphan cleanup.
`probe_command` exercises short commands under the same container restrictions;
`container_identity` inspects the local immutable image without pulling it.

Host support beyond the existing Linux baseline, including macOS container
hosts and ARM64 images, requires independent physical-flow qualification.
Container command preparation tests alone do not establish EDA compatibility.

## Repeating the real-container acceptance test

Install the test extra and choose an already installed EDA image:

```bash
CHIPFLOW_TEST_CONTAINER_IMAGE='your-reviewed-image@sha256:YOUR_64_HEXADECIMAL_DIGEST' \
  python -m pytest tests/test_execution_provenance.py \
  -k real_container_simulation_synthesis_and_orphan_cleanup -v
```

If the image is a minimal Linux base and you explicitly mount a compatible
relocatable toolchain, set `CHIPFLOW_TEST_CONTAINER_TOOLS` to that toolchain's
absolute `bin` directory. The test mounts its parent read-only, compiles and runs
a self-checking testbench, validates a generated VCD and Yosys netlist, disconnects
a running command's worker pipe and verifies its container is removed.

This test passed in the implementation environment with the official Ubuntu
24.04 digest `sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac`
and an explicitly mounted OSS CAD Suite (Icarus 14 development build, Yosys
0.70+157). This is actual Linux x86-64 container RTL simulation and synthesis
evidence. It does not qualify containerized physical implementation, other
architectures or macOS hosts. Runtime/tool availability controls whether this
opt-in test runs; a skipped test is not acceptance evidence.
