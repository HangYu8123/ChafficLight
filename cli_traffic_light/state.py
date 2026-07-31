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
    """The lights a session can show.

    ``NEEDS_INPUT`` is deliberately narrow: it means the agent has *asked the
    user something* and cannot continue until it is answered. A session that
    merely finished its turn and is sitting at its prompt is ``IDLE`` — nothing
    is blocked on the user, so it must not compete for their attention.
    """

    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    IDLE = "idle"
    FINISHED = "finished"
    UNKNOWN = "unknown"


#: The signal reads the way a road signal does, from the *session's* point of
#: view rather than the driver's: green it is moving, yellow slow down and give
#: it your attention because it has asked you something, red it has stopped —
#: the turn is over and nothing moves again until you type. So the colour tracks
#: how much is still happening on its own, and yellow — the one colour that
#: means "act" on a road — is the one that wants you. A session that has ended
#: is none of the three and takes blue.
#:
#: Red is a muted brick rather than the signal red it started as, and it is
#: deliberately the quietest of the three: it means "nothing is happening here",
#: it is lit whenever a session is simply sitting at its prompt, and so it is
#: the colour the face wears most of the time. At full saturation that made the
#: widget shout its least urgent state. Toned down it still reads red — the hue
#: barely moves, which is what keeps it left of yellow on the housing — while
#: yellow and green now stand further off the case than it does.
STATE_COLORS: dict[SessionState, str] = {
    SessionState.RUNNING: "#2ecc40",
    SessionState.NEEDS_INPUT: "#ffdc00",
    SessionState.IDLE: "#c2564e",
    SessionState.FINISHED: "#3498db",
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


#: Exact (case-sensitive) Claude ``status`` values we recognise; anything else is
#: UNKNOWN. The CLI writes exactly four: ``busy`` while the turn runs, ``shell``
#: when the turn is over but a background shell command is still executing,
#: ``waiting`` while a dialog is open and the agent is blocked on the user, and
#: ``idle`` otherwise. Only ``waiting`` is a question for the user — it is
#: accompanied by a ``waitingFor`` note reading "permission prompt", "input
#: needed", "sandbox request", "worker request" or "dialog open".
_CLAUDE_STATUS_STATES = {
    "busy": SessionState.RUNNING,
    "shell": SessionState.RUNNING,
    "waiting": SessionState.NEEDS_INPUT,
    "idle": SessionState.IDLE,
}

#: Codex turn events we recognise; any other event (or none) is UNKNOWN. Codex
#: never persists its approval prompts to a rollout, so none of these can mean
#: "the agent asked you something": a turn either runs, ends, or is cancelled.
_CODEX_TURN_EVENT_STATES = {
    "task_started": SessionState.RUNNING,
    "task_complete": SessionState.IDLE,
    "turn_aborted": SessionState.IDLE,
}


def claude_status_to_state(status: str) -> SessionState:
    """Map a Claude session file's ``status`` field onto a light."""
    return _CLAUDE_STATUS_STATES.get(status, SessionState.UNKNOWN)


def codex_events_to_state(
    last_turn_event: str | None,
    mtime: float,
    now: float,
) -> SessionState:
    """Map a Codex rollout's last turn event plus its file mtime onto a light.

    Staleness is checked first and covers every event, because a rollout is the
    only liveness signal Codex offers: nothing records that a session ended, so
    one untouched for :data:`STALE_SECONDS` is treated as gone whether its last
    turn started, finished or was cancelled.
    """
    if now - mtime > STALE_SECONDS:
        return SessionState.FINISHED
    return _CODEX_TURN_EVENT_STATES.get(last_turn_event, SessionState.UNKNOWN)
