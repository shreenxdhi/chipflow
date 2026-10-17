"""Evidence-preserving reports and comparisons, without signoff inference."""

from __future__ import annotations

import hashlib
import html
import json
import math

MAX_METADATA_BYTES = 16 * 1024 * 1024
DISCLAIMER = (
    "Execution completion does not establish engineering acceptance or tapeout readiness. "
    "Missing checks are unknown. Router DRC is distinct from qualified physical verification."
)


def _metadata(store, run_id, relative, warnings):
    try:
        path = store.artifact(run_id, relative)
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds 16 MiB")
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value
    except FileNotFoundError:
        warnings.append(f"{relative}: unavailable")
    except (OSError, ValueError) as exc:
        warnings.append(f"{relative}: {exc}")
    return {}


def build_report(store, run_id: str) -> dict:
    """Read a run through Store boundaries; never modify its original evidence."""
    state = store.get(run_id)
    warnings = []
    manifest = _metadata(store, run_id, "manifest.json", warnings)
    toolchain = _metadata(store, run_id, "toolchain.json", warnings)
    environment = state.get("environment_manifest")
    if not isinstance(environment, dict):
        environment = _metadata(store, run_id, "environment-manifest.json", warnings)
    raw_metrics = _metadata(store, run_id, "metrics.json", warnings)
    configuration = manifest.get("configuration", {})
    if not isinstance(configuration, dict):
        configuration = {}
        warnings.append("manifest.configuration: invalid")
    quality = state.get("quality") or {"status": "unknown", "policy": None, "checks": []}
    checks = {"timing": "unknown", "routing_drc": "unknown", "physical": "unknown"}
    checks.update(state.get("checks", {}))
    stages = state.get("stages", [])
    started, finished = state.get("started"), state.get("finished")
    duration = finished - started if _number(started) and _number(finished) else None
    return {
        "report_schema": 1,
        "run_id": run_id,
        "name": state.get("name"),
        "target": state.get("target"),
        "execution_status": state.get("status"),
        "quality": quality,
        "checks": checks,
        "verification": state.get("verification", {}),
        "resources": state.get("resources", {}),
        "workflow": state.get("workflow", {}),
        "configuration": configuration,
        "stages": stages,
        "metrics": state.get("metrics", {}),
        "duration_seconds": duration,
        "error": state.get("error"),
        "diagnostics": state.get("diagnostics", []),
        "environment_manifest": environment,
        "toolchain": toolchain,
        "reproducibility_manifest": manifest,
        "metric_provenance": {
            "summary": "run.json:metrics; native final-report keys retained without unit conversion",
            "sources": raw_metrics,
            "units": state.get("metric_units", {}),
        },
        "artifacts": store.artifacts(run_id),
        "warnings": warnings,
        "power_analysis": {
            "kind": "native_tool_estimates_if_present",
            "activity_annotation": "not established by Chipflow",
            "assumptions": "Inspect native reports for activity, frequency, voltage, Liberty model and corner; no measured power claim",
        },
        "limitations": [DISCLAIMER, "This report does not perform additional EDA checks."],
    }


def render_report(report: dict, format: str = "json") -> str:
    """Return JSON, Markdown or self-contained HTML with escaped project content."""
    serialized = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    if format == "json":
        return serialized + "\n"
    if format == "markdown":
        # Indented code blocks cannot be terminated by project-controlled backticks.
        body = "\n".join("    " + line for line in serialized.splitlines())
        return f"# Chipflow engineering report\n\n{DISCLAIMER}\n\n{body}\n"
    if format == "html":
        title = html.escape(str(report.get("name") or report.get("run_id") or "Run"))
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>Chipflow report: {title}</title>"
            "<style>body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:80rem}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f2f4;"
            "color:#17212b;padding:1rem}summary{cursor:pointer}</style></head><body>"
            f"<h1>Chipflow report: {title}</h1><p>{html.escape(DISCLAIMER)}</p>"
            "<details open><summary>Recorded engineering evidence and artifact references</summary>"
            f"<pre>{html.escape(serialized)}</pre></details></body></html>\n"
        )
    raise ValueError("report format must be json, markdown, or html")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _identity(report):
    manifest = report.get("reproducibility_manifest", {})
    cfg = report.get("configuration", {})
    hashes = manifest.get("input_sha256", {})
    # Store snapshot names differ between runs; category/index/name keys are stable.
    rtl = {key: value for key, value in hashes.items() if str(key).startswith("inputs/rtl/")}
    constraints = {
        key: value
        for key, value in hashes.items()
        if str(key).startswith("inputs/constraints/") or key == "inputs/clocks.sdc"
    }
    config = dict(cfg)
    # Paths themselves are not content identity; input hashes above retain content evidence.
    for key in ("rtl", "sdc", "include_dirs", "orfs"):
        config.pop(key, None)
    tools = report.get("environment_manifest") or report.get("toolchain") or None
    return {
        "design": {"name": cfg.get("name"), "top": cfg.get("top")} if cfg else None,
        "rtl": rtl or None,
        "constraints": constraints or None,
        "configuration": config or None,
        "platform": cfg.get("platform"),
        "environment": tools,
        "all_inputs": hashes or None,
    }


