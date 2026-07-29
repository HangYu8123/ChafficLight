"""Reader for Claude Code CLI on-disk session state and transcripts.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with token usage, reading the state the CLIs
themselves keep on disk.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

import psutil

from .jsonl import parse_iso, read_jsonl
from .state import Session, SessionState, claude_status_to_state, is_stale
from .tokens import TokenUsage, claude_usage_from_record, tokens_per_second

__all__ = ["ClaudeReader"]

#: Zero-based index of ``starttime`` (field 22) in ``/proc/<pid>/stat``, counted
#: after the ``(comm)`` field, which may itself contain spaces or brackets.
_STAT_STARTTIME_OFFSET = 19

#: Only transcript lines carrying this key can hold token usage, so the rest are
#: skipped before being parsed.
_USAGE_KEY = '"usage"'


def _default_proc_info(pid: int) -> tuple[float, int] | None:
    """``(create_time, /proc/<pid>/stat field 22)`` for a live pid, else ``None``."""
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    try:
        create_time = psutil.Process(pid).create_time()
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        return create_time, int(fields[_STAT_STARTTIME_OFFSET])
    except (psutil.Error, OSError, IndexError, ValueError):
        return None


def _usage_samples(records: Iterable[dict]) -> list[tuple[float, TokenUsage]]:
    """``(timestamp, usage)`` per billed record, deduped on ``(message id, requestId)``.

    A retried request reappears with the same ids and the corrected usage, so the
    last occurrence wins rather than being added twice.
    """
    latest: dict[tuple, tuple[float, TokenUsage]] = {}
    for position, record in enumerate(records):
        message = record.get("message") or {}
        usage = message.get("usage")
        if not usage:
            continue
        message_id, request_id = message.get("id"), record.get("requestId")
        # Records carrying neither id cannot be deduped against each other, so
        # they get a per-record key rather than all collapsing onto one.
        key = (message_id, request_id) if (message_id or request_id) else (position,)
        latest[key] = (
            parse_iso(record.get("timestamp")) or 0.0,
            claude_usage_from_record(usage),
        )
    return sorted(latest.values(), key=lambda sample: sample[0])


def _total_usage(samples: Iterable[tuple[float, TokenUsage]]) -> TokenUsage:
    """Sum the usage of ``samples`` into one :class:`TokenUsage`."""
    total = TokenUsage()
    for _, usage in samples:
        total.add(usage)
    return total


def _rate(samples: list[tuple[float, TokenUsage]], now: float) -> float:
    """Tokens/sec from the running cumulative total across ``samples``."""
    cumulative = []
    running = 0
    for timestamp, usage in samples:
        running += usage.total_tokens
        cumulative.append((timestamp, running))
    return tokens_per_second(cumulative, now)


class ClaudeReader:
    """Reads ``$CLAUDE_CONFIG_DIR`` read-only and yields normalised sessions."""

    def __init__(self, home: Path, proc_info=None, now=None):
        """Build a reader over ``home``.

        ``proc_info`` is a callable taking a pid and returning
        ``(create_time, procstart_ticks)`` for a live process or ``None`` when the
        pid is gone; it defaults to a psutil-backed lookup. ``now`` is a callable
        returning epoch seconds, defaulting to ``time.time``.
        """
        self.home = Path(home)
        self._proc_info = proc_info or _default_proc_info
        self._now = now or time.time
        self._samples: dict[Path, tuple[tuple, tuple[list, list]]] = {}

    def read_sessions(self) -> list[Session]:
        """All main-thread Claude sessions seen within the recency bound."""
        now = self._now()
        transcripts = {
            path.stem: path for path in sorted((self.home / "projects").glob("*/*.jsonl"))
        }
        sessions = []
        for path in sorted((self.home / "sessions").glob("*.json")):
            session = self._live_session(path, transcripts, now)
            if session is not None:
                sessions.append(session)
        for session_id, path in transcripts.items():
            if _mtime_is_stale(path, now):
                continue
            session = self._finished_session(session_id, path, now)
            if session is not None:
                sessions.append(session)
        return sessions

    def subagent_token_total(self) -> TokenUsage:
        """Aggregate usage of sidechain records and ``subagents/`` transcripts.

        Covers the same recency bound as :meth:`read_sessions`, so the aggregate
        describes the subagent work behind the sessions being listed.
        """
        now = self._now()
        projects = self.home / "projects"
        samples: list[tuple[float, TokenUsage]] = []
        for path in sorted(projects.glob("*/*.jsonl")):
            if _mtime_is_stale(path, now):
                continue
            samples += self._transcript_samples(path)[1]
        for path in sorted(projects.glob("*/*/subagents/*.jsonl")):
            if _mtime_is_stale(path, now):
                continue
            main, sidechain = self._transcript_samples(path)
            samples += main + sidechain
        return _total_usage(samples)

    def _transcript_samples(self, path: Path) -> tuple[list, list]:
        """``(main-thread, sidechain)`` usage samples of one transcript.

        Parsed once per file revision and kept: a refresh walks the same
        transcripts twice — once for the sessions, once for the subagent
        aggregate — and re-reading megabytes each time is what froze the window.
        The ``(mtime, size)`` key re-reads any transcript that has been appended
        to since, so a cached entry can never go stale.
        """
        try:
            stat = path.stat()
        except OSError:
            return [], []
        key = (stat.st_mtime, stat.st_size)
        cached = self._samples.get(path)
        if cached is None or cached[0] != key:
            main, sidechain = [], []
            for record in read_jsonl(path, only_lines_with=_USAGE_KEY):
                (sidechain if record.get("isSidechain") else main).append(record)
            cached = (key, (_usage_samples(main), _usage_samples(sidechain)))
            self._samples[path] = cached
        return cached[1]

    def _live_session(self, path: Path, transcripts: dict, now: float) -> Session | None:
        """Build the session a ``sessions/<pid>.json`` file describes."""
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        session_id = record.get("sessionId")
        transcript = transcripts.pop(session_id, None)
        samples = self._transcript_samples(transcript)[0] if transcript else []
        last_activity = max(
            parse_iso(record.get("updatedAt")) or 0.0,
            samples[-1][0] if samples else 0.0,
        )
        if is_stale(last_activity, now):
            return None
        state = (
            claude_status_to_state(record.get("status"))
            if self._is_alive(record.get("pid"), record.get("procStart"))
            else SessionState.FINISHED
        )
        return Session(
            session_id=session_id,
            agent="claude",
            title=record.get("name") or session_id,
            cwd=record.get("cwd", ""),
            state=state,
            usage=_total_usage(samples),
            tokens_per_sec=_rate(samples, now),
            is_vscode=False,
            vscode_confidence="none",
            last_activity=last_activity,
            pid=record.get("pid"),
        )

    def _finished_session(self, session_id: str, path: Path, now: float) -> Session | None:
        """Build the session a transcript with no live session file describes."""
        samples = self._transcript_samples(path)[0]
        last_activity = samples[-1][0] if samples else 0.0
        if is_stale(last_activity, now):
            return None
        return Session(
            session_id=session_id,
            agent="claude",
            title=session_id,
            cwd=path.parent.name.replace("-", "/"),
            state=SessionState.FINISHED,
            usage=_total_usage(samples),
            tokens_per_sec=_rate(samples, now),
            is_vscode=False,
            vscode_confidence="none",
            last_activity=last_activity,
            pid=None,
        )

    def _is_alive(self, pid, proc_start) -> bool:
        """Whether ``pid`` is still the process the session file recorded.

        ``procStart`` is field 22 of ``/proc/<pid>/stat`` — boot-relative clock
        ticks, not epoch seconds — and pins the identity of a pid that may since
        have been reused. When it is missing or unparseable there is nothing to
        compare, so pid liveness alone decides.
        """
        info = self._proc_info(pid)
        if info is None:
            return False
        try:
            recorded_ticks = int(proc_start)
        except (TypeError, ValueError):
            return True
        return recorded_ticks == info[1]


def _mtime_is_stale(path: Path, now: float) -> bool:
    """Whether ``path`` was last written outside the recency bound.

    A cheap pre-filter, since a transcript's records can never be newer than the
    file holding them: an old mtime rules the whole file out without reading and
    JSON-parsing it. It only ever *adds* a skip — every file it lets through is
    still judged on its record timestamps by :func:`~.state.is_stale`.
    """
    try:
        return is_stale(path.stat().st_mtime, now)
    except OSError:
        return True
