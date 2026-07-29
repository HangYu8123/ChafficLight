"""The Tk traffic-light window: one coloured light per chat session.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, a green light while it is running, a
yellow light when it needs their input and a red light once it has finished,
along with token usage.
"""

from __future__ import annotations

import tkinter as tk

from .monitor import Monitor
from .state import STATE_COLORS, Session

__all__ = ["TrafficLightApp"]

#: Height of the scrolling rows viewport, so more rows than fit stay reachable.
_ROWS_VIEWPORT_PX = 400


def _vscode_text(session: Session) -> str:
    """Whether this session's terminal is VS Code's, and how sure the detection is."""
    if not session.is_vscode:
        return "terminal"
    return f"VS Code ({session.vscode_confidence})"


class TrafficLightApp:
    """Renders one row per session and repaints on a timer."""

    def __init__(self, root: tk.Tk, monitor: Monitor, refresh_ms: int = 2000):
        """Attach the app to ``root`` and poll ``monitor`` every ``refresh_ms``."""
        self._root = root
        self._monitor = monitor
        self._refresh_ms = refresh_ms
        self._rows: list[dict] = []
        self._after_id: str | None = None

        root.title("CLI Traffic Light")
        frame = tk.Frame(root, padx=12, pady=10)
        frame.pack(fill="both", expand=True)
        rows_area = tk.Frame(frame)
        rows_area.pack(fill="both", expand=True)
        canvas = tk.Canvas(rows_area, height=_ROWS_VIEWPORT_PX, highlightthickness=0)
        scrollbar = tk.Scrollbar(rows_area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._rows_frame = tk.Frame(canvas)
        canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        self._totals_label = tk.Label(frame, text="", anchor="w")
        self._totals_label.pack(fill="x", pady=(8, 0))
        self._schedule()

    def refresh(self) -> None:
        """Pull a fresh snapshot and rebuild the rows."""
        sessions = self._monitor.snapshot()
        for widget in self._rows_frame.winfo_children():
            widget.destroy()
        self._rows = [self._build_row(session) for session in sessions]
        totals = self._monitor.totals()
        subagent = self._monitor.subagent_totals()
        self._totals_label.config(
            text=(
                f"{len(sessions)} session(s) — {totals.total_tokens:,} tokens total"
                f" — {subagent.total_tokens:,} subagent tokens"
            )
        )

    def rows(self) -> list[dict]:
        """The current rows as ``{"session_id", "light", "labels"}`` mappings."""
        return list(self._rows)

    def stop(self) -> None:
        """Cancel every pending ``after()`` callback this app scheduled."""
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None

    def _build_row(self, session: Session) -> dict:
        """Build one session's widgets and return its row mapping."""
        row = tk.Frame(self._rows_frame)
        row.pack(fill="x", pady=2)
        light = tk.Label(row, width=2, background=STATE_COLORS[session.state], relief="raised")
        light.pack(side="left", padx=(0, 8))
        labels = {
            "agent": tk.Label(row, text=session.agent, anchor="w", width=7),
            "title": tk.Label(row, text=session.title, anchor="w", width=34),
            "state": tk.Label(row, text=session.state.value, anchor="w", width=12),
            "vscode": tk.Label(row, text=_vscode_text(session), anchor="w", width=20),
            "tokens": tk.Label(
                row, text=f"{session.usage.total_tokens:,} tok", anchor="e", width=12
            ),
            "rate": tk.Label(
                row, text=f"{session.tokens_per_sec:.1f} tok/s", anchor="e", width=12
            ),
        }
        for label in labels.values():
            label.pack(side="left")
        return {"session_id": session.session_id, "light": light, "labels": labels}

    def _schedule(self) -> None:
        """Queue the next repaint; only ever one callback is pending at a time."""
        self._after_id = self._root.after(self._refresh_ms, self._tick)

    def _tick(self) -> None:
        """Repaint, then queue the repaint after this one.

        The reschedule is in a ``finally`` because a refresh that raises must not
        stop the timer: a traffic light frozen on stale colours is worse than one
        that skips a frame.
        """
        self._after_id = None
        try:
            self.refresh()
        finally:
            self._schedule()
