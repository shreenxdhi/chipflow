import os
import subprocess
import sys
import time

import pytest
import yaml

from chipflow.engine import Store, save
from chipflow.tui import Dashboard, LogTail, clip, plain, resolve_run


@pytest.fixture
def terminal_store(tmp_path):
    store = Store(tmp_path / "runs")
    for index, status in enumerate(["completed", "failed"]):
        run_id = str(index + 1) * 32
        directory = store.root / run_id
        directory.mkdir()
        save(
            directory / "run.json",
            dict(
                id=run_id,
                name=f"design_{index}",
                target="sim",
                status=status,
                created=time.time() - 10 - index,
                finished=time.time() - 2,
                stages=[
                    dict(
                        name="sim", status=status, started=time.time() - 9, finished=time.time() - 2
                    )
                ],
                checks={"timing": "unknown", "physical": "unknown"},
            ),
        )
        (directory / "console.log").write_text(f"PASS fixture {index}\nsecond line\n")
        (directory / "waveform.vcd").write_text("$enddefinitions $end\n")
    return store


def test_log_tail_updates_filters_switches_and_truncates(tmp_path):
    path = tmp_path / "console.log"
    path.write_text("first\npar")
    tail = LogTail(max_lines=3)
    tail.read(path)
    assert tail.matching() == ["first", "par"]
    with path.open("a") as stream:
        stream.write("tial\nPASS: check\nlast\n")
    tail.read(path)
    assert tail.matching() == ["partial", "PASS: check", "last"]
    assert tail.matching("pass") == ["PASS: check"]
    assert tail.trimmed
    path.write_text("reset\n")
    tail.read(path)
    assert tail.matching() == ["reset"]
    other = tmp_path / "other.log"
    other.write_text("different\n")
    tail.read(other)
    assert tail.matching() == ["different"]


def test_log_control_sequences_and_unicode_are_safe(tmp_path):
    assert plain("\x1b[31mERROR\x1b[0m\x07") == "ERROR"
    assert clip("abc界def", 5) == "abc界"
    assert clip("abc界def", 4) == "abc"
    path = tmp_path / "log"
    path.write_bytes(b"hello \xe2\x82")
    tail = LogTail()
    tail.read(path)
    with path.open("ab") as stream:
        stream.write(b"\xac\n")
    tail.read(path)
    assert tail.matching() == ["hello €"]


def test_large_log_starts_at_tail_and_remains_bounded(tmp_path):
    path = tmp_path / "log"
    path.write_text("old output\n" * 30000 + "LATEST\n")
    tail = LogTail(max_lines=20)
    tail.read(path)
    assert tail.trimmed
    assert tail.matching()[-1] == "LATEST"
    assert len(tail.lines) <= 20


def test_run_selection_views_and_search(terminal_store):
    dashboard = Dashboard(terminal_store)
    dashboard.refresh()
    assert dashboard.selected == "1" * 32
    dashboard.move_run(1)
    assert dashboard.selected == "2" * 32
    dashboard.handle("2")
    assert dashboard.view == "logs" and dashboard.focus == "details"
    assert dashboard.content_lines()[0] == "PASS fixture 1"
    dashboard.handle("/")
    for key in "PASS":
        dashboard.handle_modal(key)
    dashboard.handle_modal("\n")
    assert dashboard.content_lines() == ["PASS fixture 1"]
    dashboard.handle("x")
    assert dashboard.filters["logs"] == ""
    dashboard.handle("3")
    assert any("waveform.vcd" in line for line in dashboard.content_lines())
    assert resolve_run(terminal_store, "111") == "1" * 32
    with pytest.raises(ValueError):
        resolve_run(terminal_store, "abc")


def test_input_editing_and_launch_preflight(terminal_store):
    dashboard = Dashboard(terminal_store)
    dashboard.refresh()
    dashboard.handle("n")
    dashboard.handle_modal("\x15")
    for key in "/missing/project.yaml":
        dashboard.handle_modal(key)
    dashboard.handle_modal("\n")
    assert dashboard.modal == "new"
    assert "Project file not found" in dashboard.message
    assert len(terminal_store.history()) == 2
    dashboard.handle_modal("\x1b")
    assert dashboard.modal is None
    dashboard.input = "abc"
    dashboard.cursor = 3
    dashboard.edit_input("\x7f")
    assert dashboard.input == "ab"


def test_quit_keeps_history_and_does_not_cancel(terminal_store):
    dashboard = Dashboard(terminal_store)
    dashboard.refresh()
    dashboard.handle("q")
    assert not dashboard.running
    assert not list(terminal_store.root.glob("*/cancel"))
    assert len(terminal_store.history()) == 2


def test_cancel_requires_explicit_key_and_keeps_selected_id(terminal_store):
    dashboard = Dashboard(terminal_store)
    dashboard.refresh()
    dashboard.runs[0]["status"] = "running"
    dashboard.handle("c")
    assert dashboard.modal == "cancel"
    dashboard.select("2" * 32)
    dashboard.handle_modal("n")
    assert not list(terminal_store.root.glob("*/cancel"))
    dashboard.select("1" * 32)
    dashboard.handle("c")
    # The confirmation is bound to the original run, even if selection changes.
    original = terminal_store.get("1" * 32)
    original.update(status="running", created=time.time())
    save(terminal_store.directory("1" * 32) / "run.json", original)
    dashboard.select("2" * 32)
    dashboard.handle_modal("y")
    assert (terminal_store.directory("1" * 32) / "cancel").exists()
    assert not (terminal_store.directory("2" * 32) / "cancel").exists()


