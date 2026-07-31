"""Frozen acceptance tests for the Tk traffic-light window itself.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows a green light while a session is running, a yellow
light when it needs their input and a red light once it has finished.
"""

import colorsys
import json
import math
import os
import sys
import time
import tkinter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli_traffic_light.claude import _default_proc_info
from cli_traffic_light.gui import (
    _CLOSE_DRAG_SLOP,
    _CLOSE_FILL_COLOR,
    _CLOSE_TAG,
    _GWL_EXSTYLE,
    _WS_EX_TRANSPARENT,
    _DRAG_TAG,
    _KEY_COLOR,
    _LAMP_ORDER,
    _LIT_TEXT_COLOR,
    _OFF_FACE_STATES,
    _OPAQUE_BACKDROP,
    _MINIMIZE_TAG,
    _RESTORE_TAG,
    _UNLIT_COLORS,
    _UNLIT_TEXT_COLOR,
    _BarLayout,
    _Layout,
    TrafficLightApp,
    _hex_to_rgb,
    _photo_pixel,
    enable_hidpi,
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
    the same literals in a third place — but the *seats* belong here, because
    which state sits where has to follow whichever colour it carries. A state
    that swapped colour without swapping seat would leave the housing reading
    yellow-red-green, which is not a traffic light and which nothing else
    catches: ``_LAMP_ORDER`` alone cannot say what colour it is putting there.
    """
    assert _LAMP_ORDER == (
        SessionState.IDLE,
        SessionState.NEEDS_INPUT,
        SessionState.RUNNING,
    )
    # Red before yellow before green, checked as hue rather than as three more
    # copies of the hex: hue *is* what "red before yellow before green" means,
    # and it stays true if a lamp's exact shade is ever retuned.
    hues = [colorsys.rgb_to_hsv(*_hex_to_rgb(STATE_COLORS[s]))[0] for s in _LAMP_ORDER]
    assert hues == sorted(hues), "the lamps are not in red-yellow-green order"


def test_each_unlit_shade_is_a_dark_version_of_its_own_lamp():
    """A rotated colour must take its dark shade with it.

    Spelled out as literals for the same reason the lit colours are: the lamp
    test compares each rendered fill against ``_UNLIT_COLORS`` itself, so it
    reads the table it is checking and a shade left paired with a state's *old*
    hue — a lamp that changes colour when it goes dark — would pass it.
    """
    assert _UNLIT_COLORS == {
        SessionState.RUNNING: "#1f4623",
        SessionState.NEEDS_INPUT: "#534a13",
        SessionState.IDLE: "#532f2c",
    }


def _contrast(first: str, second: str) -> float:
    """WCAG 2.x contrast ratio between two ``"#rrggbb"`` colours."""

    def luminance(color: str) -> float:
        channels = (part / 255 for part in _hex_to_rgb(color))
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_every_count_is_legible_on_the_lamp_it_sits_in():
    """One fixed text colour per half, so a retuned lamp can hide its own count.

    The counts are drawn *inside* the lamps, and both text colours are single
    constants shared by all three — so nothing about changing one lamp's shade
    forces a second look at the digit on top of it. Red is the live case: it was
    toned down because it shouted, and the AA floor is what says how much
    further it can go before the count stops being readable rather than before
    it stops looking good. 4.5:1 is AA for text this size.
    """
    for state in _LAMP_ORDER:
        assert _contrast(_LIT_TEXT_COLOR, STATE_COLORS[state]) >= 4.5, state
        assert _contrast(_UNLIT_TEXT_COLOR, _UNLIT_COLORS[state]) >= 4.5, state


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
        assert lit == [SessionState.IDLE, SessionState.NEEDS_INPUT]
        assert lamps[SessionState.NEEDS_INPUT]["fill"] == STATE_COLORS[SessionState.NEEDS_INPUT]
        assert lamps[SessionState.IDLE]["fill"] == STATE_COLORS[SessionState.IDLE]
    finally:
        app.stop()


def test_the_yellow_lamp_flashes_while_it_has_sessions(tmp_path, monkeypatch, tk_root):
    """Yellow is the blocking state, so it moves; nothing else on the face does.

    The blink is driven by calling the timer callback rather than by waiting
    ``_FLASH_MS`` of real time, so this asserts the alternation itself instead of
    racing it. What must hold at every step is that the lamp still reads as
    yellow or as its own dark shade — never as another lamp's colour — and that
    its count stays put and stays legible against whichever shade is showing.

    A *lit* red session runs alongside it throughout, because "only yellow
    flashes" is the requirement and a red lamp that happened to be dark anyway
    would not be evidence of it: the lamp that must hold still is the one that
    is on.
    """
    claude_home = _build_homes(tmp_path, monkeypatch)
    _write_session_file(claude_home, "waiting")
    _add_idle_codex_session(tmp_path / "codex_home")
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        # A lamp that has just come on starts lit: the light must not spend the
        # first half-blink dark while a session is already waiting.
        yellow = app.lamps()[SessionState.NEEDS_INPUT]
        assert yellow["fill"] == STATE_COLORS[SessionState.NEEDS_INPUT]
        assert yellow["text"] == "1" and yellow["text_fill"] == "#000000"

        seen = []
        for _ in range(4):
            app._flash_tick()
            tk_root.update_idletasks()
            lamps = app.lamps()
            seen.append(lamps[SessionState.NEEDS_INPUT]["fill"])
            # The count is what the lamp is for; it stays through both halves,
            # in whichever colour is readable on the shade underneath it.
            assert lamps[SessionState.NEEDS_INPUT]["text"] == "1"
            assert lamps[SessionState.NEEDS_INPUT]["text_fill"] == (
                "#000000"
                if seen[-1] == STATE_COLORS[SessionState.NEEDS_INPUT]
                else "#c8c8c8"
            )
            # Only yellow flashes — red, the state it swapped with, is lit here
            # and stays lit. Two lamps blinking would be two things competing
            # for the same glance, which is the whole point of flashing one.
            assert lamps[SessionState.IDLE]["fill"] == STATE_COLORS[SessionState.IDLE]
            assert lamps[SessionState.IDLE]["text"] == "1"
            assert lamps[SessionState.RUNNING]["fill"] == _UNLIT_COLORS[
                SessionState.RUNNING
            ]
        assert seen == [
            _UNLIT_COLORS[SessionState.NEEDS_INPUT],
            STATE_COLORS[SessionState.NEEDS_INPUT],
            _UNLIT_COLORS[SessionState.NEEDS_INPUT],
            STATE_COLORS[SessionState.NEEDS_INPUT],
        ]

        # A refresh mid-blink must not fight the blink: the lamp is dark here and
        # stays dark until the flash timer says otherwise.
        app._flash_tick()
        app.refresh()
        tk_root.update_idletasks()
        assert app.lamps()[SessionState.NEEDS_INPUT]["fill"] == _UNLIT_COLORS[
            SessionState.NEEDS_INPUT
        ]
    finally:
        app.stop()


def test_the_flash_stops_and_leaves_the_lamp_dark_when_nothing_is_waiting(
    tmp_path, monkeypatch, tk_root
):
    """The blink is a state, so it must end when the state does.

    Two failures live here and neither is visible on a screenshot: a timer left
    running forever on a face with nothing waiting, and a lamp stranded on the
    lit half of a blink that stopped — a yellow light that means nothing.
    """
    claude_home = _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        # Nothing waiting: the blink timer never starts at all.
        assert app._flash_after_id is None

        _write_session_file(claude_home, "waiting")
        app.refresh()
        assert app._flash_after_id is not None

        # The question was answered, and the flash goes with it.
        app._flash_tick()  # leave the blink on its dark half...
        _write_session_file(claude_home, "busy")
        app.refresh()
        tk_root.update_idletasks()
        assert app._flash_after_id is None
        lamps = app.lamps()
        assert lamps[SessionState.NEEDS_INPUT]["text"] == "0"
        assert lamps[SessionState.NEEDS_INPUT]["fill"] == _UNLIT_COLORS[
            SessionState.NEEDS_INPUT
        ]
        # ...and the next session that waits still opens lit, rather than
        # inheriting the half the last one stopped on.
        _write_session_file(claude_home, "waiting")
        app.refresh()
        tk_root.update_idletasks()
        assert app.lamps()[SessionState.NEEDS_INPUT]["fill"] == STATE_COLORS[
            SessionState.NEEDS_INPUT
        ]
    finally:
        app.stop()


def test_token_total_and_rate_are_shown_under_the_light(tmp_path, monkeypatch, tk_root):
    """The figure opens at zero and then counts what this window watched.

    The fixture already has 125 billed tokens on disk before the app is built,
    which is exactly the history the reset exists to leave out: both CLIs count
    cumulatively and keep a transcript for a day, so the face would otherwise
    open at whatever yesterday came to.
    """
    claude_home = _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        assert app.stats()["tokens"] == "0 tokens"

        # A second billed record 10 s after the first, so the rate is a real
        # non-zero number too: asserting only 0.0 would pass just as well if the
        # rate were hardcoded or the sum dropped a term.
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
        app.refresh()
        tk_root.update_idletasks()
        stats = app.stats()
        # Only the 80 + 20 that arrived while the window was open, not the
        # 100 + 20 + 5 that was already there.
        assert stats["tokens"] == "100 tokens"
        # The rate is unaffected by the reset: it has always been the newest
        # step between two readings, and that step is spread across the whole
        # 30 s since the record it is measured from — the 10 s between the two
        # plus the 20 s of silence since.
        assert stats["rate"] == "3.3 tok/s"
    finally:
        app.stop()


def _append_billed_record(transcript: Path, tag: str, stamp: float) -> None:
    """One more billed assistant record, so a transcript has a rate at all.

    A rate needs two samples that gained tokens; a fixture with one record
    reports 0.0 whatever its state, which would let a filtering test pass
    without filtering anything.
    """
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": False,
                    "requestId": f"req-{tag}",
                    "timestamp": _iso(stamp),
                    "message": {
                        "id": f"msg-{tag}",
                        "role": "assistant",
                        "usage": {"input_tokens": 400, "output_tokens": 100},
                    },
                }
            )
            + "\n"
        )


def test_a_dark_green_lamp_means_zero_tokens_per_second(tmp_path, monkeypatch, tk_root):
    """Only a turn in flight can bill tokens, so only RUNNING may add to the rate.

    A rate ages with the silence after its last billed record rather than
    stopping dead, so every *other* state carries the decaying tail of a burst
    that is already over — a session for the minute after it finishes, an idle
    one for the minute after its turn does. Summing those printed a speed under
    three lamps reading zero, which is the reported "tok/s is non-zero when no
    session is running". The two halves of the face now agree by construction.
    """
    claude_home = _build_homes(tmp_path, monkeypatch)
    now = time.time()
    # An idle session and a finished one, both with a burst recent enough that
    # each really does report a rate of its own.
    live = claude_home / "projects" / "-work-gui" / "gui-sess.jsonl"
    _append_billed_record(live, "gui-2", now - 10)
    gone = claude_home / "projects" / "-work-gone" / "gone-sess.jsonl"
    _append_billed_record(gone, "gone-1", now - 20)
    _append_billed_record(gone, "gone-2", now - 10)
    _write_session_file(claude_home, "idle")

    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()

        # Non-vacuity: both fixtures really are non-RUNNING sessions really
        # reporting a rate, so the assertion below is about the filtering and
        # not about there being nothing to filter.
        by_id = {s.session_id: s for s in Monitor().snapshot()}
        assert by_id["gui-sess"].state is SessionState.IDLE
        assert by_id["gone-sess"].state in _OFF_FACE_STATES
        assert by_id["gui-sess"].tokens_per_sec > 0
        assert by_id["gone-sess"].tokens_per_sec > 0

        assert app.lamps()[SessionState.RUNNING]["text"] == "0"
        assert app.stats()["rate"] == "0.0 tok/s"

        # And the filter is not simply a hardcoded zero: light the green lamp
        # on the same transcripts and the figure comes back.
        _write_session_file(claude_home, "busy")
        app.refresh()
        tk_root.update_idletasks()
        assert app.lamps()[SessionState.RUNNING]["text"] == "1"
        assert app.stats()["rate"] != "0.0 tok/s"
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

    Taken from the drawn extent rather than from the layout, because the
    geometry is the thing under test: a reading computed from the same numbers
    the app drew with would agree with itself no matter what appeared.
    """
    x0, y0, x1, y1 = app._canvas.bbox(app._lamps[state][0])
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
    """A keyed pixel is click-through, so the ✕ must be painted and topmost.

    Read out of the image the canvas is actually showing rather than off the
    colour it was asked for: a button rendered onto the keyed backdrop would be
    clicked straight through on Windows, and that only shows in the pixels.
    Every pixel across the middle of the disc is checked, because one keyed
    patch inside it is a hole in the only affordance the window has.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        close_items = set(app._canvas.find_withtag(_CLOSE_TAG))
        assert len(close_items) == 1
        button = next(iter(close_items))
        photo = app._images[app._canvas.itemcget(button, "image")]
        middle = app._layout.close_half
        reach = app._layout.close_radius // 2  # a square well inside the disc
        painted = [
            _hex_to_rgb(_photo_pixel(photo, middle + dx, middle + dy))
            for dx in range(-reach, reach + 1)
            for dy in range(-reach, reach + 1)
        ]
        assert _hex_to_rgb(_KEY_COLOR) not in painted
        # Nor a shade a pixel or two off it, which dodges the exact comparison
        # while still being an invisible button on a keyed-out backdrop. Every
        # one of these is either the disc or the ✕ drawn on it, and the disc is
        # the darkest of them.
        assert min(sum(pixel) for pixel in painted) > sum(_hex_to_rgb(_CLOSE_FILL_COLOR)) / 2
        assert "<ButtonRelease-1>" in app._canvas.tag_bind(_CLOSE_TAG)

        x, y = app._layout.close_center_x, app._layout.close_center_y
        under_the_pointer = app._canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        # Tk delivers a click to the topmost item, which must not be the housing
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


def test_a_denser_display_is_drawn_in_more_pixels_not_larger_ones():
    """The whole point of the scale factor: same design, more pixels.

    Every length has to move together — a lamp that scaled while the housing did
    not would simply not fit — so this compares the ratios rather than any one
    number, and includes the fonts, which are what the housing is sized around.
    """
    single, double = _Layout(1.0), _Layout(2.0)
    for name in (
        "width",
        "height",
        "lamp_radius",
        "lamp_pitch",
        "housing_width",
        "housing_height",
        "close_radius",
        "count_font_px",
        "tokens_font_px",
    ):
        assert getattr(double, name) == pytest.approx(
            2 * getattr(single, name), abs=2
        ), name
    # And the design itself is unchanged at 1.0: a lamp still sits inside its own
    # tile, and the tiles still tile — neighbours must not overlap and clip each
    # other's glow.
    assert single.tile_half <= single.lamp_pitch / 2
    assert single.lamp_radius < single.tile_half


def test_the_close_button_clears_the_lamp_beside_it_and_the_corner_above_it():
    """It is tucked into a corner, between two things it must not touch.

    Both failures are silent and only visible on screen: overlapping the last
    lamp hides part of the count, and straying outside the rounded corner puts
    half the button on the keyed backdrop, where it is see-through.
    """
    layout = _Layout(1.0)
    last = len(_LAMP_ORDER) - 1
    from_lamp = math.dist(
        (layout.close_center_x, layout.close_center_y),
        (layout.lamp_center_x(last), layout.lamp_center_y),
    )
    assert from_lamp >= layout.lamp_radius + layout.close_radius

    # The corner is an arc of `housing_radius` centred this far in from it; the
    # button is inside the housing only while it stays inside that arc.
    _left, _top, right, _bottom = layout.housing_box()
    corner = (right - layout.housing_radius, layout.pad + layout.housing_radius)
    from_corner = math.dist((layout.close_center_x, layout.close_center_y), corner)
    assert from_corner + layout.close_radius <= layout.housing_radius


def test_hidpi_awareness_is_only_claimed_where_it_exists():
    """Windows hands out a stretched 96 dpi space until a process opts out.

    Nowhere else needs it, and claiming it must not raise on a platform with no
    such notion — the call sits on the path that opens the window.
    """
    taken = enable_hidpi()
    assert isinstance(taken, bool)
    if sys.platform != "win32":
        assert taken is False


@pytest.mark.parametrize("forced_scale", [None, 1.5, 2.0])
def test_the_window_is_scaled_to_the_display_it_opened_on(
    tmp_path, monkeypatch, tk_root, forced_scale
):
    """The canvas must be exactly the size the layout was computed for.

    A mismatch is how a HiDPI rewrite goes wrong in practice: the drawing scales
    and the widget it sits in does not, so the window silently clips it. The
    denser scales are forced rather than waited for, because the machine this
    runs on is almost always a plain 96 dpi one and the path would otherwise
    never be executed at all.
    """
    _build_homes(tmp_path, monkeypatch)
    if forced_scale is not None:
        monkeypatch.setattr(
            TrafficLightApp, "_display_scale", lambda _self: forced_scale
        )
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        if forced_scale is not None:
            assert app._layout.scale == forced_scale
        assert app._layout.scale >= 1.0
        assert int(app._canvas.cget("width")) == app._layout.width
        assert int(app._canvas.cget("height")) == app._layout.height
        # Nothing may be drawn outside the canvas, which is the window.
        x0, y0, x1, y1 = app._canvas.bbox("all")
        assert x0 >= 0 and y0 >= 0
        assert x1 <= app._layout.width and y1 <= app._layout.height
        # And the lamp colours still read back exactly, so the pixel the count
        # is judged against is the state's own colour at every density.
        lamps = app.lamps()
        for state in _LAMP_ORDER:
            expected = (
                STATE_COLORS[state]
                if lamps[state]["text"] != "0"
                else _UNLIT_COLORS[state]
            )
            assert lamps[state]["fill"] == expected, state
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


def test_only_the_close_button_is_solid(tmp_path, monkeypatch, tk_root):
    """The whole widget passes clicks on except the ✕, which must not.

    The region tested is the ✕'s canvas *item*, not the disc drawn inside it: a
    canvas dispatches a click to any item whose rectangle covers the pointer, so
    a smaller region would leave a ring that closes the window when clicked and
    is nonetheless passed through to whatever is underneath.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        layout = app._layout
        assert app._pointer_over_close(layout.close_center_x, layout.close_center_y)
        # The corners of the item are still the item.
        for corner_x in (-layout.close_half, layout.close_half):
            for corner_y in (-layout.close_half, layout.close_half):
                assert app._pointer_over_close(
                    layout.close_center_x + corner_x, layout.close_center_y + corner_y
                )
        # Everything the light is actually made of is not.
        for index in range(len(_LAMP_ORDER)):
            assert not app._pointer_over_close(
                layout.lamp_center_x(index), layout.lamp_center_y
            )
        for figure_y in (layout.tokens_center_y, layout.rate_center_y):
            assert not app._pointer_over_close(layout.center_x, figure_y)
        assert not app._pointer_over_close(
            layout.close_center_x - layout.close_half - 1, layout.close_center_y
        )
        assert not app._pointer_over_close(0, 0)
    finally:
        app.stop()


def test_click_through_is_only_claimed_on_a_window_that_can_do_it(
    tmp_path, monkeypatch, tk_root
):
    """Passing clicks on is a property of a *layered* window, not of a platform.

    Microsoft documents `WS_EX_TRANSPARENT` as overriding the hit-testing of a
    layered window, and layered is what `-transparentcolor` made it — so the
    honest coupling is with `transparent`, and asserting that keeps this
    meaningful everywhere instead of skipping off Windows.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        if app.click_through:
            assert app.transparent
        if sys.platform != "win32":
            assert app.click_through is False
        else:
            # Where the backdrop test already requires the key to be accepted,
            # the window is layered and this must follow — otherwise the suite
            # would pass with the feature silently switched off.
            assert app.click_through
    finally:
        app.stop()


def test_the_window_takes_clicks_only_while_the_pointer_is_on_the_close_button(
    tmp_path, monkeypatch, tk_root
):
    """Read back off the window itself: what the OS will do with the next click.

    The style bit is the whole mechanism, so this asserts the bit rather than
    the bookkeeping around it. Guarded on the capability rather than skipped,
    like the backdrop test above it.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        if not app.click_through:
            return
        # Tk builds the window that carries the style lazily, so until this the
        # app has nothing to set the bit on — and neither would a reader here.
        tk_root.update_idletasks()
        import ctypes

        get_style = getattr(
            ctypes.windll.user32, "GetWindowLongPtrW", ctypes.windll.user32.GetWindowLongW
        )
        get_style.restype = ctypes.c_ssize_t
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]

        def passes_clicks_on() -> bool:
            assert app._click_through_handle, "no layered window was ever found"
            style = get_style(app._click_through_handle, _GWL_EXSTYLE)
            return bool(style & _WS_EX_TRANSPARENT)

        layout = app._layout
        for offset, expected in (
            ((layout.close_center_x, layout.close_center_y), False),
            ((layout.lamp_center_x(0), layout.lamp_center_y), True),
        ):
            monkeypatch.setattr(
                tk_root,
                "winfo_pointerxy",
                lambda offset=offset: (
                    tk_root.winfo_rootx() + offset[0],
                    tk_root.winfo_rooty() + offset[1],
                ),
            )
            app._click_through_tick()
            assert passes_clicks_on() is expected, offset
    finally:
        app.stop()


