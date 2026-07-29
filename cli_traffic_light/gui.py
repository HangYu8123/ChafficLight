"""The Tk traffic-light window: one coloured light per chat session.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, a green light while it is running, a
yellow light when it needs their input and a red light once it has finished,
along with token usage.
"""

from __future__ import annotations

import tkinter as tk
from collections import Counter

from .monitor import Monitor
from .state import STATE_COLORS, SessionState

__all__ = ["TrafficLightApp"]

#: Housing lamps, left to right. Red before yellow before green is the
#: arrangement the MUTCD requires of a *horizontal* signal face, and it is what
#: makes the drawing read as a traffic light rather than as three coloured
#: circles.
_LAMP_ORDER = (SessionState.NEEDS_INPUT, SessionState.IDLE, SessionState.RUNNING)

#: The states with no lamp, and so no count on the face. A finished session and
#: one whose status this build cannot read are both things you can do nothing
#: about, and the light exists to answer "what needs me?" — so they are left off
#: it entirely and reported by ``--once`` / ``--once --json`` instead.
#:
#: Listed explicitly rather than derived as "everything else", because the
#: counts are read off the lamp mapping and ``Counter`` answers 0 for a key
#: nobody looks up: a state missing from both tuples would vanish in silence
#: rather than raise. Together these two must cover ``SessionState``, which
#: ``test_every_state_is_either_a_lamp_or_deliberately_off_the_face`` pins, so
#: adding a state forces a decision here instead of quietly dropping it.
_OFF_FACE_STATES = (SessionState.FINISHED, SessionState.UNKNOWN)

#: An unlit lamp, per state: a dark, same-hue version of that state's *own* lit
#: colour, so a dark lamp still says which lamp it is and the two shades move
#: together whenever a state changes colour. Spelled out rather
#: than computed from :data:`~.state.STATE_COLORS` — deriving them would put hex
#: parsing and formatting on the repaint path, where a malformed result raises
#: inside ``refresh`` and ``_tick``'s ``finally`` then reschedules the same
#: failure forever.
#: Only the three lamps need one; the off-face states are never drawn.
_UNLIT_COLORS = {
    SessionState.RUNNING: "#1f4623",
    SessionState.NEEDS_INPUT: "#532321",
    SessionState.IDLE: "#534a13",
}

#: The unlit shade for a lamp added since this table was written. Neutral dark,
#: so a new lamp renders as a plain dark circle rather than failing the repaint.
_UNLIT_FALLBACK = "#3e3e3e"

#: Painted over the whole canvas and then keyed out by the window manager, so
#: everything outside the card is a hole in the window. Near-black rather than a
#: loud magenta: where a keyed pixel does survive — the anti-aliased rim of a
#: glyph — it reads as a faint shadow instead of a coloured halo.
_KEY_COLOR = "#010203"

#: The backdrop when colour-keying is unavailable. ``-transparentcolor`` is a
#: Windows-only ``wm`` attribute, so X11 and Aqua get an opaque window; a plain
#: dark rectangle behind the card is the honest fallback.
_OPAQUE_BACKDROP = "#1a1a1a"

#: The widget body, the signal face on top of it, and the outline both share.
#: The charcoal housing is the flat-icon convention (Twemoji ``#31373d``), and
#: its contrast holds up over whatever wallpaper the widget floats above.
_CARD_COLOR = "#22262b"
_HOUSING_COLOR = "#31373d"
_OUTLINE_COLOR = "#000000"

#: Counts sit inside the lamp, so they are read against the lamp's own colour.
#: Black clears WCAG AA on all three lit colours (9.8:1 green, 15.5:1 yellow,
#: 6.1:1 red) where white clears none of them — including red, which is light
#: enough at #ff4136 that the usual white-on-red instinct fails.
_LIT_TEXT_COLOR = "#000000"
_UNLIT_TEXT_COLOR = "#c8c8c8"

#: The two figures under the light, and the close button's own colours.
_TOKENS_TEXT_COLOR = "#f0f0f0"
_RATE_TEXT_COLOR = "#9aa4ad"
_CLOSE_FILL_COLOR = "#3a4048"
_CLOSE_MARK_COLOR = "#e6e6e6"

