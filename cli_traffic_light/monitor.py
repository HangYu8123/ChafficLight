"""Combines both CLI readers into a single snapshot of every session.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with token usage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psutil

from .claude import ClaudeReader
from .codex import CodexReader
from .state import Session
from .tokens import TokenUsage
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

    def snapshot(self) -> list[Session]:
        """Every currently visible session from both CLIs."""
        sessions = self._claude.read_sessions()
        sessions += CodexReader(self.codex_home).read_sessions()
        ide_locks = _ide_locks(self.claude_home)
        for session in sessions:
            _add_vscode_detection(session, ide_locks)
        self._latest = sessions
        return sessions

    def subagent_totals(self) -> TokenUsage:
        """Claude subagent token usage, aggregated apart from the session totals."""
        return self._claude.subagent_token_total()

    def totals(self) -> TokenUsage:
        """Token usage summed across the sessions in the latest snapshot."""
        if self._latest is None:
            self.snapshot()
        total = TokenUsage()
        for session in self._latest:
            total.add(session.usage)
        return total