def test_a_drag_that_started_on_the_close_button_moves_instead_of_closing(
    tmp_path, monkeypatch, tk_root
):
    """The ✕ is the only solid part left, so it has to serve both gestures.

    Its own root would be destroyed by a close, which is exactly what must not
    happen here — the shared fixture is safe precisely because this asserts the
    window survives.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        assert "<Button-1>" in app._canvas.tag_bind(_CLOSE_TAG)
        assert "<B1-Motion>" in app._canvas.tag_bind(_CLOSE_TAG)
        tk_root.geometry("+300+200")
        tk_root.update_idletasks()
        app._start_drag(_StubEvent(10, 10, 310, 210))
        app._drag(_StubEvent(10, 10, 400, 300))
        app._release_close(_StubEvent(10, 10, 400, 300))
        tk_root.update_idletasks()
        assert tk_root.winfo_exists()
        assert tk_root.geometry().endswith("+390+290")
        # And the gesture is over, so the widget may pass clicks on again.
        assert app._drag_offset is None
    finally:
        app.stop()


def test_a_press_that_does_not_move_still_closes_the_window(tmp_path, monkeypatch):
    """Its own root: the shared fixture would destroy an already-dead one.

    A hand is never perfectly still, so a click is only distinguishable from a
    drag by a tolerance — which means the tolerance itself has to be a click.
    """
    _build_homes(tmp_path, monkeypatch)
    root = tkinter.Tk()
    root.withdraw()
    app = TrafficLightApp(root, Monitor(), refresh_ms=50_000)
    app._start_drag(_StubEvent(10, 10, 310, 210))
    app._release_close(_StubEvent(10, 10, 310 + _CLOSE_DRAG_SLOP, 210))
    with pytest.raises(tkinter.TclError):
        root.winfo_exists()


def test_a_finished_gesture_lets_the_widget_pass_clicks_on_again(
    tmp_path, monkeypatch, tk_root
):
    """A press with no release would hold the window solid for good.

    `_click_through_tick` leaves the style alone mid-drag, so the release is the
    only thing that can end that — and nothing else in the app clears it.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app._start_drag(_StubEvent(40, 30, 340, 230))
        assert app._drag_offset is not None
        app._end_drag(_StubEvent(40, 30, 340, 230))
        assert app._drag_offset is None
        assert app._drag_origin is None
    finally:
        app.stop()


