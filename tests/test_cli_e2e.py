"""Frozen end-to-end acceptance tests for the headless ``--once --json`` path.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with token usage.
"""

import json
import os
import time
import tkinter
from datetime import datetime, timezone
from pathlib import Path

from cli_traffic_light import cli
from cli_traffic_light.claude import _default_proc_info


def _real_proc_start() -> str:
    """The value Claude records as ``procStart`` for the running process.

    Taken from the production lookup so the fixture cannot drift from the
    platform-specific identity the reader compares it against.
    """
    return str(_default_proc_info(os.getpid())[1])


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _build_claude_home(tmp_path: Path) -> Path:
    now = time.time()
    home = tmp_path / "claude_home"
    session_path = home / "sessions" / f"{os.getpid()}.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "sessionId": "cli-claude",
                "cwd": "/work/cli-claude",
                "startedAt": _iso(now - 300),
                "procStart": _real_proc_start(),
                "version": "2.1.220",
                "kind": "cli",
                "entrypoint": "cli",
                "name": "CLI fixture session",
                "status": "busy",
                "updatedAt": _iso(now - 5),
                "statusUpdatedAt": _iso(now - 5),
                "bridgeSessionId": None,
            }
        ),
        encoding="utf-8",
    )
    transcript = home / "projects" / "-work-cli-claude" / "cli-claude.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": False,
                "requestId": "req-cli",
                "timestamp": _iso(now - 30),
                "message": {
                    "id": "msg-cli",
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
    return home


def _build_codex_home(tmp_path: Path) -> Path:
    now = time.time()
    home = tmp_path / "codex_home"
    rollout = home / "sessions" / "2026" / "07" / "28" / "rollout-2026-07-28T11-00-00-cli.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "timestamp": _iso(now - 200),
            "type": "session_meta",
            "payload": {
                "session_id": "cli-codex",
                "cwd": "/work/cli-codex",
                "cli_version": "0.146.0-alpha.3.1",
                "thread_source": "cli",
                "source": "cli",
                "model_provider": "openai",
                "context_window": 272_000,
            },
        },
        {"timestamp": _iso(now - 150), "type": "event_msg", "payload": {"type": "task_started"}},
        {
            "timestamp": _iso(now - 60),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1_000,
                        "cached_input_tokens": 400,
                        "output_tokens": 50,
                        "cache_write_input_tokens": 0,
                        "total_tokens": 1_050,
                    },
                    "last_token_usage": {},
                },
            },
        },
        {"timestamp": _iso(now - 30), "type": "event_msg", "payload": {"type": "task_complete"}},
    ]
    rollout.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    (home / "session_index.jsonl").write_text(
        json.dumps(
            {"id": "cli-codex", "thread_name": "CLI codex thread", "updated_at": _iso(now - 30)}
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(rollout, (now - 30, now - 30))
    return home


def _run_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_build_claude_home(tmp_path)))
    monkeypatch.setenv("CODEX_HOME", str(_build_codex_home(tmp_path)))
    exit_code = cli.main(["--once", "--json"])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)


def test_once_json_reports_both_fixture_sessions(tmp_path, monkeypatch, capsys):
    exit_code, payload = _run_once(tmp_path, monkeypatch, capsys)
    assert exit_code == 0
    sessions = payload["sessions"]
    assert len(sessions) == 2
    assert sorted(s["session_id"] for s in sessions) == ["cli-claude", "cli-codex"]
    for session in sessions:
        assert {"state", "total_tokens", "tokens_per_sec", "is_vscode"} <= set(session)


def test_once_json_reports_state_and_tokens_per_session(tmp_path, monkeypatch, capsys):
    _, payload = _run_once(tmp_path, monkeypatch, capsys)
    by_id = {s["session_id"]: s for s in payload["sessions"]}
    assert by_id["cli-claude"]["state"] == "running"
    assert by_id["cli-codex"]["state"] == "idle"
    assert by_id["cli-claude"]["total_tokens"] == 125
    assert by_id["cli-codex"]["total_tokens"] == 650
    assert isinstance(by_id["cli-claude"]["is_vscode"], bool)
    assert isinstance(by_id["cli-codex"]["tokens_per_sec"], float)


def test_once_json_reports_agent_cwd_and_totals(tmp_path, monkeypatch, capsys):
    _, payload = _run_once(tmp_path, monkeypatch, capsys)
    by_id = {s["session_id"]: s for s in payload["sessions"]}
    assert by_id["cli-claude"]["agent"] == "claude"
    assert by_id["cli-codex"]["agent"] == "codex"
    assert by_id["cli-claude"]["cwd"] == "/work/cli-claude"
    assert payload["totals"]["total_tokens"] == 775


def test_once_json_never_constructs_a_tk_root(tmp_path, monkeypatch, capsys):
    assert tkinter._default_root is None
    exit_code, payload = _run_once(tmp_path, monkeypatch, capsys)
    assert exit_code == 0
    assert len(payload["sessions"]) == 2
    assert tkinter._default_root is None