def compare_reports(baseline: dict, candidate: dict) -> dict:
    """Numeric deltas are descriptive; unknown/different identities invalidate comparability."""
    left, right = _identity(baseline), _identity(candidate)
    differences = []
    for key in left:
        if left[key] is None or right[key] is None:
            differences.append({"field": key, "reason": "identity unavailable"})
        elif left[key] != right[key]:
            differences.append({"field": key, "reason": "identities differ"})
    environment_a = baseline.get("environment_manifest")
    environment_b = candidate.get("environment_manifest")
    if environment_a and environment_b:
        from .provenance import compare

        environment_check = compare(environment_a, environment_b)
        for field in environment_check["unknown"]:
            differences.append({"field": "environment", "reason": field})
    else:
        differences.append(
            {
                "field": "environment",
                "reason": "complete manifest unavailable; legacy toolchain identity is partial",
            }
        )
    units_a = baseline.get("metric_provenance", {}).get("units", {})
    units_b = candidate.get("metric_provenance", {}).get("units", {})
    deltas = {}
    for key, value in baseline.get("metrics", {}).items():
        other = candidate.get("metrics", {}).get(key)
        if not (_number(value) and _number(other)):
            continue
        unit_a, unit_b = units_a.get(key), units_b.get(key)
        if unit_a != unit_b:
            differences.append({"field": f"units.{key}", "reason": "units differ or missing"})
            continue
        deltas[key] = {
            "baseline": value,
            "candidate": other,
            "delta": other - value,
            "unit": unit_a,
            "unit_status": "declared" if unit_a else "native metric; unit unverified",
        }
    return {
        "baseline": baseline["run_id"],
        "candidate": candidate["run_id"],
        "compatible": not differences,
        "warnings": differences,
        "metrics": deltas,
        "baseline_execution": baseline.get("execution_status"),
        "candidate_execution": candidate.get("execution_status"),
        "baseline_quality": baseline.get("quality"),
        "candidate_quality": candidate.get("quality"),
        "note": "Deltas are candidate minus baseline; changed constraints can invalidate conclusions.",
    }


def compare_runs(store, baseline: str, candidates: list[str]) -> dict:
    if not candidates or len(candidates) > 64:
        raise ValueError("compare between 1 and 64 candidate runs")
    report = build_report(store, baseline)
    return {
        "baseline": baseline,
        "comparisons": [
            compare_reports(report, build_report(store, candidate)) for candidate in candidates
        ],
    }


def pareto_frontier(runs: list[dict], objectives: dict[str, str]) -> dict:
    """Return nondominated measured points in the provided search space only."""
    if not objectives or any(direction not in {"min", "max"} for direction in objectives.values()):
        raise ValueError("provide metric objectives with min/max directions")
    eligible, excluded = [], []
    for run in runs:
        if run.get("status", run.get("execution_status")) != "completed":
            excluded.append(
                {"id": run.get("id", run.get("run_id")), "reason": "execution incomplete"}
            )
        elif not all(_number(run.get("metrics", {}).get(key)) for key in objectives):
            excluded.append(
                {"id": run.get("id", run.get("run_id")), "reason": "objective metric unavailable"}
            )
        else:
            eligible.append(run)

    def coordinates(run):
        return [
            run["metrics"][key] * (1 if direction == "min" else -1)
            for key, direction in objectives.items()
        ]

    points = [coordinates(run) for run in eligible]
    front = [
        run.get("id", run.get("run_id"))
        for i, run in enumerate(eligible)
        if not any(
            all(a <= b for a, b in zip(other, points[i], strict=True))
            and any(a < b for a, b in zip(other, points[i], strict=True))
            for j, other in enumerate(points)
            if j != i
        )
    ]
    return {
        "run_ids": front,
        "objectives": objectives,
        "evaluated": len(eligible),
        "excluded": excluded,
        "scope": "Provided measured points only; no global optimum claimed.",
    }


def content_identity(value) -> str:
    """Stable digest for JSON configuration/identity records."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