def test_the_minimize_button_shrinks_the_widget_to_the_bar(
    tmp_path, monkeypatch, tk_root
):
    """The ─ swaps which of the two sizes is on screen, and the window follows.

    Two separate things have to be true and only one of them is the packing.
    Tk documents a toplevel whose geometry has been set — which `_drag` does on
    every move — as no longer following the size its children ask for, so the
    size is *also* set outright; a bar drawn inside a face-sized window is a
    widget that never got smaller. The size actually granted cannot be read back
    here (a withdrawn window reports whatever it last mapped at), so what is
    asserted is the pair either side of that: the size the window now asks for,
    and the size the app demanded.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        assert app.minimized is False
        bar, face = app._bar_layout, app._layout
        assert (bar.width, bar.height) < (face.width, face.height)

        asked: list[str] = []
        granted = tk_root.geometry

        def spy(spec=None):
            if spec is None:
                return granted()
            asked.append(spec)
            return granted(spec)

        monkeypatch.setattr(tk_root, "geometry", spy)

        app.minimize()
        tk_root.update_idletasks()
        assert app.minimized is True
        assert app._bar_canvas.winfo_manager() == "pack"
        assert app._canvas.winfo_manager() == ""
        assert (tk_root.winfo_reqwidth(), tk_root.winfo_reqheight()) == (
            bar.width,
            bar.height,
        )
        assert asked[-1].startswith(f"{bar.width}x{bar.height}+")

        app.restore()
        tk_root.update_idletasks()
        assert app.minimized is False
        assert app._canvas.winfo_manager() == "pack"
        assert app._bar_canvas.winfo_manager() == ""
        assert (tk_root.winfo_reqwidth(), tk_root.winfo_reqheight()) == (
            face.width,
            face.height,
        )
        assert asked[-1].startswith(f"{face.width}x{face.height}+")
    finally:
        app.stop()


def test_the_minimize_button_is_opaque_and_on_top(tmp_path, monkeypatch, tk_root):
    """Same two ways to draw an unclickable button as the ✕ has.

    A keyed pixel is click-through rather than merely invisible, and a canvas
    hands a click to the topmost item — and the ─ sits in the corner above the
    red lamp, whose own tile reaches under it. Either mistake leaves a button
    that is drawn, bound and dead.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        items = set(app._canvas.find_withtag(_MINIMIZE_TAG))
        assert len(items) == 1
        photo = app._images[app._canvas.itemcget(next(iter(items)), "image")]
        middle = app._layout.close_half
        reach = app._layout.close_radius // 2
        painted = [
            _hex_to_rgb(_photo_pixel(photo, middle + dx, middle + dy))
            for dx in range(-reach, reach + 1)
            for dy in range(-reach, reach + 1)
        ]
        assert _hex_to_rgb(_KEY_COLOR) not in painted
        assert min(sum(pixel) for pixel in painted) > sum(_hex_to_rgb(_CLOSE_FILL_COLOR)) / 2

        x, y = app._layout.minimize_center_x, app._layout.minimize_center_y
        under_the_pointer = app._canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        assert under_the_pointer[-1] in items
        assert "<ButtonRelease-1>" in app._canvas.tag_bind(_MINIMIZE_TAG)
    finally:
        app.stop()


