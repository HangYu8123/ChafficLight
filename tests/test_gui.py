"""Frozen acceptance tests for the Tk traffic-light window itself.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows a green light while a session is running, a yellow
light when it needs their input and a red light once it has finished.
"""

import json
import os
import sys
import time
import tkinter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli_traffic_light.claude import _default_proc_info
from cli_traffic_light.gui import (
    _CLOSE_CENTER_X,
    _CLOSE_CENTER_Y,
    _CLOSE_TAG,
    _DRAG_TAG,
    _KEY_COLOR,
    _LAMP_ORDER,
    _OFF_FACE_STATES,
    _OPAQUE_BACKDROP,
    _UNLIT_COLORS,
    TrafficLightApp,
)
from cli_traffic_light.monitor import Monitor
from cli_traffic_light.state import STATE_COLORS, SessionState


def _real_proc_start() -> str:
    """The value Claude records as ``procStart`` for the running process.

    Taken from the production lookup so the fixture cannot drift from the
    platform-specific identity the reader compares it against.
    """
    return str(_default_proc_info(os.getpid())[1])


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _session_path(home: Path) -> Path:
    return home / "sessions" / f"{os.getpid()}.json"


def _write_session_file(home: Path, status: str) -> None:
    now = time.time()
    path = _session_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "sessionId": "gui-sess",
                "cwd": "/work/gui",
                "startedAt": _iso(now - 300),
                "procStart": _real_proc_start(),
                "version": "2.1.220",
                "kind": "cli",
                "entrypoint": "cli",
                "name": "GUI fixture session",
                "status": status,
                "updatedAt": _iso(now - 5),
                "statusUpdatedAt": _iso(now - 5),
                "bridgeSessionId": None,
            }
        ),
        encoding="utf-8",
    )


