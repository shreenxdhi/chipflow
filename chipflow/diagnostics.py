"""Bounded, evidence-backed diagnostics for native EDA logs.

These hints classify text; they do not establish verification results and never
change a project's constraints or execution settings.
"""

from __future__ import annotations

import re
from pathlib import Path

# Ordered from specific infrastructure failures to broad tool diagnostics.
_RULES = (
    (
        "disk_exhaustion",
        r"no space left on device|disk quota exceeded",
        "Storage exhausted",
        "Run output or temporary storage has reached its limit.",
        "Check free space and quotas; archive completed runs before retrying.",
    ),
    (
        "out_of_memory",
        r"out of memory|cannot allocate memory|oom[- ]kill",
        "Memory allocation failed",
        "The tool or host reported a memory shortage.",
        "Inspect host memory and kernel logs; reduce concurrent jobs or increase available memory.",
    ),
    (
        "timeout",
        r"command exceeded .+s\b|timed out|timeout expired",
        "Execution timed out",
        "A configured execution limit was reached.",
        "Inspect the last completed operation and configured timeout before retrying.",
    ),
    (
        "missing_include",
        r"include file .+ not found|cannot (?:open|find) include|can't open include",
        "Include file unavailable",
        "An include directory or generated header may be missing.",
        "Check the reported include path and project include_dirs.",
    ),
    (
        "missing_module",
        r"unknown module type|module .+ not found|cannot find (?:root )?module|unable to find (?:the )?root module",
        "RTL module unavailable",
        "The source list, top module, or a required dependency may be incomplete.",
        "Check the top name, RTL source list, libraries, and source ordering.",
    ),
    (
        "missing_library",
        r"cannot (?:open|read|find).*(?:liberty|\.lib\b|\.lef\b)|(?:liberty|library|lef) file .+ (?:not found|does not exist)",
        "Technology library unavailable",
        "A configured technology view is missing or unreadable.",
        "Check platform installation, library paths, permissions, and required view versions.",
    ),
    (
        "invalid_constraints",
        r"(?:sdc|create_clock|set_input_delay|set_output_delay).*(?:error|invalid|unknown)|(?:error|invalid).*(?:sdc|create_clock)",
        "Timing constraint diagnostic",
        "The timing engine rejected or questioned a constraint.",
        "Inspect the native timing report and SDC at the reported location; review clock and I/O assumptions.",
    ),
    (
        "syntax",
        r"syntax error|parse error|unexpected token",
        "Source parsing failed",
        "The source may contain invalid syntax or an unsupported language construct.",
        "Inspect the reported source line and simulator/synthesis language support.",
    ),
    (
        "placement",
        r"placement (?:failed|failure)|failed to (?:place|legalize)|no legal placement",
        "Placement failed",
        "The tool could not produce a valid placement for the supplied configuration.",
        "Inspect utilization, macro/pin placement, floorplan dimensions, and native placement logs.",
    ),
    (
        "routing",
        r"routing (?:failed|failure)|routing congestion|unroutable|failed to route",
        "Routing diagnostic",
        "The router reported failure or congestion.",
        "Inspect routing reports and congestion views; review floorplan and routing constraints.",
    ),
    (
        "clock_tree",
        r"cts (?:failed|failure)|clock tree synthesis (?:failed|failure)",
        "Clock tree synthesis failed",
        "The clock tree could not be constructed under the supplied setup.",
        "Inspect clock definitions, clock buffers, libraries, and the native CTS log.",
    ),
    (
        "missing_output",
        r"required output is missing or empty|final layout is not a gdsii|missing (?:final )?gds",
        "Expected output unavailable",
        "A stage did not produce the expected usable artifact.",
        "Inspect tool exit status and output paths; retry only after resolving the upstream failure.",
    ),
    (
        "assertion",
        r"\bassertion (?:failed|failure)|\b(?:fatal|error):.*(?:assert|test)|\btest(?:bench)? failed\b",
        "Verification failure diagnostic",
        "The testbench or assertion engine reported a failure.",
        "Inspect the failing test, seed, source location, and waveform; correct the design or test before retrying.",
    ),
    (
        "timing",
        r"(?:setup|hold|timing) (?:violation|violated)|negative (?:setup |hold )?slack",
        "Timing violation diagnostic",
        "The tool reported a violated timing requirement.",
        "Inspect the path, clock, corner, and constraints in the timing report; do not silently relax the target.",
    ),
    (
        "tool_error",
        r"^\s*(?:ERROR|FATAL)(?:\s|:|\[)|\[ERROR\s",
        "Tool reported an error",
        "The native tool emitted an error diagnostic.",
        "Read the surrounding native log and tool documentation before retrying.",
    ),
)
_COMPILED = [
    (name, re.compile(pattern, re.I), summary, cause, step)
    for name, pattern, summary, cause, step in _RULES
]
_SOURCE = re.compile(
    r"(?P<path>[^\s:]+\.(?:svh?|vh?|tcl|sdc)):(?P<line>\d+)(?::(?P<column>\d+))?", re.I
)


def diagnose_log(path: Path | str, *, max_bytes: int = 256 * 1024, max_findings: int = 50) -> dict:
    """Read at most ``max_bytes`` of a log tail and return advisory findings.

    Line numbers are absolute only when the complete file was read. Truncated
    results report a tail-relative line instead. Missing/unreadable files raise
    their normal OSError so callers can present an actionable storage error.
    """
    if not 1 <= max_bytes <= 16 * 1024 * 1024:
        raise ValueError("max_bytes must be between 1 and 16777216")
    if not 1 <= max_findings <= 1000:
        raise ValueError("max_findings must be between 1 and 1000")
    path = Path(path)
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        start = max(0, size - max_bytes)
        stream.seek(start)
        data = stream.read(max_bytes)
    # The initial fragment might be a partial diagnostic or UTF-8 character.
    if start and b"\n" in data:
        data = data.split(b"\n", 1)[1]
    elif start:
        data = b""
    findings = []
    clipped = False
    for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        # Bound regex work and evidence size even for enormous single lines.
        text = line[:4096]
        for category, pattern, summary, cause, step in _COMPILED:
            if not pattern.search(text):
                continue
            if len(findings) == max_findings:
                clipped = True
                break
            source = _SOURCE.search(text)
            evidence = {
                "log": str(path),
                "line": number if not start else None,
                "tail_line": number if start else None,
                "text": text[:1000],
            }
            if source:
                evidence["source"] = {
                    "path": source.group("path"),
                    "line": int(source.group("line")),
                }
                if source.group("column"):
                    evidence["source"]["column"] = int(source.group("column"))
            findings.append(
                {
                    "category": category,
                    "severity": "error",
                    "summary": summary,
                    "evidence": evidence,
                    "likely_causes": [cause],
                    "steps": [step],
                }
            )
            break
        if clipped:
            break
    return {
        "findings": findings,
        "truncated": bool(start),
        "findings_truncated": clipped,
        "bytes_read": min(size, max_bytes),
        "file_bytes": size,
        "advisory_only": True,
    }