#: Signal-face geometry, in the proportions the clip-art traffic light icons
#: share: corner radius 0.22, lamp diameter 0.44, lamp pitch 0.61 and end margin
#: 0.28, all of the housing's short side. The short side is deliberately large:
#: the Tk canvas does not antialias, and a bigger circle keeps the
#: stair-stepping a small fraction of the perimeter.
_HOUSING_DEPTH = 80
_HOUSING_RADIUS = round(_HOUSING_DEPTH * 0.22)
_LAMP_RADIUS = round(_HOUSING_DEPTH * 0.22)
_LAMP_PITCH = round(_HOUSING_DEPTH * 0.61)
_LAMP_END_MARGIN = round(_HOUSING_DEPTH * 0.28)
_HOUSING_LENGTH = 2 * _LAMP_END_MARGIN + 2 * _LAMP_RADIUS + (len(_LAMP_ORDER) - 1) * _LAMP_PITCH

#: Card padding, and where each band sits inside it.
_CARD_INSET = 4
_CARD_PAD = 12
_CARD_RADIUS = 14
_CLOSE_RADIUS = 10
#: The close button widens the card on the right, so the left padding matches it:
#: the face, the pips and both figures then share one centre line, which is also
#: the card's.
_HOUSING_LEFT = _CARD_INSET + _CARD_PAD + 2 * _CLOSE_RADIUS
_HOUSING_TOP = _CARD_INSET + 16
_HOUSING_RIGHT = _HOUSING_LEFT + _HOUSING_LENGTH
_HOUSING_BOTTOM = _HOUSING_TOP + _HOUSING_DEPTH
_LAMP_CENTER_Y = _HOUSING_TOP + _HOUSING_DEPTH // 2
_FIRST_LAMP_X = _HOUSING_LEFT + _LAMP_END_MARGIN + _LAMP_RADIUS

_FACE_CENTER_X = (_HOUSING_LEFT + _HOUSING_RIGHT) // 2

#: The two figures, then the close button in the card's top-right corner. They
#: hang off the housing directly: nothing is drawn between the two any more.
_TOKENS_CENTER_Y = _HOUSING_BOTTOM + 18
_RATE_CENTER_Y = _TOKENS_CENTER_Y + 20

_CARD_LEFT, _CARD_TOP = _CARD_INSET, _CARD_INSET
_CARD_RIGHT = _HOUSING_RIGHT + _CARD_PAD + 2 * _CLOSE_RADIUS
_CARD_BOTTOM = _RATE_CENTER_Y + 14
_CLOSE_CENTER_X = _CARD_RIGHT - _CARD_INSET - _CLOSE_RADIUS
_CLOSE_CENTER_Y = _CARD_TOP + _CARD_INSET + _CLOSE_RADIUS

_CANVAS_WIDTH = _CARD_RIGHT + _CARD_INSET
_CANVAS_HEIGHT = _CARD_BOTTOM + _CARD_INSET

#: Canvas items that drag the window, and the ones that close it. The window has
#: no title bar, so these bindings are the only way to move or dismiss it — and
#: they are bound per tag rather than per item because Tk delivers a click to the
#: *topmost* item under the pointer, which over most of the face is a lamp
#: rather than the housing beneath it.
_DRAG_TAG = "drag"
_CLOSE_TAG = "close"


def _rounded_rect_points(x0: int, y0: int, x1: int, y1: int, radius: int) -> list[int]:
    """Polygon points outlining a rounded rectangle.

    Tk has no rounded-rectangle item; the established idiom is a polygon drawn
    with ``smooth=True``. Its quadratic splines pass through the *midpoint* of
    each segment between control points, so repeating the point that starts each
    straight run makes that run collinear and it renders as a straight edge,
    leaving only the single corner points curved.
    """
    return [
        x0 + radius, y0, x1 - radius, y0, x1 - radius, y0,
        x1, y0,
        x1, y0 + radius, x1, y1 - radius, x1, y1 - radius,
        x1, y1,
        x1 - radius, y1, x0 + radius, y1, x0 + radius, y1,
        x0, y1,
        x0, y1 - radius, x0, y0 + radius, x0, y0 + radius,
        x0, y0,
    ]