def _build_homes(tmp_path: Path, monkeypatch) -> Path:
    claude_home = tmp_path / "claude_home"
    codex_home = tmp_path / "codex_home"
    (codex_home / "sessions").mkdir(parents=True)
    _write_session_file(claude_home, "busy")

    now = time.time()
    transcript = claude_home / "projects" / "-work-gui" / "gui-sess.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": False,
                "requestId": "req-gui",
                "timestamp": _iso(now - 30),
                "message": {
                    "id": "msg-gui",
                    "role": "assistant",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 5,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    return claude_home


@pytest.fixture
def tk_root():
    root = tkinter.Tk()
    root.withdraw()
    yield root
    root.destroy()


def _add_finished_session(claude_home: Path) -> None:
    """A transcript with no session file, which the reader reports as FINISHED."""
    transcript = claude_home / "projects" / "-work-done" / "done-sess.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": False,
                "requestId": "req-done",
                "timestamp": _iso(time.time() - 60),
                "message": {
                    "id": "msg-done",
                    "role": "assistant",
                    "usage": {"input_tokens": 7, "output_tokens": 3},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _add_idle_codex_session(codex_home: Path) -> None:
    """A fresh rollout whose last turn event completed, which reads as IDLE.

    Fresh matters: `codex_events_to_state` checks staleness first, so a rollout
    older than 900 s would be FINISHED whatever its events say.
    """
    now = time.time()
    rollout = codex_home / "sessions" / "2026" / "07" / "29" / "rollout-idle.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_text(
        "".join(
            json.dumps(record) + "\n"
            for record in (
                {
                    "timestamp": _iso(now - 60),
                    "type": "session_meta",
                    "payload": {
                        "session_id": "gui-codex",
                        "cwd": "/work/gui-codex",
                        "thread_source": "cli",
                    },
                },
                {
                    "timestamp": _iso(now - 30),
                    "type": "event_msg",
                    "payload": {"type": "task_complete"},
                },
            )
        ),
        encoding="utf-8",
    )


def test_lamp_counts_and_lit_state_follow_the_sessions(tmp_path, monkeypatch, tk_root):
    claude_home = _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        lamps = app.lamps()
        # One busy session: green lamp lit and counting 1, the rest dark at 0.
        assert lamps[SessionState.RUNNING]["text"] == "1"
        assert lamps[SessionState.RUNNING]["fill"] == STATE_COLORS[SessionState.RUNNING]
        assert lamps[SessionState.RUNNING]["text_fill"] == "#000000"
        for state in set(_LAMP_ORDER) - {SessionState.RUNNING}:
            assert lamps[state]["text"] == "0"
            assert lamps[state]["fill"] == _UNLIT_COLORS[state]
            assert lamps[state]["text_fill"] == "#c8c8c8"

        _write_session_file(claude_home, "idle")
        app.refresh()
        tk_root.update_idletasks()
        lamps = app.lamps()
        assert lamps[SessionState.IDLE]["text"] == "1"
        assert lamps[SessionState.IDLE]["fill"] == STATE_COLORS[SessionState.IDLE]
        assert lamps[SessionState.RUNNING]["text"] == "0"
        assert lamps[SessionState.RUNNING]["fill"] == _UNLIT_COLORS[SessionState.RUNNING]
    finally:
        app.stop()


def test_the_face_counts_the_sessions_you_can_act_on_and_no_others(
    tmp_path, monkeypatch, tk_root
):
    """The lamps are a census of what needs you — nothing more, nothing less.

    Two opposite failures hide here, and only comparing totals catches either.
    ``Counter`` answers 0 for a key nobody looks up, so a lamp state dropped from
    the face stops being counted without raising; and an off-face state that
    crept onto a lamp would pad the count with sessions you can do nothing
    about. The fixture keeps a FINISHED session alive throughout and drives the
    live one through a nonsense status (UNKNOWN), so both off-face states are
    present while the assertions run.
    """
    claude_home = _build_homes(tmp_path, monkeypatch)
    _add_finished_session(claude_home)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        for status in ("busy", "idle", "waiting", "nonsense-status"):
            _write_session_file(claude_home, status)
            app.refresh()
            tk_root.update_idletasks()
            sessions = Monitor().snapshot()
            counted = sum(int(lamp["text"]) for lamp in app.lamps().values())
            on_face = [s for s in sessions if s.state in _LAMP_ORDER]
            off_face = [s for s in sessions if s.state in _OFF_FACE_STATES]
            assert off_face, f"{status}: the fixture stopped exercising off-face states"
            assert counted == len(on_face), f"{status}: a lamp state is counted nowhere"
            assert counted < len(sessions), f"{status}: an off-face session reached a lamp"
    finally:
        app.stop()


def test_every_state_is_either_a_lamp_or_deliberately_off_the_face():
    """A state added later must be classified, not silently dropped.

    The counts are read off the lamp mapping and ``Counter`` answers 0 for a key
    nobody looks up, so a state in neither tuple would disappear from the window
    without any error at all. Naming both halves is what makes adding a member to
    :class:`~cli_traffic_light.state.SessionState` a decision rather than an
    accident — the guarantee the derived pip row used to provide.
    """
    assert set(_LAMP_ORDER) | set(_OFF_FACE_STATES) == set(SessionState)
    assert not set(_LAMP_ORDER) & set(_OFF_FACE_STATES)


def test_the_housing_carries_the_three_signal_states_in_signal_order():
    """Only the states the user must act on, in road-signal order.

    ``_LAMP_ORDER`` is what puts a state on the face at all, so this is where a
    state silently losing or gaining a lamp shows up. The colours those three
    states carry are pinned in ``test_state.py``; repeating them here would put
    the same literals in a third place.
    """
    assert _LAMP_ORDER == (
        SessionState.NEEDS_INPUT,
        SessionState.IDLE,
        SessionState.RUNNING,
    )


def test_each_unlit_shade_is_a_dark_version_of_its_own_lamp():
    """A rotated colour must take its dark shade with it.

    Spelled out as literals for the same reason the lit colours are: the lamp
    test compares each rendered fill against ``_UNLIT_COLORS`` itself, so it
    reads the table it is checking and a shade left paired with a state's *old*
    hue — a lamp that changes colour when it goes dark — would pass it.
    """
    assert _UNLIT_COLORS == {
        SessionState.RUNNING: "#1f4623",
        SessionState.NEEDS_INPUT: "#532321",
        SessionState.IDLE: "#534a13",
    }


def test_two_states_light_two_housing_lamps_at_once(tmp_path, monkeypatch, tk_root):
    """Unlike a road signal, several lamps are lit together — the counts are a census.

    The pair is deliberately one Claude session and one Codex one, so this also
    covers the two CLIs sharing a single face rather than each driving its own.
    """
    claude_home = _build_homes(tmp_path, monkeypatch)
    _write_session_file(claude_home, "waiting")
    _add_idle_codex_session(tmp_path / "codex_home")
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        lamps = app.lamps()
        assert lamps[SessionState.NEEDS_INPUT]["text"] == "1"
        assert lamps[SessionState.IDLE]["text"] == "1"
        lit = [state for state in _LAMP_ORDER if lamps[state]["text"] != "0"]
        assert lit == [SessionState.NEEDS_INPUT, SessionState.IDLE]
        assert lamps[SessionState.NEEDS_INPUT]["fill"] == STATE_COLORS[SessionState.NEEDS_INPUT]
        assert lamps[SessionState.IDLE]["fill"] == STATE_COLORS[SessionState.IDLE]
    finally:
        app.stop()


def test_token_total_and_rate_are_shown_under_the_light(tmp_path, monkeypatch, tk_root):
    claude_home = _build_homes(tmp_path, monkeypatch)
    # A second billed record 10 s after the first, so the rate is a real
    # non-zero number: asserting only 0.0 would pass just as well if the rate
    # were hardcoded or the sum dropped a term.
    now = time.time()
    transcript = claude_home / "projects" / "-work-gui" / "gui-sess.jsonl"
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": False,
                    "requestId": "req-gui-2",
                    "timestamp": _iso(now - 20),
                    "message": {
                        "id": "msg-gui-2",
                        "role": "assistant",
                        "usage": {"input_tokens": 80, "output_tokens": 20},
                    },
                }
            )
            + "\n"
        )
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        stats = app.stats()
        # 100 + 20 + 5 billed first, then 80 + 20. Only the second lot is a
        # measurable step, and it is spread over the whole 30 s since the first
        # record — the 10 s between the two plus the 20 s of silence since.
        assert stats["tokens"] == "225 tokens"
        assert stats["rate"] == "3.3 tok/s"
    finally:
        app.stop()


