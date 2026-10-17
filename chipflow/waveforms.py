"""Bounded VCD data for the workspace, parsed by maintained PyVCD."""

from __future__ import annotations

from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
MAX_SIGNALS = 128
MAX_EVENTS = 4096


def read_waveform(
    path: Path, *, start: int = 0, end: int | None = None, signals: list[str] | None = None
) -> dict:
    from vcd.reader import TokenKind, VCDParseError, tokenize

    if path.stat().st_size > MAX_BYTES:
        raise ValueError(
            "Waveform exceeds the 16 MiB interactive limit; download it for a desktop viewer."
        )
    if start < 0 or end is not None and end < start:
        raise ValueError("Invalid waveform time range")
    definitions, scope, timescale = {}, [], "unspecified"
    now = 0
    count = 0
    truncated = False
    try:
        with path.open("rb") as stream:
            for token in tokenize(stream):
                kind, data = token.kind, token.data
                if kind == TokenKind.SCOPE:
                    scope.append(data.ident)
                elif kind == TokenKind.UPSCOPE:
                    if scope:
                        scope.pop()
                elif kind == TokenKind.TIMESCALE:
                    timescale = str(data)
                elif kind == TokenKind.VAR:
                    if data.id_code not in definitions and len(definitions) < MAX_SIGNALS:
                        definitions[data.id_code] = {
                            "id": data.id_code,
                            "name": ".".join([*scope, data.reference]),
                            "width": data.size,
                            "events": [],
                        }
                    elif data.id_code not in definitions:
                        truncated = True
                elif kind == TokenKind.CHANGE_TIME:
                    if data < now:
                        raise ValueError("Non-monotonic waveform timestamps")
                    now = data
                elif kind in {
                    TokenKind.CHANGE_SCALAR,
                    TokenKind.CHANGE_VECTOR,
                    TokenKind.CHANGE_REAL,
                    TokenKind.CHANGE_STRING,
                }:
                    item = definitions.get(data.id_code)
                    if item is None:
                        continue
                    count += 1
                    if count > MAX_EVENTS:
                        truncated = True
                        break
                    value = (
                        format(data.value, "b") if isinstance(data.value, int) else str(data.value)
                    )
                    item["events"].append([now, value])
    except (ValueError, EOFError, VCDParseError) as exc:
        raise ValueError(f"Cannot read VCD waveform: {exc}") from exc
    maximum = now
    selected = []
    for item in definitions.values():
        if signals and item["id"] not in signals and item["name"] not in signals:
            continue
        events = item["events"]
        preceding = [event for event in events if event[0] < start]
        item["events"] = ([preceding[-1]] if preceding else []) + [
            event for event in events if event[0] >= start and (end is None or event[0] <= end)
        ]
        selected.append(item)
    return {
        "timescale": timescale,
        "start": start,
        "end": maximum if end is None else min(end, maximum),
        "min_time": 0,
        "max_time": maximum,
        "signals": selected,
        "truncated": truncated,
        "limits": {"bytes": MAX_BYTES, "signals": MAX_SIGNALS, "events": MAX_EVENTS},
        "parser": "PyVCD",
        "scope": "recorded waveform events, not assertion or coverage proof",
    }
