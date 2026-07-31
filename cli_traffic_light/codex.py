"""Reader for Codex CLI rollout files and its session index.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with token usage, reading the state the CLIs
themselves keep on disk.
"""

from __future__ import annotations

import time
from pathlib import Path

from .jsonl import parse_iso, read_jsonl
from .state import Session, SessionState, codex_events_to_state, is_stale
from .tokens import TokenUsage, codex_usage_from_total, observed_output_rate

__all__ = ["CodexReader"]

#: Rollout events that mark a turn boundary; the last one seen drives the state.
_TURN_EVENTS = ("task_started", "task_complete", "turn_aborted")


def _scan_events(records: list[dict]) -> tuple[str | None, TokenUsage, list[tuple[float, int]]]:
    """``(last turn event, session usage, rate samples)`` from a rollout's events."""
    last_turn_event = None
    usage = TokenUsage()
    rate_samples: list[tuple[float, int]] = []
    for record in records:
        if record.get("type") != "event_msg":
            continue
        payload = record.get("payload") or {}
        event_type = payload.get("type")
        if event_type in _TURN_EVENTS:
            last_turn_event = event_type
            if event_type == "task_started":
                # Never divide across the user's wait between two turns. The
                # first current-turn sample is only a baseline; a second one is
                # required before this turn has an observable interval.
                rate_samples = []
        elif event_type == "token_count":
            total = (payload.get("info") or {}).get("total_token_usage")
            if total is None:
                continue
            # These records are cumulative, so the last one is the session
            # total and successive ones give the rate — they are never summed.
            usage = codex_usage_from_total(total)
            timestamp = parse_iso(record.get("timestamp"))
            if timestamp is not None:
                rate_samples.append((timestamp, usage.output_tokens))
    return last_turn_event, usage, rate_samples


class CodexReader:
    """Reads ``$CODEX_HOME`` read-only; the state sqlite file is never opened."""

    def __init__(self, home: Path, now=None):
        """Build a reader over ``home``; ``now`` returns epoch seconds."""
        self.home = Path(home)
        self._now = now or time.time

    def read_sessions(self) -> list[Session]:
        """All main-thread Codex sessions seen within the recency bound."""
        now = self._now()
        thread_names = self._thread_names()
        sessions = []
        for path in sorted((self.home / "sessions").rglob("rollout-*.jsonl")):
            session = self._read_rollout(path, thread_names, now)
            if session is not None:
                sessions.append(session)
        return sessions

    def _thread_names(self) -> dict[str, str]:
        """Session id to human thread name, from ``session_index.jsonl``."""
        return {
            record["id"]: record["thread_name"]
            for record in read_jsonl(self.home / "session_index.jsonl")
            if record.get("id") and record.get("thread_name")
        }

    def _read_rollout(self, path: Path, thread_names: dict, now: float) -> Session | None:
        """Build the session one ``rollout-*.jsonl`` describes, or skip it."""
        # Checked before the file is read: a rollout outside the recency bound is
        # dropped whatever it contains, and parsing every historical rollout only
        # to discard it is what made a snapshot take seconds.
        try:
            mtime = path.stat().st_mtime
        except OSError:  # rotated or deleted between the glob and here
            return None
        if is_stale(mtime, now):
            return None

        records = list(read_jsonl(path))
        meta = next(
            (r.get("payload") or {} for r in records if r.get("type") == "session_meta"),
            None,
        )
        if meta is None or meta.get("thread_source") == "subagent":
            return None

        last_turn_event, usage, rate_samples = _scan_events(records)
        session_id = meta.get("session_id")
        state = codex_events_to_state(last_turn_event, mtime, now)
        if state is SessionState.RUNNING:
            rate = observed_output_rate(rate_samples)
        elif state is SessionState.UNKNOWN:
            rate = None
        else:
            rate = 0.0
        return Session(
            session_id=session_id,
            agent="codex",
            title=thread_names.get(session_id, session_id),
            cwd=meta.get("cwd", ""),
            state=state,
            usage=usage,
            tokens_per_sec=rate,
            is_vscode=False,
            vscode_confidence="none",
            last_activity=mtime,
            pid=None,
        )
