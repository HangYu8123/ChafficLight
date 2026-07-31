"""Frozen acceptance tests for the since-start token total.

Original request:
The user asked for the total token figure to reset every time ChafficLight is
restarted, so the window reports what has been spent since it opened rather than
what the transcripts on disk add up to.

Both CLIs count tokens cumulatively and keep their files for a day, so this is
accumulated rather than read: `Monitor.totals` stays the lifetime figure that
``--once`` prints, and `Monitor.totals_since_start` is the one the window shows.
The cases below are the ones where the difference between "accumulate the rises"
and "subtract a starting figure" actually shows.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli_traffic_light.claude import _default_proc_info
from cli_traffic_light.monitor import Monitor


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _write_session_file(home: Path, session_id: str, cwd: str) -> Path:
    """A live Claude session file for this very process, so the reader trusts it."""
    now = time.time()
    path = home / "sessions" / f"{os.getpid()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "sessionId": session_id,
                "cwd": cwd,
                "startedAt": _iso(now - 300),
                "procStart": str(_default_proc_info(os.getpid())[1]),
                "version": "2.1.220",
                "kind": "cli",
                "entrypoint": "cli",
                "name": "monitor fixture session",
                "status": "busy",
                "updatedAt": _iso(now - 5),
                "statusUpdatedAt": _iso(now - 5),
                "bridgeSessionId": None,
            }
        ),
        encoding="utf-8",
    )
    return path


def _transcript(home: Path, cwd: str, session_id: str) -> Path:
    return home / "projects" / cwd.replace("/", "-") / f"{session_id}.jsonl"


def _bill(path: Path, request_id: str, tokens: int, *, ago: float = 30) -> None:
    """Append one billed assistant record worth ``tokens`` output tokens."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": False,
                    "requestId": request_id,
                    "timestamp": _iso(time.time() - ago),
                    "message": {
                        "id": f"msg-{request_id}",
                        "role": "assistant",
                        "usage": {"input_tokens": 0, "output_tokens": tokens},
                    },
                }
            )
            + "\n"
        )


@pytest.fixture
def homes(tmp_path, monkeypatch) -> Path:
    """A Claude home with one live session that has already billed 500 tokens."""
    claude_home = tmp_path / "claude_home"
    codex_home = tmp_path / "codex_home"
    (codex_home / "sessions").mkdir(parents=True)
    _write_session_file(claude_home, "sess-a", "/work/a")
    _bill(_transcript(claude_home, "/work/a", "sess-a"), "req-history", 500)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    return claude_home


def test_the_first_snapshot_starts_the_count_at_zero(homes):
    """What was already on disk is history, and history is what resets.

    The lifetime total is read alongside it, because "reset" must mean the
    window's figure and not the reader losing track of the tokens.
    """
    monitor = Monitor()
    monitor.snapshot()
    assert monitor.totals_since_start().total_tokens == 0
    assert monitor.totals().total_tokens == 500


def test_tokens_billed_after_the_first_snapshot_are_counted(homes):
    monitor = Monitor()
    monitor.snapshot()
    _bill(_transcript(homes, "/work/a", "sess-a"), "req-new", 70)
    monitor.snapshot()
    assert monitor.totals_since_start().total_tokens == 70
    assert monitor.totals().total_tokens == 570


def test_a_session_that_ends_keeps_the_tokens_it_contributed(homes):
    """The failure a subtracted starting figure would have.

    A session leaves the snapshot when it is deleted, and every session leaves
    24 hours after its last activity — so a widget left open overnight would
    watch its own total walk backwards to zero past work that really happened.
    """
    monitor = Monitor()
    monitor.snapshot()
    _bill(_transcript(homes, "/work/a", "sess-a"), "req-new", 70)
    monitor.snapshot()

    _transcript(homes, "/work/a", "sess-a").unlink()
    (homes / "sessions" / f"{os.getpid()}.json").unlink()
    assert monitor.snapshot() == []
    assert monitor.totals().total_tokens == 0
    assert monitor.totals_since_start().total_tokens == 70


def test_a_session_that_appears_later_is_counted_in_full(homes):
    """It cannot have run before the monitor did, so all of it is new work."""
    monitor = Monitor()
    monitor.snapshot()

    _write_session_file(homes, "sess-b", "/work/b")
    _bill(_transcript(homes, "/work/b", "sess-b"), "req-b", 40)
    monitor.snapshot()
    assert monitor.totals_since_start().total_tokens == 40


def test_a_rewritten_transcript_cannot_take_tokens_back(homes):
    """Another process owns these files and may truncate or replace one.

    Clamped per session rather than on the sum, so a falling count contributes
    nothing instead of cancelling out a different session's real work.
    """
    monitor = Monitor()
    monitor.snapshot()
    transcript = _transcript(homes, "/work/a", "sess-a")
    _bill(transcript, "req-new", 70)
    monitor.snapshot()

    transcript.write_text("", encoding="utf-8")
    monitor.snapshot()
    assert monitor.totals_since_start().total_tokens == 70

    # And it resumes counting from wherever the rewritten file now stands,
    # rather than counting the whole of it again the moment it grows.
    _bill(transcript, "req-after-rewrite", 9)
    monitor.snapshot()
    assert monitor.totals_since_start().total_tokens == 79


def test_the_running_total_cannot_be_mutated_through_what_it_returns(homes):
    """It is handed out on every repaint, ~30 times a minute."""
    monitor = Monitor()
    monitor.snapshot()
    handed_out = monitor.totals_since_start()
    handed_out.output_tokens += 1_000_000
    assert monitor.totals_since_start().total_tokens == 0
