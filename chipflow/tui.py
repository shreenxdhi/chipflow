"""Dependency-free curses dashboard over the same persistent execution backend."""

from __future__ import annotations

import codecs
import curses
import re
import sys
import textwrap
import time
import unicodedata
from collections import deque
from pathlib import Path

from .engine import STAGES, TERMINAL, Store
from .projects import inspect_project, list_projects

LABELS = {
    "sim": "RTL simulation",
    "synth": "Synthesis",
    "gate_sim": "Gate simulation",
    "floorplan": "Floorplan + power",
    "place": "Placement",
    "cts": "Clock tree",
    "route": "Routing",
    "finish": "Final layout / GDSII",
}
PHYSICAL = ["synth", "floorplan", "place", "cts", "route", "finish"]
VIEWS = ["overview", "logs", "files", "metrics"]
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def plain(value: object) -> str:
    """Render tool output as text, never as terminal control sequences."""
    text = ANSI.sub("", str(value)).expandtabs(4)
    return "".join(c for c in text if c.isprintable() or c in "\n")


def clip(value: object, width: int) -> str:
    result, used = [], 0
    for char in plain(value).replace("\n", " "):
        size = (
            0
            if unicodedata.combining(char)
            else 2
            if unicodedata.east_asian_width(char) in "WF"
            else 1
        )
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result)


def elapsed(run: dict) -> str:
    seconds = max(0, int(run.get("finished", time.time()) - run.get("started", run["created"])))
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def stage_names(target: str) -> list[str]:
    return (
        ["sim"]
        if target == "sim"
        else ["synth", "gate_sim"]
        if target == "gate_sim"
        else PHYSICAL[: PHYSICAL.index(target) + 1]
    )


def resolve_run(store: Store, value: str | None) -> str | None:
    if not value:
        return None
    matches = [run["id"] for run in store.history() if run["id"].startswith(value)]
    if len(matches) != 1:
        raise ValueError(
            "Run ID must match exactly one saved run. Use the full ID or a unique prefix."
        )
    return matches[0]


class LogTail:
    """Incrementally read bounded log output, retaining enough context to scroll."""

    def __init__(self, max_lines: int = 8000):
        self.max_lines = max_lines
        self.lines: deque[str] = deque(maxlen=max_lines)
        self.offset = 0
        self.path: Path | None = None
        self.partial = ""
        self.trimmed = False
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def read(self, path: Path) -> None:
        if path != self.path:
            self.path = path
            self.decoder.reset()
            self.lines.clear()
            self.partial = ""
            self.offset = 0
            self.trimmed = False
            if path.exists() and path.stat().st_size > 128 * 1024:
                self.offset = path.stat().st_size - 128 * 1024
                self.trimmed = True
                # Avoid showing a truncated first line.
                with path.open("rb") as stream:
                    stream.seek(self.offset)
                    stream.readline()
                    self.offset = stream.tell()
        if not path.exists():
            return
        if path.stat().st_size < self.offset:
            self.offset = 0
            self.decoder.reset()
            self.partial = ""
            self.lines.clear()
            self.trimmed = False
        with path.open("rb") as stream:
            stream.seek(self.offset)
            data = stream.read(256 * 1024)
            self.offset = stream.tell()
        if not data:
            return
        text = self.partial + plain(self.decoder.decode(data)).replace("\r", "")
        pieces = text.split("\n")
        self.partial = pieces.pop()
        if len(self.partial) > 32768:
            self.partial = self.partial[-32768:]
            self.trimmed = True
        if len(self.lines) + len(pieces) > self.max_lines:
            self.trimmed = True
        self.lines.extend(pieces)

    def matching(self, query: str = "") -> list[str]:
        lines = [*self.lines, *([self.partial] if self.partial else [])]
        return [line for line in lines if query.lower() in line.lower()] if query else lines