def test_repainting_does_not_accumulate_canvas_items(tmp_path, monkeypatch, tk_root):
    """Canvas items are not garbage collected, so the light must reuse them.

    Reaches for the canvas directly because item count is exactly what a public
    accessor would hide, and unbounded growth here is invisible until the window
    has been open for hours.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        after_first = len(app._canvas.find_all())
        for _ in range(5):
            app.refresh()
        tk_root.update_idletasks()
        assert len(app._canvas.find_all()) == after_first
    finally:
        app.stop()


def _lamp_center(app: TrafficLightApp, state: SessionState) -> tuple[float, float]:
    """Where a lamp actually landed, read back off the canvas.

    Reaches for the item ids because the geometry is the thing under test: a
    reading taken from the module constants would agree with itself no matter
    what was drawn.
    """
    x0, y0, x1, y1 = app._canvas.coords(app._lamps[state][0])
    return (x0 + x1) / 2, (y0 + y1) / 2


def test_the_signal_face_is_horizontal(tmp_path, monkeypatch, tk_root):
    """Red, then yellow, then green, left to right on one axis."""
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        centers = [_lamp_center(app, state) for state in _LAMP_ORDER]
        xs = [x for x, _y in centers]
        ys = [y for _x, y in centers]
        assert xs == sorted(xs) and len(set(xs)) == len(xs)
        assert len(set(ys)) == 1, "the lamps are not on one horizontal axis"
    finally:
        app.stop()


def test_the_close_button_is_opaque_and_on_top(tmp_path, monkeypatch, tk_root):
    """A keyed pixel is click-through, so the ✕ must be filled and topmost.

    An unfilled oval is hit-tested on its outline alone and a key-coloured one
    is clicked straight through on Windows; either would leave a window with no
    title bar and no way to close it.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        close_items = set(app._canvas.find_withtag(_CLOSE_TAG))
        assert close_items
        fills = {app._canvas.itemcget(item, "fill") for item in close_items}
        assert _KEY_COLOR not in fills
        assert "" not in fills
        assert "<ButtonRelease-1>" in app._canvas.tag_bind(_CLOSE_TAG)

        x, y = _CLOSE_CENTER_X, _CLOSE_CENTER_Y
        under_the_pointer = app._canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        # Tk delivers a click to the topmost item, which must not be the card
        # drawn underneath the button.
        assert under_the_pointer[-1] in close_items
    finally:
        app.stop()