def test_noninteractive_dashboard_explains_alternatives(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "chipflow", "--runs", str(tmp_path / "runs"), "dashboard"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "interactive terminal" in result.stderr and "chipflow history" in result.stderr


class Terminal:
    """Real PTY, without an extra terminal-testing dependency."""

    def __init__(self, argv, rows=30, columns=120, environment=None):
        import fcntl
        import pty
        import struct
        import termios

        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.process = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**os.environ, "TERM": "xterm-256color", **(environment or {})},
            start_new_session=True,
        )
        os.close(slave)
        self.output = b""

    def expect(self, text, timeout=8):
        import select

        deadline = time.monotonic() + timeout
        wanted = text.encode()
        while wanted not in self.output and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    self.output += os.read(self.master, 65536)
                except OSError:
                    break
        assert wanted in self.output, self.output[-5000:].decode(errors="replace")

    def send(self, value):
        os.write(self.master, value.encode())

    def close(self):
        try:
            if self.process.poll() is None:
                self.send("\x1bq")
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=3)
        finally:
            os.close(self.master)


def test_real_terminal_navigation_search_resize_and_safe_quit(terminal_store):
    import fcntl
    import signal
    import struct
    import termios

    terminal = Terminal(
        [
            sys.executable,
            "-m",
            "chipflow",
            "--runs",
            str(terminal_store.root),
            "dashboard",
            "--run",
            "111",
            "--view",
            "logs",
        ]
    )
    try:
        terminal.expect("CHIPFLOW")
        terminal.expect("PASS fixture 0")
        terminal.send("3")
        terminal.expect("waveform.vcd")
        terminal.send("?")
        terminal.expect("KEYBOARD GUIDE")
        terminal.send("\x1b")
        terminal.send("2/PASS\n")
        terminal.expect("/PASS")
        fcntl.ioctl(terminal.master, termios.TIOCSWINSZ, struct.pack("HHHH", 18, 64, 0, 0))
        os.kill(terminal.process.pid, signal.SIGWINCH)
        terminal.send("r")
        terminal.expect("History and logs refreshed.")
        terminal.send("q")
        assert terminal.process.wait(timeout=5) == 0
        assert b"Traceback" not in terminal.output
        assert not list(terminal_store.root.glob("*/cancel"))
    finally:
        terminal.close()


def test_real_terminal_starts_simulation_and_worker_survives_exit(
    terminal_store, tmp_path, monkeypatch
):
    # Slow the real compiler probe/compile so the job is still active at UI exit.
    import shutil

    compiler = shutil.which("iverilog")
    assert compiler
    wrappers = tmp_path / "bin"
    wrappers.mkdir()
    wrapper = wrappers / "iverilog"
    import shlex

    wrapper.write_text("#!/bin/sh\nsleep 1\nexec " + shlex.quote(compiler) + ' "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(wrappers) + os.pathsep + os.environ["PATH"])
    path = tmp_path / "terminal-project.yaml"
    (tmp_path / "rtl.v").write_text("module dut; endmodule")
    (tmp_path / "tb.v").write_text(
        'module tb; initial begin $display("PASS terminal"); $finish; end endmodule'
    )
    path.write_text(
        yaml.safe_dump(
            dict(
                name="terminal_launch",
                top="dut",
                rtl=["rtl.v"],
                orfs="/missing/orfs",
                clocks=[dict(port="clk", period_ns=10)],
                simulation=dict(top="tb", testbench=["tb.v"], pass_marker="PASS terminal"),
            )
        )
    )
    terminal = Terminal(
        [sys.executable, "-m", "chipflow", "--runs", str(terminal_store.root), "dashboard"]
    )
    try:
        terminal.expect("CHIPFLOW")
        terminal.send("n")
        terminal.expect("START A RUN")
        terminal.send("\x15" + str(path) + "\n")
        # Curses may retain the leading S from the previous setup message.
        terminal.expect("terminal_launch. This run continues", timeout=15)
        active = next(r for r in terminal_store.history() if r["name"] == "terminal_launch")
        assert active["status"] in {"queued", "running"}
        terminal.send("q")
        assert terminal.process.wait(timeout=5) == 0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = next(
                (r for r in terminal_store.history() if r["name"] == "terminal_launch"), None
            )
            if run and run["status"] in {"completed", "failed", "interrupted"}:
                break
            time.sleep(0.1)
        assert run and run["status"] == "completed", run
        assert "PASS terminal" in (terminal_store.directory(run["id"]) / "console.log").read_text()
        assert not (terminal_store.directory(run["id"]) / "cancel").exists()
    finally:
        terminal.close()


def test_default_command_in_monochrome_terminal(terminal_store):
    terminal = Terminal(
        [sys.executable, "-m", "chipflow", "--runs", str(terminal_store.root)],
        rows=24,
        columns=80,
        environment={"TERM": "vt100"},
    )
    try:
        terminal.expect("Review installation choices before opening the dashboard?")
        terminal.send("\n")
        terminal.expect("RUN HISTORY")
        terminal.send("2")
        terminal.expect("PASS fixture 0")
        terminal.send("q")
        assert terminal.process.wait(timeout=5) == 0
    finally:
        terminal.close()


def test_paused_log_view_does_not_move_when_output_appends(terminal_store):
    dashboard = Dashboard(terminal_store, "1" * 32, view="logs")
    dashboard.refresh()
    original = dashboard.content_lines()
    dashboard.handle("f")
    with (terminal_store.directory("1" * 32) / "console.log").open("a") as stream:
        stream.write("new live output\n")
    dashboard.refresh()
    assert dashboard.content_lines() == original
    dashboard.handle("f")
    assert dashboard.content_lines()[-1] == "new live output"
