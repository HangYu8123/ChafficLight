"""Command line entry point: opens the window, or prints one headless snapshot.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with token usage.
"""

from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
from dataclasses import asdict

from .monitor import Monitor
from .state import Session
from .tokens import TokenUsage

__all__ = ["main"]


def _usage_payload(usage: TokenUsage) -> dict:
    """One token count in JSON form, with the billable total spelled out."""
    return {**asdict(usage), "total_tokens": usage.total_tokens}


def _session_payload(session: Session) -> dict:
    """One session in JSON form."""
    return {
        "session_id": session.session_id,
        "agent": session.agent,
        "title": session.title,
        "cwd": session.cwd,
        "state": session.state.value,
        "tokens_per_sec": round(session.tokens_per_sec, 3),
        "is_vscode": session.is_vscode,
        "vscode_confidence": session.vscode_confidence,
        "pid": session.pid,
        **_usage_payload(session.usage),
    }


def _print_once(monitor: Monitor, as_json: bool) -> None:
    """Write a single snapshot to stdout."""
    sessions = monitor.snapshot()
    if as_json:
        payload = {
            "sessions": [_session_payload(session) for session in sessions],
            "totals": _usage_payload(monitor.totals()),
            "subagent_totals": _usage_payload(monitor.subagent_totals()),
        }
        print(json.dumps(payload, indent=2))
        return
    for session in sessions:
        print(
            f"{session.state.value:<11} {session.agent:<7}"
            f" {session.usage.total_tokens:>9,} tok  {session.title}"
        )


def main(argv: list[str] | None = None) -> int:
    """Run the monitor.

    With ``--once --json`` this writes a single JSON snapshot to stdout and
    returns without ever constructing a Tk root; otherwise it opens the window.
    """
    parser = argparse.ArgumentParser(
        prog="cli-traffic-light",
        description="Traffic light for Claude Code and Codex CLI chat sessions.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="print one snapshot and exit instead of opening the window",
    )
    parser.add_argument("--json", action="store_true", help="with --once, print JSON")
    args = parser.parse_args(argv)

    monitor = Monitor()
    if args.once:
        _print_once(monitor, args.json)
        return 0

    # Imported here so the headless path never loads the GUI module.
    from .gui import TrafficLightApp

    root = tk.Tk()
    app = TrafficLightApp(root, monitor)
    app.refresh()

    def close() -> None:
        app.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
