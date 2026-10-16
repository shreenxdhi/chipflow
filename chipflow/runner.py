"""Internal tool guardian: stop process groups when their worker disappears."""

from __future__ import annotations

import argparse
import os
import select
import signal
import subprocess
import sys
import time


def terminate(process):
    # The leader may have exited while its children still hold files or licenses.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise(argv, parent_fd, timeout):
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    process = None
    try:
        if select.select([parent_fd], [], [], 0)[0] and not os.read(parent_fd, 1):
            return 125
        process = subprocess.Popen(argv, start_new_session=True)
        started = time.monotonic()
        while True:
            if stopping:
                return 130
            if select.select([parent_fd], [], [], 0.1)[0] and not os.read(parent_fd, 1):
                print("Chipflow: worker disconnected; terminating its tools.", flush=True)
                return 125
            if time.monotonic() - started >= timeout:
                print(f"Chipflow: command exceeded {timeout:g}s.", flush=True)
                return 124
            code = process.poll()
            if code is not None:
                return code if code >= 0 else 128 - code
    finally:
        if process is not None:
            terminate(process)
        os.close(parent_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-fd", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    try:
        return supervise(argv, args.parent_fd, args.timeout)
    except OSError as exc:
        print(f"Chipflow: could not execute tool: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
