# Resource visibility and manager benchmark

`chipflow setup --check` includes observed CPU count/affinity, Linux memory,
available disk space, runtime executable locations and visible cgroup-v2 values.
Runtime presence does not establish access to a daemon. Host totals can exceed a
container's limits, so inspect affinity/cgroup observations too. No environment
variable values or process command lines are included in this resource inventory.

Native execution uses configured thread counts and sampled resource visibility;
this does not enforce a native memory budget. Container CPU/memory/PID settings,
when a supported runtime is actually used, have separate runtime semantics.

The process sampler bounds procfs scanning and selected descendants. Its RSS sum
can count shared pages twice, CPU seconds are cumulative live-process values,
and short-lived children can be missed. Periodic sample maxima are sampled peaks,
not exact peak resource measurements. No sample certifies design correctness.

The scheduler helper uses a current-user cache directory at
`$XDG_CACHE_HOME/chipflow` or `~/.cache/chipflow`. The directory is private (0700),
the lock is regular and private (0600), and symlink/hardlink lock replacements are
rejected. Cooperating workers can retain its descriptor through guardian cleanup
to serialize physical work across workspaces for this user. It does not schedule
other users, unrelated native tools, or another configured cache location.

## Reproducible application benchmark

Install test dependencies and run from the checkout:

```sh
python scripts/benchmark_manager.py --runs 1000 --log-mib 8 --samples 10 \
  --output benchmark-manager.json
```

The output file must not already exist. The script creates an isolated temporary
history of explicitly synthetic failed records and a synthetic application log.
It invokes **no EDA tools** and presents no engineering success. History fixture
creation is excluded. CLI timing starts a fresh Python process per sample;
steady-state API timing uses in-process TestClient after a warm-up request,
excluding HTTP transport/browser latency. Results report median, p95 and maximum
milliseconds and retain machine observations. A response-size assertion checks
that log retrieval stays at its existing 256 KiB bound.

On the review host (Linux x86-64, Python 3.12.14, 3 affinity CPUs, visible cgroup
CPU quota equivalent to 2 CPUs and 8 GiB memory), the 1000-record/8 MiB/10-sample
run completed. See the implementation report for measured timings. This is one
host observation, not a cross-platform performance guarantee or large-ASIC
benchmark. Existing artifact/history enumeration remains proportional to the
number of stored records; large deployment acceptance needs its own measurements.
