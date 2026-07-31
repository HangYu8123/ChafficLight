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


def _report(paths: list, *, removed: bool, nothing: str) -> None:
    """Say which files a launcher command touched, or that it touched none."""
    verb = "removed" if removed else "created"
    for path in paths:
        print(f"{verb} {path}")
    if not paths:
        print(nothing)


def main(argv: list[str] | None = None) -> int:
    """Run the monitor.

    With ``--once --json`` this writes a single JSON snapshot to stdout and
    returns without ever constructing a Tk root; otherwise it opens the window.
    """
    parser = argparse.ArgumentParser(
        prog="chafficlight",
        description="Traffic light for Claude Code and Codex CLI chat sessions.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="print one snapshot and exit instead of opening the window",
    )
    parser.add_argument("--json", action="store_true", help="with --once, print JSON")
    launcher = parser.add_mutually_exclusive_group()
    launcher.add_argument(
        "--install-desktop",
        action="store_true",
        help="create a click-to-run launcher for this app and exit",
    )
    launcher.add_argument(
        "--uninstall-desktop",
        action="store_true",
        help="remove the launcher created by --install-desktop and exit",
    )
    # A second group, because starting at login is not an alternative to having a
    # launcher: --install-desktop --enable-autostart is the ordinary first-run pair.
    autostart = parser.add_mutually_exclusive_group()
    autostart.add_argument(
        "--enable-autostart",
        action="store_true",
        help="start this app automatically when you log in, and exit",
    )
    autostart.add_argument(
        "--disable-autostart",
        action="store_true",
        help="stop starting this app when you log in, and exit",
    )
    args = parser.parse_args(argv)

    launchers = args.install_desktop or args.uninstall_desktop
    logins = args.enable_autostart or args.disable_autostart
    if launchers or logins:
        # Imported here so the monitoring paths never load the installer.
        from .desktop import install, uninstall, unavailable_reason

        if launchers:
            reason = unavailable_reason()
            if reason:
                print(reason)
            else:
                removing = args.uninstall_desktop
                _report(
                    uninstall() if removing else install(),
                    removed=removing,
                    nothing="no launcher was installed",
                )
        if logins:
            removing = args.disable_autostart
            _report(
                uninstall(autostart=True) if removing else install(autostart=True),
                removed=removing,
                nothing="no autostart entry was installed",
            )
        return 0

    monitor = Monitor()
    if args.once:
        _print_once(monitor, args.json)
        return 0

    # Imported here so the headless path never loads the GUI module.
    from .gui import TrafficLightApp, enable_hidpi

    # Before the first window exists: Windows fixes a window's DPI awareness when
    # it is created, and an unaware one is drawn at 96 dpi and then stretched.
    enable_hidpi()
    root = tk.Tk()
    app = TrafficLightApp(root, monitor)
    app.refresh()
    # The window is undecorated, so no window manager will ever send this; the
    # app's own close button is the real path out. Kept because it costs nothing
    # and is the correct wiring the moment the window has a title bar again.
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
