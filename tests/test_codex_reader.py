"""Frozen acceptance tests for the Codex CLI rollout reader.

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

from cli_traffic_light.codex import CodexReader
from cli_traffic_light.state import SessionState

NOW = 1_753_700_000.0


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _record(epoch, record_type, payload) -> dict:
    return {"timestamp": _iso(epoch), "type": record_type, "payload": payload}


def _meta(epoch, session_id, cwd, thread_source="cli") -> dict:
    return _record(
        epoch,
        "session_meta",
        {
            "session_id": session_id,
            "cwd": cwd,
            "cli_version": "0.146.0-alpha.3.1",
            "thread_source": thread_source,
            "source": "cli",
            "model_provider": "openai",
            "context_window": 272_000,
        },
    )


def _event(epoch, event_type, **extra) -> dict:
    payload = {"type": event_type}
    payload.update(extra)
    return _record(epoch, "event_msg", payload)


def _token_count(epoch, total_token_usage) -> dict:
    return _event(
        epoch,
        "token_count",
        info={"total_token_usage": total_token_usage, "last_token_usage": {}},
    )


def _write_rollout(home: Path, day, name, records) -> Path:
    path = home / "sessions" / day / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _build_home(tmp_path: Path) -> Path:
    home = tmp_path / "codex_home"

    running = _write_rollout(
        home, "2026/07/28", "2026-07-28T10-00-00-run",
        [
            _meta(NOW - 600, "codex-run", "/work/run"),
            _event(NOW - 300, "user_message", message="keep going"),
            _event(NOW - 290, "task_started"),
            _token_count(NOW - 40, {
                "input_tokens": 1_000, "cached_input_tokens": 400,
                "output_tokens": 20, "cache_write_input_tokens": 0,
                "total_tokens": 1_020,
            }),
            _token_count(NOW - 20, {
                "input_tokens": 2_000, "cached_input_tokens": 900,
                "output_tokens": 80, "cache_write_input_tokens": 0,
                "total_tokens": 2_080,
            }),
        ],
    )
    aborted = _write_rollout(
        home, "2026/07/28", "2026-07-28T10-05-00-abort",
        [
            _meta(NOW - 500, "codex-abort", "/work/abort"),
            _event(NOW - 400, "task_started"),
            _event(NOW - 350, "turn_aborted"),
        ],
    )
    done = _write_rollout(
        home, "2026/07/28", "2026-07-28T10-06-00-done",
        [
            _meta(NOW - 450, "codex-done", "/work/done"),
            _event(NOW - 400, "task_started"),
            _event(NOW - 300, "task_complete"),
        ],
    )
    subagent = _write_rollout(
        home, "2026/07/28", "2026-07-28T10-07-00-sub",
        [
            _meta(NOW - 400, "codex-sub", "/work/sub", thread_source="subagent"),
            _event(NOW - 300, "task_started"),
        ],
    )
    old = _write_rollout(
        home, "2026/07/26", "2026-07-26T10-00-00-old",
        [
            _meta(NOW - 30 * 3_600, "codex-old", "/work/old"),
            _event(NOW - 30 * 3_600, "task_complete"),
        ],
    )

    (home / "session_index.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {"id": "codex-run", "thread_name": "Refactor the parser", "updated_at": _iso(NOW - 20)},
                {"id": "codex-abort", "thread_name": "Aborted experiment", "updated_at": _iso(NOW - 350)},
            ]
        ),
        encoding="utf-8",
    )

    for path, age in (
        (running, 20.0),
        (aborted, 350.0),
        (done, 300.0),
        (subagent, 300.0),
        (old, 30 * 3_600.0),
    ):
        os.utime(path, (NOW - age, NOW - age))
    return home


def _by_id(sessions):
    return {session.session_id: session for session in sessions}


def test_exactly_the_expected_rollouts_are_listed(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = CodexReader(home, now=lambda: NOW).read_sessions()
    assert len(sessions) == 3
    assert sorted(s.session_id for s in sessions) == ["codex-abort", "codex-done", "codex-run"]


def test_last_turn_event_drives_the_state(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())
    assert sessions["codex-run"].state == SessionState.RUNNING
    assert sessions["codex-done"].state == SessionState.IDLE


def test_turn_aborted_rollout_is_idle_not_finished(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    aborted = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())["codex-abort"]
    assert aborted.state == SessionState.IDLE
    assert aborted.state != SessionState.FINISHED


def test_no_codex_rollout_is_ever_reported_as_needing_input(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = CodexReader(home, now=lambda: NOW).read_sessions()
    assert sessions
    assert all(s.state != SessionState.NEEDS_INPUT for s in sessions)


def test_subagent_rollout_is_excluded(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = CodexReader(home, now=lambda: NOW).read_sessions()
    assert "codex-sub" not in _by_id(sessions)


def test_rollout_older_than_the_recency_bound_is_excluded(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = CodexReader(home, now=lambda: NOW).read_sessions()
    assert "codex-old" not in _by_id(sessions)


def test_thread_name_from_the_session_index_is_the_title(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())
    assert sessions["codex-run"].title == "Refactor the parser"
    assert sessions["codex-abort"].title == "Aborted experiment"


def test_title_falls_back_to_the_session_id_when_unindexed(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())
    assert sessions["codex-done"].title == "codex-done"


def test_cwd_and_agent_come_from_the_session_meta(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    running = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())["codex-run"]
    assert running.cwd == "/work/run"
    assert running.agent == "codex"


def test_only_the_last_cumulative_token_count_is_used(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    usage = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())["codex-run"].usage
    assert usage.total_tokens == 1_180
    assert usage.cache_read_tokens == 900
    assert usage.total_tokens != 1_800


def test_rate_uses_only_output_tokens_over_the_observed_interval(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    running = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())["codex-run"]
    # Output rises by 60 over the 20 seconds between the snapshots. The much
    # larger input/cache movement is billing data, not generated tokens.
    assert running.tokens_per_sec == pytest.approx(60.0 / 20.0)


def test_rate_sorts_valid_samples_and_ignores_a_bad_timestamp(tmp_path, monkeypatch):
    home = tmp_path / "codex_home_bad_rate"
    path = _write_rollout(
        home,
        "2026/07/28",
        "2026-07-28T12-00-00-rate",
        [
            _meta(NOW - 100, "codex-rate", "/work/rate"),
            _event(NOW - 90, "task_started"),
            _token_count(NOW - 10, {"input_tokens": 0, "output_tokens": 70}),
            {
                "timestamp": "not-a-timestamp",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 50_000,
                            "output_tokens": 50,
                        }
                    },
                },
            },
            _token_count(NOW - 30, {"input_tokens": 0, "output_tokens": 10}),
        ],
    )
    os.utime(path, (NOW - 5, NOW - 5))
    monkeypatch.setenv("CODEX_HOME", str(home))
    session = CodexReader(home, now=lambda: NOW).read_sessions()[0]
    assert session.tokens_per_sec == pytest.approx(60.0 / 20.0)


def test_running_rate_is_unknown_without_two_valid_output_samples(
    tmp_path, monkeypatch
):
    home = tmp_path / "codex_home_one_rate_sample"
    path = _write_rollout(
        home,
        "2026/07/28",
        "2026-07-28T12-30-00-rate",
        [
            _meta(NOW - 100, "codex-one", "/work/one"),
            _event(NOW - 90, "task_started"),
            _token_count(NOW - 10, {"input_tokens": 0, "output_tokens": 70}),
        ],
    )
    os.utime(path, (NOW - 5, NOW - 5))
    monkeypatch.setenv("CODEX_HOME", str(home))
    session = CodexReader(home, now=lambda: NOW).read_sessions()[0]
    assert session.tokens_per_sec is None


def test_rate_never_crosses_a_human_gap_between_turns(tmp_path, monkeypatch):
    home = tmp_path / "codex_home_new_turn"
    path = _write_rollout(
        home,
        "2026/07/28",
        "2026-07-28T13-00-00-rate",
        [
            _meta(NOW - 1_000, "codex-new-turn", "/work/new-turn"),
            _event(NOW - 900, "task_started"),
            _token_count(NOW - 850, {"input_tokens": 0, "output_tokens": 10}),
            _token_count(NOW - 840, {"input_tokens": 0, "output_tokens": 70}),
            _event(NOW - 830, "task_complete"),
            _event(NOW - 20, "user_message", message="continue"),
            _event(NOW - 10, "task_started"),
        ],
    )
    os.utime(path, (NOW - 5, NOW - 5))
    monkeypatch.setenv("CODEX_HOME", str(home))
    session = CodexReader(home, now=lambda: NOW).read_sessions()[0]
    assert session.state is SessionState.RUNNING
    assert session.tokens_per_sec is None


def test_non_running_codex_rate_is_exactly_zero(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())
    assert sessions["codex-abort"].tokens_per_sec == 0.0
    assert sessions["codex-done"].tokens_per_sec == 0.0


def test_last_activity_is_the_rollout_mtime(tmp_path, monkeypatch):
    home = _build_home(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    sessions = _by_id(CodexReader(home, now=lambda: NOW).read_sessions())
    assert sessions["codex-run"].last_activity == pytest.approx(NOW - 20, abs=1.0)
    assert sessions["codex-done"].last_activity == pytest.approx(NOW - 300, abs=1.0)


@pytest.mark.parametrize(
    "age_seconds, expected",
    [
        (899.0, SessionState.RUNNING),
        (901.0, SessionState.FINISHED),
    ],
)
def test_rollout_mtime_staleness_boundary(tmp_path, monkeypatch, age_seconds, expected):
    home = tmp_path / f"codex_home_{int(age_seconds)}"
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = _write_rollout(
        home, "2026/07/28", "2026-07-28T09-00-00-edge",
        [
            _meta(NOW - 1_000, "codex-edge", "/work/edge"),
            _event(NOW - 950, "task_started"),
        ],
    )
    os.utime(path, (NOW - age_seconds, NOW - age_seconds))
    sessions = CodexReader(home, now=lambda: NOW).read_sessions()
    assert len(sessions) == 1
    assert sessions[0].state == expected
