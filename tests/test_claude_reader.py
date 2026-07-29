"""Frozen acceptance tests for the Claude Code CLI on-disk reader.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with token usage, reading the state the CLIs
themselves keep on disk.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli_traffic_light.claude import ClaudeReader
from cli_traffic_light.state import SessionState

NOW = 1_753_700_000.0
DEAD_PID = 999_001


def _real_proc_start() -> str:
    """Field 22 of ``/proc/self/stat`` — the value Claude stores as ``procStart``."""
    with open("/proc/self/stat") as handle:
        return handle.read().rsplit(")", 1)[1].split()[19]


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _usage(input_tokens, output_tokens, cache_creation=0, cache_read=0) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
    }


def _assistant(epoch, message_id, request_id, usage, is_sidechain=False) -> dict:
    return {
        "type": "assistant",
        "isSidechain": is_sidechain,
        "requestId": request_id,
        "timestamp": _iso(epoch),
        "message": {"id": message_id, "role": "assistant", "usage": usage},
    }


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _write_session_file(home: Path, pid, session_id, cwd, status, name, proc_start, updated):
    record = {
        "pid": pid,
        "sessionId": session_id,
        "cwd": cwd,
        "startedAt": _iso(NOW - 1_200),
        "version": "2.1.220",
        "kind": "cli",
        "entrypoint": "cli",
        "name": name,
        "status": status,
        "updatedAt": _iso(updated),
        "statusUpdatedAt": _iso(updated),
        "bridgeSessionId": None,
    }
    if proc_start is not None:
        record["procStart"] = proc_start
    path = home / "sessions" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _build_home(tmp_path: Path) -> Path:
    home = tmp_path / "claude_home"
    live_pid = os.getpid()

    _write_session_file(
        home, live_pid, "sess-live", "/work/live", "busy", "live one",
        _real_proc_start(), NOW - 10,
    )
    _write_session_file(
        home, DEAD_PID, "sess-dead", "/work/dead", "busy", "dead one",
        "12345", NOW - 600,
    )

    _write_jsonl(
        home / "projects" / "-work-live" / "sess-live.jsonl",
        [
            _assistant(NOW - 40, "msg-a", "req-a", _usage(100, 20, 5, 900)),
            _assistant(NOW - 20, "msg-b", "req-b", _usage(200, 30, 0, 900)),
            _assistant(NOW - 15, "msg-side", "req-side", _usage(700, 700), is_sidechain=True),
        ],
    )
    _write_jsonl(
        home / "projects" / "-work-live" / "sess-live" / "subagents" / "sub-1.jsonl",
        [_assistant(NOW - 30, "msg-sub", "req-sub", _usage(11, 3, 1))],
    )
    _write_jsonl(
        home / "projects" / "-work-dead" / "sess-dead.jsonl",
        [_assistant(NOW - 600, "msg-d", "req-d", _usage(10, 10))],
    )
    _write_jsonl(
        home / "projects" / "-work-recent" / "sess-recent.jsonl",
        [_assistant(NOW - 3_600, "msg-r", "req-r", _usage(1, 2, 3, 4))],
    )
    _write_jsonl(
        home / "projects" / "-work-old" / "sess-old.jsonl",
        [_assistant(NOW - 30 * 3_600, "msg-o", "req-o", _usage(5, 5))],
    )
    return home


def _by_id(sessions):
    return {session.session_id: session for session in sessions}


def test_live_session_with_real_proc_start_is_running(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    live = _by_id(ClaudeReader(home, now=lambda: NOW).read_sessions())["sess-live"]
    assert live.state == SessionState.RUNNING
    assert live.agent == "claude"
    assert live.cwd == "/work/live"
    assert live.pid == os.getpid()


def test_exactly_the_expected_sessions_are_listed(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    sessions = ClaudeReader(home, now=lambda: NOW).read_sessions()
    assert len(sessions) == 3
    assert sorted(s.session_id for s in sessions) == ["sess-dead", "sess-live", "sess-recent"]


def test_session_whose_proc_start_no_longer_matches_is_finished(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    dead = _by_id(ClaudeReader(home, now=lambda: NOW).read_sessions())["sess-dead"]
    assert dead.state == SessionState.FINISHED
    assert dead.cwd == "/work/dead"


def test_transcript_without_a_session_file_is_finished(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    recent = _by_id(ClaudeReader(home, now=lambda: NOW).read_sessions())["sess-recent"]
    assert recent.state == SessionState.FINISHED
    assert recent.usage.total_tokens == 6


def test_transcript_older_than_the_recency_bound_is_absent(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    sessions = ClaudeReader(home, now=lambda: NOW).read_sessions()
    assert "sess-old" not in _by_id(sessions)


def test_sidechain_and_subagent_usage_is_excluded_from_the_session_total(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    live = _by_id(ClaudeReader(home, now=lambda: NOW).read_sessions())["sess-live"]
    assert live.usage.total_tokens == 355
    assert live.usage.cache_read_tokens == 1_800


def test_subagent_token_total_is_a_non_zero_separate_aggregate(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    total = ClaudeReader(home, now=lambda: NOW).subagent_token_total()
    assert total.total_tokens == 1_415


def test_session_name_surfaces_as_the_title(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    live = _by_id(ClaudeReader(home, now=lambda: NOW).read_sessions())["sess-live"]
    assert live.title == "live one"


def test_tokens_per_sec_uses_clamped_deltas_of_main_thread_records(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    live = _by_id(ClaudeReader(home, now=lambda: NOW).read_sessions())["sess-live"]
    assert live.tokens_per_sec == pytest.approx(230.0 / 20.0)


def test_last_activity_tracks_the_most_recent_signal(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    sessions = _by_id(ClaudeReader(home, now=lambda: NOW).read_sessions())
    assert sessions["sess-live"].last_activity == pytest.approx(NOW - 10, abs=1.0)
    assert sessions["sess-recent"].last_activity == pytest.approx(NOW - 3_600, abs=1.0)


def test_dead_pid_reported_by_the_process_provider_is_finished(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    reader = ClaudeReader(home, proc_info=lambda pid: None, now=lambda: NOW)
    sessions = _by_id(reader.read_sessions())
    assert sessions["sess-live"].state == SessionState.FINISHED
    assert sessions["sess-dead"].state == SessionState.FINISHED


def test_pid_reuse_with_a_mismatched_proc_start_is_finished(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    reused_ticks = int(_real_proc_start()) + 12_345
    reader = ClaudeReader(home, proc_info=lambda pid: (1.0, reused_ticks), now=lambda: NOW)
    sessions = _by_id(reader.read_sessions())
    assert sessions["sess-live"].state == SessionState.FINISHED


def test_missing_proc_start_never_forces_finished(tmp_path, monkeypatch):
    home = tmp_path / "claude_home_no_procstart"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    _write_session_file(
        home, os.getpid(), "sess-nostart", "/work/nostart", "shell", "no procstart",
        None, NOW - 5,
    )
    _write_jsonl(
        home / "projects" / "-work-nostart" / "sess-nostart.jsonl",
        [_assistant(NOW - 60, "msg-n", "req-n", _usage(4, 6))],
    )
    sessions = ClaudeReader(home, now=lambda: NOW).read_sessions()
    assert len(sessions) == 1
    assert sessions[0].state == SessionState.RUNNING


def test_duplicate_message_and_request_ids_keep_the_last_usage(tmp_path, monkeypatch):
    home = tmp_path / "claude_home_dup"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    _write_session_file(
        home, os.getpid(), "sess-dup", "/work/dup", "idle", "dup one",
        _real_proc_start(), NOW - 5,
    )
    _write_jsonl(
        home / "projects" / "-work-dup" / "sess-dup.jsonl",
        [
            _assistant(NOW - 30, "msg-x", "req-x", _usage(10, 1)),
            _assistant(NOW - 20, "msg-x", "req-x", _usage(400, 40)),
        ],
    )
    sessions = ClaudeReader(home, now=lambda: NOW).read_sessions()
    assert len(sessions) == 1
    total = sessions[0].usage.total_tokens
    assert total == 440
    assert total != 11
    assert total != 451
    assert sessions[0].state == SessionState.NEEDS_INPUT