class Dashboard:
    def __init__(
        self, store: Store, run_id: str | None = None, view: str = "overview", refresh: float = 1
    ):
        self.store = store
        self.selected = run_id
        self.view = view
        self.refresh_seconds = refresh
        self.runs: list[dict] = []
        self.focus = "details" if run_id or view != "overview" else "runs"
        self.filters = {"runs": "", **{view: "" for view in VIEWS}}
        self.scroll = 0
        self.follow = True
        self.paused_log_lines: list[str] | None = None
        self.log = LogTail()
        self.files: list[dict] = []
        self.files_at = 0.0
        self.files_key = None
        self.modal: str | None = None
        self.message = "Jobs keep running when you quit. Press ? for help."
        self.message_error = False
        self.input = ""
        self.cursor = 0
        self.project_paths: list[str] = []
        self.project_index = -1
        self.target = "sim"
        self.new_field = "path"
        self.last_refresh = 0.0
        self.screen = None
        self.colors = {}
        self.running = True
        self.confirm_run: str | None = None

    @property
    def run(self) -> dict | None:
        return next((run for run in self.runs if run["id"] == self.selected), None)

    @property
    def filtered_runs(self) -> list[dict]:
        query = self.filters["runs"].lower()
        return [
            run
            for run in self.runs
            if query
            in f"{run['name']} {run['id']} {run['status']} {run['target']} {LABELS[run['target']]}".lower()
        ]

    def tell(self, text: object, error: bool = False):
        self.message = plain(text).replace("\n", " ")
        self.message_error = error

    def select(self, run_id: str):
        if self.selected != run_id:
            self.selected = run_id
            self.scroll = 0
            self.follow = True
            self.paused_log_lines = None
            self.files = []
            self.files_key = None

    def refresh(self):
        try:
            self.runs = self.store.history()
            if not self.run and self.runs:
                self.select(
                    next(
                        (r["id"] for r in self.runs if r["status"] not in TERMINAL),
                        self.runs[0]["id"],
                    )
                )
            if self.run:
                directory = self.store.directory(self.selected)
                self.log.read(directory / "console.log")
                key = (self.selected, self.run["status"])
                if (
                    key != self.files_key
                    or self.view in {"files", "overview"}
                    and time.monotonic() - self.files_at >= 3
                ):
                    self.files = self.store.artifacts(self.selected)
                    self.files_at = time.monotonic()
                    self.files_key = key
        except (OSError, ValueError) as exc:
            self.tell(f"Could not refresh history: {exc}", True)
        self.last_refresh = time.monotonic()

    def main(self, screen):
        self.screen = screen
        screen.keypad(True)
        screen.timeout(100)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            for number, (name, color) in enumerate(
                [
                    ("accent", curses.COLOR_CYAN),
                    ("good", curses.COLOR_GREEN),
                    ("bad", curses.COLOR_RED),
                    ("warn", curses.COLOR_YELLOW),
                ],
                1,
            ):
                curses.init_pair(number, color, background)
                self.colors[name] = curses.color_pair(number)
        self.refresh()
        while self.running:
            if time.monotonic() - self.last_refresh >= self.refresh_seconds:
                self.refresh()
            self.draw()
            try:
                key = screen.get_wch()
            except curses.error:
                continue
            self.handle(key)

    def write(self, y: int, x: int, text: object, style: int = 0, width: int | None = None):
        height, columns = self.screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= columns - 1:
            return
        try:
            self.screen.addstr(
                y,
                x,
                clip(text, min(width if width is not None else columns - x - 1, columns - x - 1)),
                style,
            )
        except curses.error:
            pass

    def status_style(self, status):
        name = (
            "good"
            if status in {"completed", "passed"}
            else "bad"
            if status in {"failed", "interrupted"}
            else "accent"
            if status in {"running", "queued"}
            else "warn"
        )
        return self.colors.get(name, 0)

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        self.write(
            0, 1, "CHIPFLOW  /  local RTL -> GDS", curses.A_BOLD | self.colors.get("accent", 0)
        )
        if height < 18 or width < 64:
            self.write(2, 1, "Resize terminal to at least 64 columns x 18 rows.")
            self.write(4, 1, "q: quit (running jobs are unaffected)")
            self.screen.refresh()
            return
        active = sum(run["status"] not in TERMINAL for run in self.runs)
        self.write(
            1, 1, f"{active} running | {len(self.runs)} saved | {self.store.root}", curses.A_DIM
        )
        self.write(
            2,
            1,
            "1 Overview   2 Live logs   3 Files   4 Metrics    n New run    / Search",
            curses.A_BOLD,
        )
        self.write(3, 0, "-" * (width - 1), curses.A_DIM)
        split = width >= 100
        if split:
            left = min(40, max(29, width // 3))
            self.draw_runs(4, 1, height - 8, left - 2)
            for row in range(4, height - 3):
                self.write(row, left, "|", curses.A_DIM)
            self.draw_detail(4, left + 2, height - 8, width - left - 4)
        elif self.focus == "runs":
            self.draw_runs(4, 1, height - 8, width - 3)
        else:
            self.draw_detail(4, 1, height - 8, width - 3)
        self.write(
            height - 3,
            1,
            self.message,
            self.colors.get("bad" if self.message_error else "accent", 0),
        )
        self.write(
            height - 2,
            1,
            "Tab pane  j/k move  PgUp/PgDn scroll  f follow  c cancel  R rerun",
            curses.A_DIM,
        )
        self.write(
            height - 1,
            1,
            "q quit  ? help  r refresh | Focus: " + self.focus + " | " + self.view,
            curses.A_DIM,
        )
        if self.modal:
            self.draw_modal()
        self.screen.refresh()

    def draw_runs(self, y, x, height, width):
        self.write(
            y,
            x,
            "RUN HISTORY" + (" [focused]" if self.focus == "runs" else ""),
            curses.A_BOLD,
            width,
        )
        visible = self.filtered_runs
        self.write(
            y + 1,
            x,
            f"Search: {self.filters['runs']}"
            if self.filters["runs"]
            else "Up/Down select | Enter inspect",
            curses.A_DIM,
            width,
        )
        if not visible:
            self.write(
                y + 3,
                x,
                "No matching runs." if self.runs else "No runs yet. Press n to start.",
                width=width,
            )
            return
        index = next((i for i, run in enumerate(visible) if run["id"] == self.selected), 0)
        slots = max(1, (height - 2) // 3)
        first = max(0, min(index - slots // 2, len(visible) - slots))
        for row, run in enumerate(visible[first : first + slots]):
            top = y + 2 + row * 3
            style = curses.A_REVERSE if run["id"] == self.selected else 0
            self.write(
                top,
                x,
                ("> " if run["id"] == self.selected else "  ") + run["name"],
                style | curses.A_BOLD,
                width,
            )
            self.write(
                top + 1,
                x,
                f"  {run['id'][:8]} {run['status']} | {LABELS[run['target']]}",
                style | self.status_style(run["status"]),
                width,
            )

    def overview_lines(self):
        run = self.run
        if not run:
            return []
        lines = []
        if run["status"] == "completed":
            lines += [
                "SIMULATION PASSED"
                if run["target"] in {"sim", "gate_sim"}
                else "REQUESTED FLOW COMPLETED",
                "",
            ]
        elif run["status"] not in TERMINAL:
            lines += [
                run.get("queue_reason", "LIVE EXECUTION - updates automatically"),
                "Quit safely; this job continues independently.",
                "",
            ]
        else:
            lines += [
                run["status"].upper(),
                run.get("error", "Saved inputs and output files remain available."),
                "",
            ]
        if run["target"] not in {"sim", "gate_sim"}:
            lines += ["DESIGN CHECKS (separate from completion)"]
            for key, label in [
                ("timing", "Timing"),
                ("routing_drc", "Router DRC"),
                ("physical", "Physical signoff"),
            ]:
                status = run["checks"].get(key, "unknown")
                lines.append(f"  {label:18} {status if status != 'unknown' else 'not verified'}")
            lines += ["  Timing covers constrained paths; full DRC/LVS is not certified.", ""]
        for key, label in [
            ("finish__timing__setup__ws", "Setup slack (ns)"),
            ("finish__timing__hold__ws", "Hold slack (ns)"),
        ]:
            if key in run.get("metrics", {}):
                lines.append(f"{label}: {run['metrics'][key]}")
        lines += ["OUTPUTS"]
        outputs = [
            f
            for f in self.files
            if f["path"].endswith((".gds", ".vcd", "1_2_yosys.v", "6_final.spef"))
        ]
        lines.extend(f"  {file['path']} ({file['bytes']:,} bytes)" for file in outputs)
        if not outputs:
            lines.append("  No primary output files yet. Press 2 for logs or 3 for all files.")
        lines += [
            "",
            "RUN DIRECTORY",
            str(self.store.directory(run["id"])),
            "",
            "n: choose a project and stage | R: repeat saved inputs",
        ]
        return lines

    def content_lines(self):
        run = self.run
        if not run:
            return ["Select a run or press n to launch a project."]
        if self.view == "logs":
            lines = (
                self.paused_log_lines
                if not self.follow and self.paused_log_lines is not None
                else self.log.matching()
            )
            query = self.filters["logs"].lower()
            return [line for line in lines if query in line.lower()] or [
                "No matching log lines." if query else "Preparing tools; waiting for log output..."
            ]
        if self.view == "files":
            query = self.filters["files"].lower()
            return [
                f"{f['path']}  ({f['bytes']:,} bytes)"
                for f in self.files
                if query in f["path"].lower()
            ] or ["No matching files."]
        if self.view == "metrics":
            query = self.filters["metrics"].lower()
            return [
                f"{key}: {value}"
                for key, value in run.get("metrics", {}).items()
                if query in key.lower()
            ] or ["Final metrics appear after the finishing stage."]
        return [
            line
            for line in self.overview_lines()
            if self.filters["overview"].lower() in line.lower()
        ]

    def draw_detail(self, y, x, height, width):
        run = self.run
        if not run:
            self.write(y, x, "Select a run or press n to get started.", width=width)
            return
        self.write(
            y,
            x,
            f"{run['name']}  [{run['status']}]",
            curses.A_BOLD | self.status_style(run["status"]),
            width,
        )
        self.write(y + 1, x, f"{run['id']} | {elapsed(run)}", curses.A_DIM, width)
        current = next((s["name"] for s in run["stages"] if s["status"] == "running"), None)
        self.write(
            y + 2,
            x,
            "Target: " + LABELS[run["target"]] + (" | Now: " + LABELS[current] if current else ""),
            width=width,
        )
        symbols = {
            "completed": "+",
            "running": ">",
            "failed": "!",
            "cancelled": "x",
            "interrupted": "!",
        }
        short = {
            "synth": "SYN",
            "floorplan": "FP",
            "place": "PL",
            "cts": "CTS",
            "route": "RT",
            "finish": "GDS",
            "sim": "SIM",
            "gate_sim": "GLS",
        }
        pipeline = []
        for stage in stage_names(run["target"]):
            state = next((s["status"] for s in run["stages"] if s["name"] == stage), "pending")
            pipeline.append(f"{short[stage]}[{symbols.get(state, '.')}]")
        self.write(y + 3, x, " -> ".join(pipeline), self.colors.get("accent", 0), width)
        title = self.view.upper() + (" [focused]" if self.focus == "details" else "")
        if self.filters[self.view]:
            title += " | /" + self.filters[self.view]
        if self.view == "logs":
            title += (
                " | "
                + ("FOLLOW" if self.follow else "PAUSED")
                + (" | tail; full log on disk" if self.log.trimmed else "")
            )
        self.write(y + 4, x, title, curses.A_BOLD, width)
        lines = self.content_lines()
        if self.view != "logs":
            lines = [
                part
                for line in lines
                for part in (
                    textwrap.wrap(plain(line), max(1, width), replace_whitespace=False) or [""]
                )
            ]
        count = max(1, height - 5)
        if self.view == "logs":
            if self.follow:
                self.scroll = 0
            self.scroll = max(0, min(self.scroll, max(0, len(lines) - count)))
            start = max(0, len(lines) - count - self.scroll)
        else:
            self.scroll = max(0, min(self.scroll, max(0, len(lines) - count)))
            start = self.scroll
        for row, line in enumerate(lines[start : start + count]):
            style = self.colors.get("bad", 0) if re.search(r"\b(ERROR|FAIL|FATAL)\b", line) else 0
            self.write(y + 5 + row, x, line, style, width)

    def set_view(self, view):
        self.view = view
        self.focus = "details"
        self.scroll = 0
        self.follow = True
        self.paused_log_lines = None
        self.refresh()

    def pause_logs(self):
        if self.view == "logs" and self.paused_log_lines is None:
            self.paused_log_lines = self.log.matching()
        self.follow = False

    def move_run(self, delta):
        runs = self.filtered_runs
        if not runs:
            return
        index = next((i for i, run in enumerate(runs) if run["id"] == self.selected), 0)
        self.select(runs[max(0, min(index + delta, len(runs) - 1))]["id"])
        self.refresh()

    def open_new(self):
        self.project_paths = [
            project["path"] for project in list_projects(self.store.root.parent / "projects")
        ]
        if self.project_paths:
            self.project_index = next(
                (i for i, path in enumerate(self.project_paths) if "DMA_Controller" in path), 0
            )
            self.input = self.project_paths[self.project_index]
        else:
            self.project_index = -1
            self.input = "examples/counter/chipflow.yaml"
        self.cursor = len(self.input)
        self.target = "sim"
        self.new_field = "path"
        self.modal = "new"

    def start_run(self):
        try:
            path = Path(self.input.strip()).expanduser()
            preflight = inspect_project(path, self.target)
            if not preflight["ready"]:
                self.tell("Fix before starting: " + " | ".join(preflight["issues"]), True)
                self.modal_errors = preflight["issues"]
                return
            run = self.store.start(path, self.target)
            self.modal = None
            self.select(run["id"])
            self.set_view("logs")
            self.tell(f"Started {run['name']}. This run continues when the dashboard closes.")
        except (ValueError, OSError) as exc:
            self.modal_errors = str(exc).splitlines()
            self.tell(exc, True)

    def retry(self):
        if not self.run:
            self.tell("Select a run first.", True)
            return
        if self.run["status"] not in TERMINAL:
            self.tell("This run is active. Use n to start a separate project run.", True)
            return
        try:
            next_run = self.store.retry(self.selected)
            self.select(next_run["id"])
            self.set_view("logs")
            self.tell(f"Started {next_run['name']} from preserved inputs.")
        except (ValueError, OSError) as exc:
            self.tell(exc, True)

    def handle(self, key):
        if self.modal:
            self.handle_modal(key)
            return
        if key in ("q", "Q", "\x03"):
            self.running = False
        elif key == "?":
            self.modal = "help"
        elif key in ("n", "p"):
            self.modal_errors = []
            self.open_new()
        elif key in ("1", "2", "3", "4"):
            self.set_view(VIEWS[int(key) - 1])
        elif key == "\t":
            self.focus = "details" if self.focus == "runs" else "runs"
        elif key in ("j", "k", curses.KEY_UP, curses.KEY_DOWN):
            delta = 1 if key in ("j", curses.KEY_DOWN) else -1
            if self.focus == "runs":
                self.move_run(delta)
            else:
                self.pause_logs()
                self.scroll = max(0, self.scroll + (-delta if self.view == "logs" else delta))
        elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
            self.focus = "details"
            self.pause_logs()
            delta = max(1, self.screen.getmaxyx()[0] - 13)
            if key == curses.KEY_PPAGE:
                delta = -delta
            self.scroll = max(0, self.scroll + (-delta if self.view == "logs" else delta))
        elif key in (curses.KEY_END, "G"):
            self.scroll = 0 if self.view == "logs" else len(self.content_lines())
            self.follow = self.view == "logs"
            self.paused_log_lines = None
        elif key in (curses.KEY_HOME, "g"):
            self.pause_logs()
            self.scroll = len(self.content_lines()) if self.view == "logs" else 0
        elif key == "f":
            if self.follow:
                self.pause_logs()
            else:
                self.follow = True
                self.paused_log_lines = None
                self.scroll = 0
            self.tell(
                "Log following " + ("enabled." if self.follow else "paused. Scroll with PgUp/PgDn.")
            )
        elif key == "/":
            self.search_context = "runs" if self.focus == "runs" else self.view
            self.input = self.filters[self.search_context]
            self.cursor = len(self.input)
            self.modal = "search"
        elif key in ("\x1b", "x"):
            context = "runs" if self.focus == "runs" else self.view
            self.filters[context] = ""
            self.scroll = 0
        elif key == "c":
            if self.run and self.run["status"] not in TERMINAL:
                self.confirm_run = self.selected
                self.modal = "cancel"
            else:
                self.tell("Select an active run to cancel.")
        elif key == "R":
            self.retry()
        elif key == "r":
            self.refresh()
            self.tell("History and logs refreshed.")
        elif key in ("\n", "\r", curses.KEY_ENTER):
            self.focus = "details"
            self.tell(
                "Run directory: " + str(self.store.directory(self.selected))
                if self.run
                else "Press n to launch a project."
            )

    def edit_input(self, key):
        if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if self.cursor:
                self.input = self.input[: self.cursor - 1] + self.input[self.cursor :]
                self.cursor -= 1
        elif key == curses.KEY_DC:
            self.input = self.input[: self.cursor] + self.input[self.cursor + 1 :]
        elif key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(self.input), self.cursor + 1)
        elif key in (curses.KEY_HOME, "\x01"):
            self.cursor = 0
        elif key in (curses.KEY_END, "\x05"):
            self.cursor = len(self.input)
        elif key == "\x15":
            self.input = ""
            self.cursor = 0
        elif isinstance(key, str) and key.isprintable() and len(self.input) < 8192:
            self.input = self.input[: self.cursor] + key + self.input[self.cursor :]
            self.cursor += len(key)

    def handle_modal(self, key):
        if key in ("\x1b", "\x03"):
            self.modal = None
            return
        if self.modal == "help":
            if key in ("q", "?", "\n", "\r"):
                self.modal = None
            return
        if self.modal == "cancel":
            if key in ("y", "Y"):
                try:
                    result = self.store.cancel(self.confirm_run)
                    self.tell(
                        "Cancellation requested; saved files remain available."
                        if result["cancellation_requested"]
                        else "Run already finished."
                    )
                except (ValueError, OSError) as exc:
                    self.tell(exc, True)
                self.modal = None
            elif key in ("n", "N", "q"):
                self.modal = None
            return
        if self.modal == "search":
            if key in ("\n", "\r", curses.KEY_ENTER):
                self.filters[self.search_context] = self.input
                self.scroll = 0
                self.modal = None
                if (
                    self.search_context == "runs"
                    and self.filtered_runs
                    and not any(r["id"] == self.selected for r in self.filtered_runs)
                ):
                    self.select(self.filtered_runs[0]["id"])
                    self.refresh()
            else:
                self.edit_input(key)
            return
        if self.modal == "new":
            if key in ("\n", "\r", curses.KEY_ENTER):
                self.start_run()
            elif key == "\t":
                self.new_field = "target" if self.new_field == "path" else "path"
            elif key in (curses.KEY_UP, curses.KEY_DOWN) and self.project_paths:
                delta = 1 if key == curses.KEY_DOWN else -1
                self.project_index = (self.project_index + delta) % len(self.project_paths)
                self.input = self.project_paths[self.project_index]
                self.cursor = len(self.input)
                self.modal_errors = []
            elif self.new_field == "target":
                if key in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l"):
                    delta = 1 if key in (curses.KEY_RIGHT, "l") else -1
                    self.target = STAGES[(STAGES.index(self.target) + delta) % len(STAGES)]
                    self.modal_errors = []
            else:
                self.edit_input(key)
                self.modal_errors = []

    def draw_modal(self):
        height, columns = self.screen.getmaxyx()
        width = min(88, columns - 4)
        rows = min(20, height - 4)
        top, left = (height - rows) // 2, (columns - width) // 2
        for row in range(top, top + rows):
            self.write(row, left, " " * width, curses.A_REVERSE, width)
        self.write(top, left, "+" + "-" * (width - 2) + "+", curses.A_REVERSE, width)
        self.write(top + rows - 1, left, "+" + "-" * (width - 2) + "+", curses.A_REVERSE, width)
        title = {
            "new": "START A RUN",
            "search": "SEARCH " + getattr(self, "search_context", "").upper(),
            "cancel": "CANCEL RUN?",
            "help": "KEYBOARD GUIDE",
        }[self.modal]
        self.write(top + 1, left + 2, title, curses.A_REVERSE | curses.A_BOLD, width - 4)
        content = []
        if self.modal == "help":
            content = [
                "Tab              Switch run history / details",
                "Up/Down or j/k   Select a run or scroll focused details",
                "1 / 2 / 3 / 4    Overview / live logs / files / metrics",
                "PgUp / PgDn      Scroll details; pauses log following",
                "Home / End       Oldest / newest log output",
                "f                Toggle following live log output",
                "/                Search focused history or detail view",
                "Esc or x         Clear current search",
                "n                Launch a saved project or local YAML file",
                "R                Rerun selected completed job's saved inputs",
                "c                Cancel selected active run (y to confirm)",
                "r                Refresh now",
                "q / Ctrl+C       Quit dashboard; workers keep running",
                "",
                "Close with Escape or Enter. No browser server is needed.",
            ]
        elif self.modal == "cancel":
            run = next((r for r in self.runs if r["id"] == self.confirm_run), {})
            content = [
                f"Cancel {run.get('name', '')}?",
                self.confirm_run or "",
                "",
                "The active subprocess group will be stopped.",
                "Saved inputs, logs and generated outputs remain available.",
                "",
                "y: cancel run     n / Escape: keep running",
            ]
        elif self.modal == "search":
            self.draw_input(top + 3, left + 2, width - 4)
            content = ["", "", "Enter: apply    Esc: dismiss    Ctrl+U: clear"]
        elif self.modal == "new":
            content = ["Project path (relative paths start in the current folder):"]
            self.draw_input(top + 4, left + 2, width - 4, self.new_field == "path")
            content += [
                "",
                "",
                "Target: < "
                + LABELS[self.target]
                + " >"
                + ("  [selected]" if self.new_field == "target" else ""),
                "",
                "Up/Down: choose saved project   Tab: switch field",
                "Left/Right: choose stage when Target is selected",
                "Enter: validate + start   Esc: close   Ctrl+U: clear path",
            ]
            errors = getattr(self, "modal_errors", [])
            if errors:
                content += ["", "FIX BEFORE STARTING:", *errors]
            else:
                content += [
                    "",
                    "Inputs and required native tools are checked before launch.",
                    "Physical stages run their prerequisites. Simulate separately.",
                ]
        row = top + 3
        for line in content:
            for part in textwrap.wrap(plain(line), width - 4) or [""]:
                if row >= top + rows - 1:
                    break
                # Input boxes own these rows.
                if not (
                    self.modal == "search"
                    and row == top + 3
                    or self.modal == "new"
                    and row == top + 4
                ):
                    self.write(row, left + 2, part, curses.A_REVERSE, width - 4)
                row += 1
        if self.modal in {"search", "new"}:
            # Paint input last so wrapped labels cannot cover it.
            self.draw_input(
                top + (4 if self.modal == "new" else 3),
                left + 2,
                width - 4,
                self.modal == "search" or self.new_field == "path",
            )

    def draw_input(self, y, x, width, active=True):
        start = max(0, self.cursor - width + 5)
        text = self.input[start : start + width - 3]
        self.write(
            y,
            x,
            "> " + text + " " * width,
            curses.A_BOLD | (curses.A_UNDERLINE if active else curses.A_REVERSE),
            width,
        )
        if active:
            position = len(clip(self.input[start : self.cursor], width - 3))
            self.write(
                y,
                x + 2 + position,
                self.input[self.cursor : self.cursor + 1] or " ",
                curses.A_REVERSE | curses.A_BOLD,
                1,
            )


def launch(root: Path | str, run_id: str | None = None, view="overview", refresh=1.0) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "chipflow dashboard needs an interactive terminal. Use chipflow history, status, or logs for redirected output.",
            file=sys.stderr,
        )
        return 2
    if not 0.2 <= refresh <= 60:
        raise ValueError("Refresh interval must be between 0.2 and 60 seconds.")
    store = Store(root)
    selected = resolve_run(store, run_id)
    dashboard = Dashboard(store, selected, view, refresh)
    try:
        curses.wrapper(dashboard.main)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print(
            f"chipflow: terminal setup failed ({exc}). Check TERM and use a standard terminal emulator.",
            file=sys.stderr,
        )
        return 2
    return 0
