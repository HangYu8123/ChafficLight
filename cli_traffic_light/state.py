"""Session model, traffic-light colours, and the per-CLI state mapping rules.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running (green), waiting
for their input (yellow) or finished (red), along with token usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .tokens import TokenUsage

__all__ = [
    "MAX_AGE_HOURS",
    "STALE_SECONDS",
    "STATE_COLORS",
    "Session",
    "SessionState",
    "claude_status_to_state",
    "codex_events_to_state",
    "is_stale",
]


class SessionState(str, Enum):
    """The four lights a session can show."""

    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    FINISHED = "finished"
    UNKNOWN = "unknown"


STATE_COLORS: dict[SessionState, str] = {
    SessionState.RUNNING: "#2ecc40",
    SessionState.NEEDS_INPUT: "#ffdc00",
    SessionState.FINISHED: "#ff4136",
    SessionState.UNKNOWN: "#aaaaaa",
}

#: A Codex rollout untouched for longer than this is treated as finished.
STALE_SECONDS = 900

#: Sessions with no activity in this many hours are not listed at all.
MAX_AGE_HOURS = 24


def is_stale(last_activity: float, now: float) -> bool:
    """Whether a session is too old to be worth listing."""
    return now - last_activity > MAX_AGE_HOURS * 3_600


@dataclass
class Session:
    """One monitored chat session, normalised across both CLIs."""

    session_id: str
    agent: str
    title: str
    cwd: str
    state: SessionState
    usage: TokenUsage
    tokens_per_sec: float
    is_vscode: bool
    vscode_confidence: str
    last_activity: float
    pid: int | None


#: Exact (case-sensitive) Claude ``status`` values we recognise; anything else is UNKNOWN.
_CLAUDE_STATUS_STATES = {
    "busy": SessionState.RUNNING,
    "shell": SessionState.RUNNING,
    "idle": SessionState.NEEDS_INPUT,
}

#: Codex turn events we recognise; any other event (or none) is UNKNOWN.
_CODEX_TURN_EVENT_STATES = {
    "task_started": SessionState.RUNNING,
    "task_complete": SessionState.NEEDS_INPUT,
    "turn_aborted": SessionState.NEEDS_INPUT,
}


def claude_status_to_state(status: str) -> SessionState:
    """Map a Claude session file's ``status`` field onto a light."""
    return _CLAUDE_STATUS_STATES.get(status, SessionState.UNKNOWN)


def codex_events_to_state(
    last_turn_event: str | None,
    mtime: float,
    now: float,
) -> SessionState:
    """Map a Codex rollout's last turn event plus its file mtime onto a light."""
    if now - mtime > STALE_SECONDS:
        return SessionState.FINISHED
    return _CODEX_TURN_EVENT_STATES.get(last_turn_event, SessionState.UNKNOWN)