def test_the_minimize_button_clears_the_lamp_beside_it_and_the_corner_above_it():
    """The mirror of the ✕, and it has the same two ways to be misplaced.

    Overlapping the first lamp hides part of a count; straying outside the
    rounded corner puts half the button on the keyed backdrop, where it is
    see-through. Checked on the layout rather than on a window, so it holds at
    every display scale the arithmetic can produce.
    """
    layout = _Layout(1.0)
    from_lamp = math.dist(
        (layout.minimize_center_x, layout.minimize_center_y),
        (layout.lamp_center_x(0), layout.lamp_center_y),
    )
    assert from_lamp >= layout.lamp_radius + layout.close_radius

    left, _top, _right, _bottom = layout.housing_box()
    corner = (left + layout.housing_radius, layout.pad + layout.housing_radius)
    from_corner = math.dist(
        (layout.minimize_center_x, layout.minimize_center_y), corner
    )
    assert from_corner + layout.close_radius <= layout.housing_radius


def test_the_bar_shows_the_same_lamps_and_the_same_rate(tmp_path, monkeypatch, tk_root):
    """The bar is the same light, smaller — not a second reading of the sessions.

    It is painted on every repaint whether or not it is showing, so what this
    really pins is that the hidden size is already right: a bar that were only
    filled in on the way down would show the light as it was one refresh ago,
    which is the whole point of a status widget being wrong.
    """
    claude_home = _build_homes(tmp_path, monkeypatch)
    _write_session_file(claude_home, "waiting")
    _add_idle_codex_session(tmp_path / "codex_home")
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        # Still expanded, and the bar already agrees with the face.
        assert app.minimized is False
        for state in _LAMP_ORDER:
            assert app.bar()["lamps"][state] == app.lamps()[state]["fill"], state
        assert app.bar()["rate"] == app.stats()["rate"]
        # Two of the three are lit here, so this is not passing on three dark
        # lamps agreeing with three dark lamps.
        assert app.bar()["lamps"][SessionState.NEEDS_INPUT] == STATE_COLORS[
            SessionState.NEEDS_INPUT
        ]
        assert app.bar()["lamps"][SessionState.IDLE] == STATE_COLORS[SessionState.IDLE]
        assert app.bar()["lamps"][SessionState.RUNNING] == _UNLIT_COLORS[
            SessionState.RUNNING
        ]
        # And the rate is a real figure rather than an empty string.
        assert app.bar()["rate"].endswith(" tok/s")

        app.minimize()
        _write_session_file(claude_home, "busy")
        app.refresh()
        tk_root.update_idletasks()
        # Minimized, the face is the hidden one and it is the bar that must be
        # following the sessions.
        assert app.bar()["lamps"][SessionState.RUNNING] == STATE_COLORS[
            SessionState.RUNNING
        ]
        assert app.bar()["lamps"][SessionState.NEEDS_INPUT] == _UNLIT_COLORS[
            SessionState.NEEDS_INPUT
        ]
    finally:
        app.stop()


