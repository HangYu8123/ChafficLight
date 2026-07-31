"""Combines both CLI readers into a single snapshot of every session.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with token usage.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import psutil

from .claude import ClaudeReader
from .codex import CodexReader
from .state import Session
from .tokens import TokenUsage, growth
from .vscode import detect_vscode

__all__ = ["Monitor"]


def _resolve_home(explicit: Path | None, env_var: str, default: str) -> Path:
    """The explicit path, else ``$env_var``, else ``default`` under the user's home."""
    if explicit is not None:
        return Path(explicit)
    from_env = os.environ.get(env_var)
    return Path(from_env) if from_env else Path(default).expanduser()


def _process_facts(pid: int | None) -> tuple[dict, list[str]]:
    """``(environ, ancestor names and executable paths)`` for ``pid``.

    Every part of this is best-effort: ``environ()`` raises ``AccessDenied`` for
    another user's process and the pid can vanish mid-read, so each lookup
    degrades to empty rather than raising.
    """
    if pid is None:
        return {}, []
    try:
        process = psutil.Process(pid)
        env = process.environ()
    except (psutil.Error, OSError):
        return {}, []

    try:
        ancestors = process.parents()
    except (psutil.Error, OSError):
        ancestors = []
    names: list[str] = []
    for ancestor in ancestors:
        try:
            names += [ancestor.name(), ancestor.exe()]
        except (psutil.Error, OSError):
            continue
    return env, names


def _ide_locks(claude_home: Path) -> list[dict]:
    """The ``ide/*.lock`` files, in the shape :func:`detect_vscode` expects."""
    locks = []
    for path in sorted((claude_home / "ide").glob("*.lock")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        pid = record.get("pid")
        locks.append(
            {
                "pid": pid,
                "alive": isinstance(pid, int) and psutil.pid_exists(pid),
                "workspaceFolders": record.get("workspaceFolders") or [],
            }
        )
    return locks


def _add_vscode_detection(session: Session, ide_locks: list[dict]) -> None:
    """Fill in a session's VS Code fields; the readers cannot see live processes."""
    env, ancestor_names = _process_facts(session.pid)
    # The session's own cwd is more trustworthy than an inherited ``PWD``.
    detection = detect_vscode({**env, "PWD": session.cwd}, ancestor_names, ide_locks)
    session.is_vscode = detection.detected
    session.vscode_confidence = detection.confidence


class Monitor:
    """Read-only view over both CLI homes."""

    def __init__(self, claude_home: Path | None = None, codex_home: Path | None = None):
        """Resolve the two homes.

        ``None`` falls back to ``$CLAUDE_CONFIG_DIR`` / ``$CODEX_HOME`` and then
        to the user's ``~/.claude`` / ``~/.codex``.
        """
        self.claude_home = _resolve_home(claude_home, "CLAUDE_CONFIG_DIR", "~/.claude")
        self.codex_home = _resolve_home(codex_home, "CODEX_HOME", "~/.codex")
        self._latest: list[Session] | None = None
        # One reader, so the sessions and the subagent aggregate share its
        # per-transcript parse cache instead of each re-reading every file.
        self._claude = ClaudeReader(self.claude_home)
        # Both CLIs count tokens cumulatively and for as long as a transcript
        # survives, so "used since this monitor was created" has to be built up
        # here: the last count seen for each session, and the running sum of
        # every rise in one. See `_accumulate`.
        self._seen: dict[tuple[str, str], TokenUsage] = {}
        self._since_start = TokenUsage()
        self._baselined = False

    def snapshot(self) -> list[Session]:
        """Every currently visible session from both CLIs."""
        sessions = self._claude.read_sessions()
        sessions += CodexReader(self.codex_home).read_sessions()
        ide_locks = _ide_locks(self.claude_home)
        for session in sessions:
            _add_vscode_detection(session, ide_locks)
        self._accumulate(sessions)
        self._latest = sessions
        return sessions

    def _accumulate(self, sessions: list[Session]) -> None:
        """Fold this snapshot's token growth into the since-start total.

        Counted per session and only upward, which is what makes the figure
        monotonic. Summing the visible sessions and subtracting a starting
        figure would not be: sessions end, and drop off the snapshot 24 hours
        later, and the total would then walk *backwards* past work that was
        genuinely done — eventually to zero, on a widget left open for a day.

        Everything already running when the first snapshot is taken is recorded
        without being counted, so the total reads "since this monitor was
        created" rather than "since these sessions began". Anything first seen
        after that started after it too, and counts in full.
        """
        for session in sessions:
            # Keyed by CLI as well as id: nothing stops the two from minting the
            # same session id, and one shadowing the other would drop its tokens.
            key = (session.agent, session.session_id)
            previous = self._seen.get(key)
            if previous is not None:
                self._since_start.add(growth(previous, session.usage))
            elif self._baselined:
                self._since_start.add(session.usage)
            # Copied, because the reader owns that object and `add` mutates.
            self._seen[key] = replace(session.usage)
        self._baselined = True

    def subagent_totals(self) -> TokenUsage:
        """Claude subagent token usage, aggregated apart from the session totals."""
        return self._claude.subagent_token_total()

    def totals(self) -> TokenUsage:
        """Token usage summed across the sessions in the latest snapshot.

        The lifetime figure, counting everything each visible session has ever
        spent. `totals_since_start` is the one the window shows; this one is what
        ``--once`` reports, where a since-start total would be nothing but zeroes
        because the process only ever takes one snapshot.
        """
        if self._latest is None:
            self.snapshot()
        total = TokenUsage()
        for session in self._latest:
            total.add(session.usage)
        return total

    def totals_since_start(self) -> TokenUsage:
        """Token usage accrued since this monitor was created.

        A copy: the caller must not be able to add to the running total through
        the object it is handed.
        """
        if self._latest is None:
            self.snapshot()
        return replace(self._since_start)