def test_the_backdrop_follows_whether_the_key_colour_was_accepted(
    tmp_path, monkeypatch, tk_root
):
    """``-transparentcolor`` is Windows-only; both outcomes must be coherent.

    Asserting the *coupling* rather than either colour keeps this meaningful on
    every platform, so nothing has to be skipped.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        expected = _KEY_COLOR if app.transparent else _OPAQUE_BACKDROP
        assert app._canvas.cget("background") == expected
        assert tk_root.cget("background") == expected
        if sys.platform == "win32":
            assert app.transparent
    finally:
        app.stop()


class _StubEvent:
    """The two fields the drag handlers read, without a real pointer."""

    def __init__(self, x: int, y: int, x_root: int, y_root: int):
        self.x, self.y = x, y
        self.x_root, self.y_root = x_root, y_root


def test_dragging_the_light_moves_the_window(tmp_path, monkeypatch, tk_root):
    """The window has no title bar, so this is the only way to move it.

    Drives the handlers rather than synthesising a click: a withdrawn window has
    no pointer over it, and what matters is that the grabbed point stays under
    the cursor rather than the window jumping to it.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        # Tcl reports the bindings in its own normalised spelling.
        assert "<Button-1>" in app._canvas.tag_bind(_DRAG_TAG)
        assert "<B1-Motion>" in app._canvas.tag_bind(_DRAG_TAG)
        tk_root.geometry("+300+200")
        tk_root.update_idletasks()
        # Grabbed 40 px in and 30 px down, then the pointer moves to (500, 400).
        app._start_drag(_StubEvent(40, 30, 340, 230))
        app._drag(_StubEvent(60, 50, 500, 400))
        tk_root.update_idletasks()
        assert tk_root.geometry().endswith("+460+370")
    finally:
        app.stop()


def test_escape_is_bound_on_the_root(tmp_path, monkeypatch, tk_root):
    """The second way out, since no window manager will send WM_DELETE_WINDOW."""
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        assert "<Key-Escape>" in tk_root.bind()
    finally:
        app.stop()


def test_close_stops_the_timer_and_destroys_the_window(tmp_path, monkeypatch):
    """Its own root: the shared fixture would destroy an already-dead one."""
    _build_homes(tmp_path, monkeypatch)
    root = tkinter.Tk()
    root.withdraw()
    app = TrafficLightApp(root, Monitor(), refresh_ms=50_000)
    app.refresh()
    app.close()
    assert app._after_id is None
    with pytest.raises(tkinter.TclError):
        root.winfo_exists()


def test_stop_cancels_every_pending_after_callback(tmp_path, monkeypatch, tk_root):
    _build_homes(tmp_path, monkeypatch)
    before = set(tk_root.tk.splitlist(tk_root.tk.call("after", "info")))
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        during = set(tk_root.tk.splitlist(tk_root.tk.call("after", "info")))
        assert during - before != set()
    finally:
        app.stop()
    remaining = set(tk_root.tk.splitlist(tk_root.tk.call("after", "info")))
    assert remaining - before == set()