def test_clicking_the_little_light_restores_it_but_dragging_it_does_not(
    tmp_path, monkeypatch, tk_root
):
    """The bar has no button, so the light is one — and it still has to drag.

    Both gestures start on the same item, exactly as they do on the ✕, so the
    press that moves the bar across the desktop must not also throw the full
    face back up when it lands.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.minimize()
        assert app.minimized

        # A press that travelled is a drag: the bar stays a bar.
        app._start_drag(_StubEvent(20, 20, 320, 220))
        app._release_restore(_StubEvent(20, 20, 320 + _CLOSE_DRAG_SLOP + 1, 220))
        assert app.minimized is True

        # A press that stayed put — within the tolerance a hand can hold — is a
        # click, and brings the face back.
        app._start_drag(_StubEvent(20, 20, 320, 220))
        app._release_restore(_StubEvent(20, 20, 320 + _CLOSE_DRAG_SLOP, 220))
        assert app.minimized is False
        assert "<ButtonRelease-1>" in app._bar_canvas.tag_bind(_RESTORE_TAG)
    finally:
        app.stop()


def test_the_widget_takes_clicks_on_whatever_is_a_button_at_the_time(
    tmp_path, monkeypatch, tk_root
):
    """The solid list is the widget as far as the mouse is concerned.

    A button left out of it is drawn, bound and passed straight through to
    whatever is underneath — and the list has to change with the size, since the
    bar's only affordance is the little light and the face's two buttons are not
    even on the window any more.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        layout = app._layout
        assert app._pointer_over_solid(layout.close_center_x, layout.close_center_y)
        assert app._pointer_over_solid(
            layout.minimize_center_x, layout.minimize_center_y
        )
        # The light itself is not a button while the face is up: its counts are
        # a reading, not something to press.
        assert not app._pointer_over_solid(layout.lamp_center_x(1), layout.lamp_center_y)
        assert not app._pointer_over_solid(layout.center_x, layout.tokens_center_y)

        app.minimize()
        bar = app._bar_layout
        for index in range(len(_LAMP_ORDER)):
            assert app._pointer_over_solid(bar.lamp_center_x(index), bar.lamp_center_y)
        # The rate beside it is not: the bar passes clicks on everywhere the
        # little light is not.
        assert not app._pointer_over_solid(bar.rate_center_x, bar.rate_center_y)
        # And the face's buttons are gone with the face, rather than leaving a
        # solid patch on a window that no longer draws anything there.
        assert not app._pointer_over_solid(
            layout.close_center_x, layout.close_center_y
        )
    finally:
        app.stop()


