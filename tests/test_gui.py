"""Frozen acceptance tests for the Tk traffic-light window itself.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows a green light while a session is running, a yellow
light when it needs their input and a red light once it has finished.
"""

import json
import os
import time
import tkinter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli_traffic_light.gui import TrafficLightApp
from cli_traffic_light.monitor import Monitor


def _real_proc_start() -> str:
    """Field 22 of ``/proc/self/stat`` — the value Claude stores as ``procStart``."""
    with open("/proc/self/stat") as handle:
        return handle.read().rsplit(")", 1)[1].split()[19]


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _session_path(home: Path) -> Path:
    return home / "sessions" / f"{os.getpid()}.json"


def _write_session_file(home: Path, status: str) -> None:
    now = time.time()
    path = _session_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "sessionId": "gui-sess",
                "cwd": "/work/gui",
                "startedAt": _iso(now - 300),
                "procStart": _real_proc_start(),
                "version": "2.1.220",
                "kind": "cli",
                "entrypoint": "cli",
                "name": "GUI fixture session",
                "status": status,
                "updatedAt": _iso(now - 5),
                "statusUpdatedAt": _iso(now - 5),
                "bridgeSessionId": None,
            }
        ),
        encoding="utf-8",
    )


def _build_homes(tmp_path: Path, monkeypatch) -> Path:
    claude_home = tmp_path / "claude_home"
    codex_home = tmp_path / "codex_home"
    (codex_home / "sessions").mkdir(parents=True)
    _write_session_file(claude_home, "busy")

    now = time.time()
    transcript = claude_home / "projects" / "-work-gui" / "gui-sess.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": False,
                "requestId": "req-gui",
                "timestamp": _iso(now - 30),
                "message": {
                    "id": "msg-gui",
                    "role": "assistant",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 5,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    return claude_home


@pytest.fixture
def tk_root():
    root = tkinter.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_light_colour_follows_the_session_state(tmp_path, monkeypatch, tk_root):
    claude_home = _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor())
    try:
        app.refresh()
        tk_root.update_idletasks()
        rows = app.rows()
        assert len(rows) == 1
        assert rows[0]["session_id"] == "gui-sess"
        assert rows[0]["light"].cget("background") == "#2ecc40"

        _write_session_file(claude_home, "idle")
        app.refresh()
        tk_root.update_idletasks()
        rows = app.rows()
        assert len(rows) == 1
        assert rows[0]["light"].cget("background") == "#ffdc00"
    finally:
        app.stop()


def test_rows_expose_labelled_session_details(tmp_path, monkeypatch, tk_root):
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        row = app.rows()[0]
        assert row["session_id"] == "gui-sess"
        assert {"title", "state", "tokens", "rate"} <= set(row["labels"])
        assert row["labels"]["state"].cget("text") == "running"
        assert row["labels"]["title"].cget("text") == "GUI fixture session"
    finally:
        app.stop()


def test_stop_cancels_every_pending_after_callback(tmp_path, monkeypatch, tk_root):
    _build_homes(tmp_path, monkeypatch)
    before = set(tk_root.tk.splitlist(tk_root.tk.call("after", "info")))
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        during = set(tk_root.tk.splitlist(tk_root.tk.call("after", "info")))
        assert during - before != set()
    finally:
        app.stop()
    remaining = set(tk_root.tk.splitlist(tk_root.tk.call("after", "info")))
    assert remaining - before == set()