class TrafficLightApp:
    """Draws the signal face and the token figures, and repaints on a timer."""

    def __init__(self, root: tk.Tk, monitor: Monitor, refresh_ms: int = 2000):
        """Attach the app to ``root`` and poll ``monitor`` every ``refresh_ms``."""
        self._root = root
        self._monitor = monitor
        self._refresh_ms = refresh_ms
        self._after_id: str | None = None
        self._drag_offset: tuple[int, int] | None = None

        root.title("CLI Traffic Light")
        root.overrideredirect(True)
        #: Whether the backdrop is keyed out of the window. Public because it is
        #: the only thing that says which of the two looks the user is getting.
        self.transparent = self._enable_transparency()
        backdrop = _KEY_COLOR if self.transparent else _OPAQUE_BACKDROP
        root.configure(background=backdrop)
        try:
            root.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        # The window manager is not involved any more, so WM_DELETE_WINDOW can
        # never fire: the close button and this key are the only ways out.
        root.bind("<Escape>", lambda _event: self.close())

        self._canvas = tk.Canvas(
            root,
            width=_CANVAS_WIDTH,
            height=_CANVAS_HEIGHT,
            highlightthickness=0,
            background=backdrop,
        )
        self._canvas.pack()
        self._lamps = self._build_face()
        self._tokens_text, self._rate_text = self._build_stats()
        self._build_close_button()
        self._canvas.tag_bind(_DRAG_TAG, "<ButtonPress-1>", self._start_drag)
        self._canvas.tag_bind(_DRAG_TAG, "<B1-Motion>", self._drag)
        self._canvas.tag_bind(_CLOSE_TAG, "<ButtonRelease-1>", lambda _event: self.close())
        self._schedule()

    def refresh(self) -> None:
        """Pull a fresh snapshot, relight the lamps and update the figures."""
        sessions = self._monitor.snapshot()
        self._relight(Counter(session.state for session in sessions))
        totals = self._monitor.totals()
        self._canvas.itemconfig(
            self._tokens_text, text=f"{totals.total_tokens:,} tokens"
        )
        # Summed rather than recomputed off a pooled counter, so a session
        # ending cannot drop a shared cumulative total and fake a negative
        # interval. The terms are addable because they share a divisor — each
        # is tokens over the same trailing window, so the sum is the combined
        # rate across the sessions rather than a mean of unrelated spans.
        rate = sum(session.tokens_per_sec for session in sessions)
        self._canvas.itemconfig(self._rate_text, text=f"{rate:.1f} tok/s")

    def lamps(self) -> dict[SessionState, dict[str, str]]:
        """Each lamp's rendered ``{"fill", "text", "text_fill"}``, off the canvas.

        It reports what is actually drawn rather than what was intended, so a
        test cannot pass on a value the canvas rejected. ``text_fill`` is
        included because the count is only legible while it contrasts with the
        lamp under it, which no other reading would catch.
        """
        return {
            state: {
                "fill": self._canvas.itemcget(oval, "fill"),
                "text": self._canvas.itemcget(text, "text"),
                "text_fill": self._canvas.itemcget(text, "fill"),
            }
            for state, (oval, text) in self._lamps.items()
        }

    def stats(self) -> dict[str, str]:
        """The token figures under the light, as rendered."""
        return {
            "tokens": self._canvas.itemcget(self._tokens_text, "text"),
            "rate": self._canvas.itemcget(self._rate_text, "text"),
        }

    def stop(self) -> None:
        """Cancel every pending ``after()`` callback this app scheduled."""
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None

    def close(self) -> None:
        """Stop the timer and tear the window down."""
        self.stop()
        self._root.destroy()

    def _enable_transparency(self) -> bool:
        """Ask the window manager to key the backdrop out; report whether it did.

        ``-transparentcolor`` is documented as Windows-only, and every other
        platform answers with a ``TclError`` listing the attributes it does
        support. Returning the outcome rather than swallowing it is what lets the
        backdrop colour and the tests follow the platform instead of guessing.
        """
        try:
            self._root.wm_attributes("-transparentcolor", _KEY_COLOR)
        except tk.TclError:
            return False
        return True

    def _build_face(self) -> dict[SessionState, tuple[int, int]]:
        """Draw the card and the signal face; return each state's ``(oval, text)``.

        Every item is created here and only ever reconfigured afterwards. Canvas
        items are not garbage collected, so recreating them on each of the ~30
        repaints a minute would pile up unboundedly behind the visible ones; it
        also keeps the ids stable, which is what lets :meth:`lamps` read them.
        """
        self._canvas.create_polygon(
            _rounded_rect_points(
                _CARD_LEFT, _CARD_TOP, _CARD_RIGHT, _CARD_BOTTOM, _CARD_RADIUS
            ),
            fill=_CARD_COLOR,
            outline=_OUTLINE_COLOR,
            smooth=True,
            tags=_DRAG_TAG,
        )
        self._canvas.create_polygon(
            _rounded_rect_points(
                _HOUSING_LEFT, _HOUSING_TOP, _HOUSING_RIGHT, _HOUSING_BOTTOM,
                _HOUSING_RADIUS,
            ),
            fill=_HOUSING_COLOR,
            outline=_OUTLINE_COLOR,
            smooth=True,
            tags=_DRAG_TAG,
        )

        lamps = {}
        for index, state in enumerate(_LAMP_ORDER):
            center_x = _FIRST_LAMP_X + index * _LAMP_PITCH
            lamps[state] = self._create_lamp(center_x, _LAMP_CENTER_Y, _LAMP_RADIUS)
        # The three lamps answer "what do I have to do?" — nothing, go and
        # reply, or type the next prompt. Every session on the face is one you
        # could act on, whichever CLI it belongs to; :data:`_OFF_FACE_STATES`
        # says which states are deliberately absent and why.
        return lamps

    def _create_lamp(self, center_x: int, center_y: int, radius: int) -> tuple[int, int]:
        """One lamp's oval and its count text, returned as their canvas ids."""
        oval = self._canvas.create_oval(
            center_x - radius, center_y - radius,
            center_x + radius, center_y + radius,
            outline=_OUTLINE_COLOR,
            tags=_DRAG_TAG,
        )
        text = self._canvas.create_text(
            center_x, center_y,
            text="0",
            font=("TkDefaultFont", max(9, radius - 6), "bold"),
            tags=_DRAG_TAG,
        )
        return oval, text

    def _build_stats(self) -> tuple[int, int]:
        """The two figures under the light, as their canvas ids.

        They sit on the card rather than on the keyed backdrop: text is the one
        thing Tk anti-aliases, and an anti-aliased edge over a colour key keeps
        the blended pixels the key does not match, leaving a rim around every
        glyph.
        """
        tokens = self._canvas.create_text(
            _FACE_CENTER_X, _TOKENS_CENTER_Y,
            text="",
            fill=_TOKENS_TEXT_COLOR,
            font=("TkDefaultFont", 12, "bold"),
            tags=_DRAG_TAG,
        )
        rate = self._canvas.create_text(
            _FACE_CENTER_X, _RATE_CENTER_Y,
            text="",
            fill=_RATE_TEXT_COLOR,
            font=("TkDefaultFont", 10),
            tags=_DRAG_TAG,
        )
        return tokens, rate

    def _build_close_button(self) -> None:
        """The ✕ that closes the window, drawn as an opaque, clickable disc.

        Filled deliberately, and in a colour that is not :data:`_KEY_COLOR`: a
        keyed pixel is not merely invisible but click-through, and an unfilled
        oval is hit-tested on its outline alone — either would leave a window
        with no title bar and no way to close it.

        The cross is two lines rather than a ``✕`` glyph for the same reason:
        this is the only affordance the window has, and it must not depend on
        whichever font Tk falls back to on a given desktop.
        """
        self._canvas.create_oval(
            _CLOSE_CENTER_X - _CLOSE_RADIUS, _CLOSE_CENTER_Y - _CLOSE_RADIUS,
            _CLOSE_CENTER_X + _CLOSE_RADIUS, _CLOSE_CENTER_Y + _CLOSE_RADIUS,
            fill=_CLOSE_FILL_COLOR,
            outline=_OUTLINE_COLOR,
            tags=_CLOSE_TAG,
        )
        arm = _CLOSE_RADIUS - 5
        for x_sign in (1, -1):
            self._canvas.create_line(
                _CLOSE_CENTER_X - arm * x_sign, _CLOSE_CENTER_Y - arm,
                _CLOSE_CENTER_X + arm * x_sign, _CLOSE_CENTER_Y + arm,
                fill=_CLOSE_MARK_COLOR,
                width=2,
                tags=_CLOSE_TAG,
            )

    def _relight(self, counts: Counter) -> None:
        """Light each lamp whose state has sessions and dim the rest."""
        for state, (oval, text) in self._lamps.items():
            count = counts[state]
            if count:
                lamp_color, text_color = STATE_COLORS[state], _LIT_TEXT_COLOR
            else:
                lamp_color = _UNLIT_COLORS.get(state, _UNLIT_FALLBACK)
                text_color = _UNLIT_TEXT_COLOR
            self._canvas.itemconfig(oval, fill=lamp_color)
            self._canvas.itemconfig(text, text=str(count), fill=text_color)

    def _start_drag(self, event: tk.Event) -> None:
        """Remember where inside the window the drag began."""
        self._drag_offset = (event.x, event.y)

    def _drag(self, event: tk.Event) -> None:
        """Move the window, keeping the grabbed point under the pointer."""
        if self._drag_offset is None:
            return
        offset_x, offset_y = self._drag_offset
        self._root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

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