def test_toggling_the_size_does_not_accumulate_canvas_items(
    tmp_path, monkeypatch, tk_root
):
    """Both sizes are built once, so a toggle may only change which is packed.

    Canvas items are not garbage collected: a bar rebuilt on every minimize
    would pile up behind the visible one, and the growth is invisible until the
    widget has been up for an afternoon.
    """
    _build_homes(tmp_path, monkeypatch)
    app = TrafficLightApp(tk_root, Monitor(), refresh_ms=50_000)
    try:
        app.refresh()
        tk_root.update_idletasks()
        before = (len(app._canvas.find_all()), len(app._bar_canvas.find_all()))
        for _ in range(3):
            app.minimize()
            app.refresh()
            app.restore()
            app.refresh()
        tk_root.update_idletasks()
        assert (len(app._canvas.find_all()), len(app._bar_canvas.find_all())) == before
    finally:
        app.stop()


def test_the_bar_is_scaled_to_the_display_like_the_face_is(tk_root):
    """The second size has to follow the display too, or it is sharp and tiny.

    Same ratio check the face gets, plus the tiling rule `_Face` depends on: a
    lamp's tile is its radius and its glow, and neighbouring tiles must meet
    rather than overlap, or each would clip the one beside it.
    """
    single, double = _BarLayout(1.0), _BarLayout(2.0)
    for name in ("width", "height", "lamp_radius", "lamp_pitch", "rate_font_px"):
        assert getattr(double, name) == pytest.approx(
            2 * getattr(single, name), abs=2
        ), name
    assert single.tile_half <= single.lamp_pitch / 2
    assert single.lamp_radius < single.tile_half
    # Nothing may be laid out past the housing it is drawn on.
    assert single.rate_center_x + single.rate_width // 2 <= single.pad + single.housing_width
