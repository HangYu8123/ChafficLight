"""The Tk traffic-light window: one coloured light per chat session.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, a green light while it is running, a
yellow light when it needs their input and a red light once it has finished,
along with token usage. They then asked for the window to be polished, for its
background to be transparent, and for the drawing to be higher resolution. They
then swapped what two of the lamps mean — red for a turn that is over, yellow
for a session that has asked them something — and asked for the yellow lamp to
flash whenever its count is not zero. They then asked that the widget block no
clicks at all apart from its close button, everything else passing through it.

Everything with an edge is rendered by Pillow at :data:`_SUPERSAMPLE` times the
final size and scaled back down, because the Tk canvas does not antialias: its
own ovals come out visibly stair-stepped, which is the whole of the "resolution"
complaint. The canvas still draws the text on top, since Tk *does* antialias text
and picks the desktop's own font while doing it.
"""

from __future__ import annotations

import base64
import io
import sys
import tkinter as tk
from collections import Counter
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFilter

from .monitor import Monitor
from .state import STATE_COLORS, SessionState

__all__ = ["TrafficLightApp", "enable_hidpi"]

#: Housing lamps, left to right. Red before yellow before green is the
#: arrangement the MUTCD requires of a *horizontal* signal face, and it is what
#: makes the drawing read as a traffic light rather than as three coloured
#: circles — so this is ordered by the *colour* each state carries in
#: :data:`~.state.STATE_COLORS`, and a state that swaps colour moves seat here
#: with it. IDLE holds the red lamp and NEEDS_INPUT the yellow one.
_LAMP_ORDER = (SessionState.IDLE, SessionState.NEEDS_INPUT, SessionState.RUNNING)

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
#: together whenever a state changes colour. Spelled out rather than computed
#: from :data:`~.state.STATE_COLORS` — deriving them would put hex parsing and
#: formatting on the repaint path, where a malformed result raises inside
#: ``refresh`` and ``_tick``'s ``finally`` then reschedules the same failure
#: forever. Only the three lamps need one; the off-face states are never drawn.
_UNLIT_COLORS = {
    SessionState.RUNNING: "#1f4623",
    SessionState.NEEDS_INPUT: "#534a13",
    SessionState.IDLE: "#532f2c",
}

#: The unlit shade for a lamp added since this table was written. Neutral dark,
#: so a new lamp renders as a plain dark circle rather than failing the repaint.
_UNLIT_FALLBACK = "#3e3e3e"

#: The one lamp that blinks while it has sessions, and how long each half of the
#: blink lasts. NEEDS_INPUT — the yellow lamp — is the only state that is
#: actually *blocking*: an agent has asked a question and nothing moves until it
#: is answered, where a red session has merely stopped and can be left. A steady
#: lamp among two other steady lamps is easy to leave sitting there unnoticed,
#: and motion is what peripheral vision picks up, so yellow flashes and the other
#: two do not: everything flashing would be the same as nothing flashing.
#:
#: Half a second is roughly the road-signal rate; much faster reads as a fault
#: and much slower can be missed between two glances at the screen.
_FLASHING_STATE = SessionState.NEEDS_INPUT
_FLASH_MS = 500

#: Painted everywhere outside the housing and then keyed out by the window
#: manager, so the whole background is a hole in the window: what floats on the
#: desktop is the signal housing itself and nothing else. Near-black rather than
#: a loud magenta because keying is exact — the antialiased rim of a shape blends
#: *towards* this colour without ever reaching it, so those pixels survive, and a
#: near-black survivor reads as a soft shadow instead of a coloured halo.
_KEY_COLOR = "#010203"

#: The backdrop when colour-keying is unavailable. ``-transparentcolor`` is a
#: Windows-only ``wm`` attribute, so X11 and Aqua get an opaque window; a plain
#: dark rectangle behind the housing is the honest fallback.
_OPAQUE_BACKDROP = "#1a1a1a"

#: How solid the window itself is. Slightly see-through, so the widget sits *in*
#: the desktop rather than on top of it; the lamps are saturated enough to stay
#: unambiguous through it. Windows applies this alongside the colour key, and
#: everywhere else it is either honoured by the compositor or ignored.
_WINDOW_ALPHA = 0.94

#: How many times over the final size every shape is drawn before being scaled
#: back down. Pillow's own drawing has hard edges, so this — not the drawing — is
#: what antialiases them. Three is where the stair-stepping stops being visible;
#: more only costs startup time.
_SUPERSAMPLE = 3

#: The housing: a charcoal case lit slightly from above, with a hairline rim. The
#: flat-icon convention (Twemoji ``#31373d``) sits between the two ends of the
#: gradient, and its contrast holds up over whatever wallpaper the widget floats
#: above.
_HOUSING_TOP_COLOR = "#3b424a"
_HOUSING_BOTTOM_COLOR = "#20242a"
_HOUSING_RIM_COLOR = "#4d5660"

#: The recess each lamp sits in, and the line around the lamp itself. Both are
#: darker than anything else on the housing, which is what makes an unlit lamp
#: read as a lamp that is off rather than as a smudge on the case.
_WELL_COLOR = "#15181b"
_LAMP_RIM_COLOR = "#0d0f11"

#: How far the lit colour bleeds onto the housing around a lamp, and how strongly
#: at its brightest. This is the one cue that survives being seen out of the
#: corner of an eye, which is the only way a status widget is ever looked at.
_GLOW_WIDTH = 8
_GLOW_ALPHA = 130

#: Counts sit inside the lamp, so they are read against the lamp's own colour.
#: Black clears WCAG AA on all three lit colours (9.8:1 green, 15.5:1 yellow,
#: 4.7:1 red) where white clears none of them — including red, which even as the
#: muted brick it now is stays light enough that the usual white-on-red instinct
#: fails. Red is the one with headroom to spare: toning it down any further
#: takes the count below AA against it, and that is the floor a darker red has
#: to buy its way past by giving *that* lamp white text of its own.
_LIT_TEXT_COLOR = "#000000"
_UNLIT_TEXT_COLOR = "#c8c8c8"

#: The two figures under the light, and the close button's own colours.
_TOKENS_TEXT_COLOR = "#f2f4f6"
_RATE_TEXT_COLOR = "#93a0ab"
_CLOSE_FILL_COLOR = "#454d57"
_CLOSE_MARK_COLOR = "#e6e6e6"

#: The shadow under the housing, and the alpha below which it is snapped to
#: nothing. The cutoff is what keeps a colour key honest: a blur's tail never
#: quite reaches zero, and a pixel one level off :data:`_KEY_COLOR` is not keyed
#: out — it is an opaque rectangle the size of the whole window.
_SHADOW_ALPHA = 115
_SHADOW_BLUR = 4
_SHADOW_OFFSET = 3
_SHADOW_CUTOFF = 6

#: Signal-face geometry in logical pixels — the design at 96 dpi, which
#: :class:`_Layout` scales to whatever the display actually has. The proportions
#: are the ones the clip-art traffic light icons share.
_LAMP_RADIUS = 22
_LAMP_GAP = 16
_WELL_RING = 3
_HOUSING_SIDE_MARGIN = 20
_HOUSING_TOP_MARGIN = 20
_HOUSING_BOTTOM_MARGIN = 18
_HOUSING_RADIUS = 20

#: The two figures hang off the housing directly, inside it: with the background
#: keyed away there is nothing else for them to be legible against.
_STATS_TOP_GAP = 24
_STATS_LINE_GAP = 20

#: The ✕, tucked into the housing's top-right corner. The margin is what keeps it
#: clear of both the rounded corner and the green lamp below it. The ─ that
#: minimizes the window sits at the same margin in the *top-left* corner, a
#: mirror image of it: the space immediately left of the ✕ is over the green
#: lamp, and the corners are the only two places on the housing with room.
_CLOSE_RADIUS = 9
_CLOSE_MARGIN = 15

#: Room around the housing for the shadow to fall into.
_CANVAS_PAD = 14

#: The minimized bar, in the same logical pixels as the face above: the three
#: lamps at a glanceable size with the tokens/sec figure beside them, and nothing
#: else. The counts are dropped rather than shrunk — a digit inside an 8 px lamp
#: is not a number anybody reads — and so is the running token total, since the
#: rate is the one figure the request names. The bar is what the widget *is*
#: while minimized, not an icon of it, which is why it stays a drawn housing.
#:
#: :data:`_BAR_LAMP_GAP` must stay at least twice :data:`_BAR_GLOW_WIDTH`, the
#: same tiling rule the full face obeys: a lamp's tile is its radius plus its
#: glow, and neighbouring tiles must meet rather than overlap.
_BAR_LAMP_RADIUS = 8
_BAR_LAMP_GAP = 8
_BAR_GLOW_WIDTH = 4
_BAR_WELL_RING = 2
_BAR_SIDE_MARGIN = 11
_BAR_VERTICAL_MARGIN = 9
_BAR_HOUSING_RADIUS = 12
_BAR_CANVAS_PAD = 8

#: The gap between the last lamp and the rate, and the room kept for the rate
#: itself. Reserved as a fixed width rather than measured, because the layout is
#: pure arithmetic with no Tk root to measure a font against — so it is sized for
#: a figure far larger than any real one and the text is centred inside it.
_BAR_RATE_GAP = 10
_BAR_RATE_WIDTH = 68
_BAR_RATE_FONT_PX = 12

#: Text sizes, in pixels rather than points, so they scale with the drawing
#: instead of with whatever ``tk scaling`` was left at — the housing is sized
#: around these, so a font that ignored the scale would overflow it.
_COUNT_FONT_PX = 17
_TOKENS_FONT_PX = 15
_RATE_FONT_PX = 12

#: Canvas items that drag the window, and the ones that close it. The window has
#: no title bar, so these bindings are the only way to move or dismiss it — and
#: they are bound per tag rather than per item because Tk delivers a click to the
#: *topmost* item under the pointer, which over most of the face is a lamp
#: rather than the housing beneath it.
_DRAG_TAG = "drag"
_CLOSE_TAG = "close"

#: The ─ that shrinks the widget to the bar, and the miniature light that brings
#: it back. The bar has no button of its own: the little traffic light *is* the
#: one, which is what keeps the minimized widget down to a light and a figure —
#: the two things it was asked to show — and keeps a 34 px strip from carrying a
#: disc nearly as tall as itself.
_MINIMIZE_TAG = "minimize"
_RESTORE_TAG = "restore"

#: How often the pointer is looked up while deciding whether the window should
#: be taking clicks at all. It is the interval between the pointer arriving on
#: the ✕ and the ✕ becoming clickable, so it has to be short enough to disappear
#: into the movement that got it there; a press landing inside it is not lost
#: silently but passed to whatever is underneath, and the next click works.
_CLICK_THROUGH_POLL_MS = 40

#: How far a press may travel and still count as a click on the ✕ rather than a
#: drag of the window. The ✕ is the only part of the widget that stays solid, so
#: it is also the only thing left to move the window by, and the two gestures
#: have to share it.
_CLOSE_DRAG_SLOP = 3

#: The Win32 numbers behind click-through. Hit-testing of a *layered* window
#: normally follows what it painted, but ``WS_EX_TRANSPARENT`` overrides that
#: and hands the mouse to whatever is underneath — documented on Microsoft's
#: "Window Features" page, and it is a property of the layered window, which is
#: why :data:`_WS_EX_LAYERED` is checked rather than assumed.
_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_LAYERED = 0x00080000

#: ``GetAncestor(hwnd, GA_ROOT)``: a Tk toplevel is two windows on Windows, and
#: ``winfo_id()`` answers with the inner one, which carries none of the styles.
_GA_ROOT = 2

#: user32 with the signatures below declared once, or None until first asked.
_WIN32_STYLE_CALLS = None


def _style_calls():
    """The three user32 entry points this module changes window styles with.

    Every signature is declared, because the ``Ptr`` variants return and take a
    pointer-width value that ctypes would otherwise truncate to 32 bits on a
    64-bit build — silently, and only for handles large enough to notice. The
    ``Ptr`` spelling itself is a C macro rather than an export on 32-bit
    Windows, so it is looked up by name and falls back to the plain one.
    """
    global _WIN32_STYLE_CALLS
    if _WIN32_STYLE_CALLS is None:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_style.restype = ctypes.c_ssize_t
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        _WIN32_STYLE_CALLS = (user32.GetAncestor, get_style, set_style)
    return _WIN32_STYLE_CALLS


def _layered_toplevel(root: tk.Tk) -> int | None:
    """The window whose style decides where this app's clicks land, if any.

    ``None`` on every platform but Windows, and on Windows too unless the window
    found really is the layered one: the answer is checked against
    :data:`_WS_EX_LAYERED` rather than trusted, because writing the pass-through
    bit to the wrong window would silently leave the widget blocking clicks and
    make something else click-through instead.
    """
    if sys.platform != "win32":
        return None
    try:
        get_ancestor, get_style, _set = _style_calls()
        handle = get_ancestor(root.winfo_id(), _GA_ROOT)
        if handle and get_style(handle, _GWL_EXSTYLE) & _WS_EX_LAYERED:
            return handle
    except (AttributeError, OSError):
        return None
    return None


def _set_click_through(handle: int, through: bool) -> bool:
    """Let clicks pass through ``handle``, or take them back; report success.

    Only the one bit is touched. The rest of the extended style is Tk's —
    ``WS_EX_LAYERED`` above all, which is what ``-transparentcolor`` set and
    what makes this bit mean anything.
    """
    try:
        _ancestor, get_style, set_style = _style_calls()
        style = get_style(handle, _GWL_EXSTYLE)
        wanted = style | _WS_EX_TRANSPARENT if through else style & ~_WS_EX_TRANSPARENT
        if wanted != style:
            set_style(handle, _GWL_EXSTYLE, wanted)
        return bool(get_style(handle, _GWL_EXSTYLE) & _WS_EX_TRANSPARENT) is through
    except (AttributeError, OSError):
        return False


def enable_hidpi() -> bool:
    """Tell Windows this process draws at the display's real pixel density.

    Without this a process is "DPI unaware": Windows hands it a 96 dpi
    coordinate space and then *bitmap-stretches* whatever it drew, so on the
    125% and 150% laptop displays that are now the default every edge in the
    window is resampled and soft. Declaring awareness is what makes the scale
    factor visible to :class:`_Layout`, which then draws more pixels rather than
    the same ones larger.

    Must be called before the first window exists — Windows fixes a window's
    awareness when it is created — which is why this is a module function the
    entry point calls rather than anything :class:`TrafficLightApp` does.

    Returns whether awareness was actually taken. False on every other platform,
    where X11 and Aqua already hand out real pixels, and also when a host process
    set it first: it can only be set once, and the answer is then already ours to
    live with rather than ours to choose.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    # Per-monitor v2 (Windows 10 1703) first, then the 8.1 and Vista APIs, which
    # is also the order of how much they get right. Each is missing outright on
    # the versions before it, so a missing symbol is an expected answer here.
    user32 = ctypes.windll.user32
    try:
        # -4 is DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2.
        if user32.SetProcessDpiAwarenessContext(-4):
            return True
    except (AttributeError, OSError):
        pass
    try:
        # 2 is PROCESS_PER_MONITOR_DPI_AWARE; S_OK is 0.
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return True
    except (AttributeError, OSError):
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """``"#rrggbb"`` as the tuple Pillow wants."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _rgb_to_hex(red: int, green: int, blue: int) -> str:
    """A rendered pixel back in the spelling the rest of the app uses."""
    return f"#{red:02x}{green:02x}{blue:02x}"


class _Geometry:
    """The arithmetic both sizes of the window share.

    A base class rather than a copy in each, because :class:`_Face` draws from
    these three and nothing else: a lamp's centre and the housing's box are what
    it crops tiles against, so the two layouts have to agree on them exactly or
    the bar's lamps would sit on pixels cut from somewhere they are not.
    """

    scale: float

    def _px(self, value: float) -> int:
        """One logical length in real pixels, never rounded away to nothing."""
        return max(1, round(value * self.scale))

    def lamp_center_x(self, index: int) -> int:
        """Where the ``index``-th lamp of :data:`_LAMP_ORDER` is centred."""
        return self.first_lamp_x + index * self.lamp_pitch

    def housing_box(self) -> tuple[int, int, int, int]:
        """The housing's ``(left, top, right, bottom)``, right/bottom exclusive."""
        return (
            self.pad,
            self.pad,
            self.pad + self.housing_width,
            self.pad + self.housing_height,
        )


class _Layout(_Geometry):
    """Every pixel position the window uses, at one display scale factor.

    Pure arithmetic on the logical constants above, so the whole geometry can be
    checked at any scale without a display attached — and so that a HiDPI screen
    changes only the numbers, never the drawing code.
    """

    def __init__(self, scale: float = 1.0):
        self.scale = scale
        px = self._px

        self.hairline = px(1)
        self.pad = px(_CANVAS_PAD)
        self.lamp_radius = px(_LAMP_RADIUS)
        self.lamp_pitch = px(2 * _LAMP_RADIUS + _LAMP_GAP)
        self.well_ring = px(_WELL_RING)
        # Half a lamp's tile: the lamp plus the glow around it. Equal to half the
        # pitch, so neighbouring tiles meet exactly and never overlap.
        self.tile_half = px(_LAMP_RADIUS + _GLOW_WIDTH)
        self.housing_radius = px(_HOUSING_RADIUS)

        side, top = px(_HOUSING_SIDE_MARGIN), px(_HOUSING_TOP_MARGIN)
        self.housing_width = (
            2 * side
            + 2 * self.lamp_radius
            + (len(_LAMP_ORDER) - 1) * self.lamp_pitch
        )
        self.first_lamp_x = self.pad + side + self.lamp_radius
        self.lamp_center_y = self.pad + top + self.lamp_radius
        self.tokens_center_y = self.lamp_center_y + self.lamp_radius + px(_STATS_TOP_GAP)
        self.rate_center_y = self.tokens_center_y + px(_STATS_LINE_GAP)
        self.housing_height = (
            self.rate_center_y - self.pad + px(_HOUSING_BOTTOM_MARGIN)
        )

        self.width = self.housing_width + 2 * self.pad
        self.height = self.housing_height + 2 * self.pad
        self.center_x = self.pad + self.housing_width // 2

        self.close_radius = px(_CLOSE_RADIUS)
        self.close_center_x = self.pad + self.housing_width - px(_CLOSE_MARGIN)
        self.close_center_y = self.pad + px(_CLOSE_MARGIN)
        self.close_half = self.close_radius + px(3)
        # The ─, mirrored into the opposite corner. The space immediately left of
        # the ✕ is over the green lamp, and these two corners are the only places
        # on the housing far enough from a lamp centre to take a button.
        self.minimize_center_x = self.pad + px(_CLOSE_MARGIN)
        self.minimize_center_y = self.close_center_y

        self.count_font_px = px(_COUNT_FONT_PX)
        self.tokens_font_px = px(_TOKENS_FONT_PX)
        self.rate_font_px = px(_RATE_FONT_PX)


class _BarLayout(_Geometry):
    """Every pixel position the minimized bar uses, at one display scale factor.

    Deliberately the same attribute names :class:`_Face` reads off
    :class:`_Layout`, so one renderer draws both sizes and neither has a drawing
    path of its own to keep in step. What differs is only what the bar carries:
    three small lamps and the rate beside them, no counts, no token total and no
    buttons.
    """

    def __init__(self, scale: float = 1.0):
        self.scale = scale
        px = self._px

        self.hairline = px(1)
        self.pad = px(_BAR_CANVAS_PAD)
        self.lamp_radius = px(_BAR_LAMP_RADIUS)
        self.lamp_pitch = px(2 * _BAR_LAMP_RADIUS + _BAR_LAMP_GAP)
        self.well_ring = px(_BAR_WELL_RING)
        self.tile_half = px(_BAR_LAMP_RADIUS + _BAR_GLOW_WIDTH)
        self.housing_radius = px(_BAR_HOUSING_RADIUS)

        side = px(_BAR_SIDE_MARGIN)
        lamps_width = (
            2 * self.lamp_radius + (len(_LAMP_ORDER) - 1) * self.lamp_pitch
        )
        self.rate_width = px(_BAR_RATE_WIDTH)
        self.housing_width = (
            2 * side + lamps_width + px(_BAR_RATE_GAP) + self.rate_width
        )
        self.housing_height = 2 * px(_BAR_VERTICAL_MARGIN) + 2 * self.lamp_radius

        self.first_lamp_x = self.pad + side + self.lamp_radius
        self.lamp_center_y = self.pad + self.housing_height // 2

        self.width = self.housing_width + 2 * self.pad
        self.height = self.housing_height + 2 * self.pad

        self.rate_center_x = (
            self.pad + self.housing_width - side - self.rate_width // 2
        )
        self.rate_center_y = self.lamp_center_y
        self.rate_font_px = px(_BAR_RATE_FONT_PX)


def _vertical_gradient(
    size: tuple[int, int], top_color: str, bottom_color: str
) -> Image.Image:
    """A one-pixel-wide ramp stretched across ``size``.

    Built as a column and widened by nearest-neighbour rather than drawn row by
    row: the ramp is what gives the housing its lit-from-above look, and this
    computes it once per row instead of once per pixel.
    """
    width, height = size
    column = Image.new("RGB", (1, height))
    top, bottom = _hex_to_rgb(top_color), _hex_to_rgb(bottom_color)
    pixels = column.load()
    for y in range(height):
        weight = y / max(1, height - 1)
        pixels[0, y] = tuple(
            round(start + (end - start) * weight) for start, end in zip(top, bottom)
        )
    return column.resize(size, Image.Resampling.NEAREST)


class _Face:
    """Renders the window's pixels, oversampled and scaled back down.

    Holds the oversampled scene so that a lamp can be redrawn against the exact
    housing pixels beneath it: every tile is cut from this one image, which is
    what makes a lamp's surroundings match the housing seamlessly instead of
    being a second guess at the same gradient.
    """

    def __init__(self, layout: _Layout, backdrop: str):
        self._layout = layout
        self._sample = _SUPERSAMPLE
        self._scene = self._render_scene(backdrop)

    def base(self) -> Image.Image:
        """The whole window: backdrop, shadow, housing and empty lamp wells."""
        return self._reduce(self._scene, (self._layout.width, self._layout.height))

    def lamp(self, index: int, color: str, lit: bool) -> Image.Image:
        """One lamp, on the housing pixels that surround it.

        The lit variant glows: the lamp's own colour, blurred outward over the
        housing. It stops inside the tile, so tiles that touch never seam.
        """
        layout = self._layout
        center_x, center_y = layout.lamp_center_x(index), layout.lamp_center_y
        tile = self._crop(center_x, center_y, layout.tile_half)
        local = layout.tile_half
        rgb = _hex_to_rgb(color)

        if lit:
            glow = Image.new("L", tile.size, 0)
            ImageDraw.Draw(glow).ellipse(
                self._disc(local, local, layout.lamp_radius + layout.well_ring),
                fill=_GLOW_ALPHA,
            )
            # Half the glow's width as the blur radius: Pillow's Gaussian reaches
            # about three of those, so the bleed dies out just inside the tile.
            # Taken from the tile the layout actually built — the room it left
            # around the lamp *is* the glow — rather than from the module
            # constant, so the bar's smaller lamps get their own smaller bleed
            # and neither size can drift from the width it was given.
            spread = (layout.tile_half - layout.lamp_radius) * self._sample
            tile.paste(rgb, mask=glow.filter(ImageFilter.GaussianBlur(spread / 2)))

        ImageDraw.Draw(tile).ellipse(
            self._disc(local, local, layout.lamp_radius),
            fill=rgb,
            outline=_hex_to_rgb(_LAMP_RIM_COLOR),
            width=layout.hairline * self._sample,
        )
        return self._reduce(tile, (2 * local, 2 * local))

    def button(
        self, center_x: int, center_y: int, radius: int, half: int, mark: str
    ) -> Image.Image:
        """One of the window's buttons, as its own opaque, clickable disc.

        Its own image rather than part of the housing because a canvas delivers a
        click to the topmost *item*: drawn into the housing it would be pixels on
        the thing that drags the window, and dragging is what the click would do.

        Every mark is drawn from lines rather than set as a glyph — ``✕``, ``─``
        — because these are the only affordances the window has, and they must
        not depend on whichever font Tk falls back to on a given desktop.
        """
        layout = self._layout
        tile = self._crop(center_x, center_y, half)
        draw = ImageDraw.Draw(tile)
        draw.ellipse(self._disc(half, half, radius), fill=_hex_to_rgb(_CLOSE_FILL_COLOR))
        arm = round(radius * 0.42) * self._sample
        center = half * self._sample
        ink = _hex_to_rgb(_CLOSE_MARK_COLOR)
        width = max(1, round(layout.hairline * 1.6)) * self._sample
        if mark == "cross":
            for x_sign in (1, -1):
                draw.line(
                    (
                        center - arm * x_sign, center - arm,
                        center + arm * x_sign, center + arm,
                    ),
                    fill=ink,
                    width=width,
                )
        else:
            # The ─ is drawn wider than either arm of the ✕ so the two read as a
            # pair at the same size: a bar as short as the cross is tall looks
            # like a stray dot rather than a minimize mark.
            reach = round(radius * 0.55) * self._sample
            draw.line((center - reach, center, center + reach, center), fill=ink, width=width)
        return self._reduce(tile, (2 * half, 2 * half))

    def _length(self, logical: float) -> float:
        """A logical length in oversampled pixels."""
        return logical * self._layout.scale * self._sample

    def _disc(self, center_x: int, center_y: int, radius: int) -> tuple[int, int, int, int]:
        """The oversampled bounding box of a circle given in final pixels."""
        sample = self._sample
        return (
            (center_x - radius) * sample,
            (center_y - radius) * sample,
            (center_x + radius) * sample - 1,
            (center_y + radius) * sample - 1,
        )

    def _crop(self, center_x: int, center_y: int, half: int) -> Image.Image:
        """The oversampled scene around a point, as a square tile to draw on."""
        sample = self._sample
        return self._scene.crop(
            (
                (center_x - half) * sample,
                (center_y - half) * sample,
                (center_x + half) * sample,
                (center_y + half) * sample,
            )
        )

    def _reduce(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        """Scale an oversampled drawing down to its final size."""
        return image.resize(size, Image.Resampling.LANCZOS)

    def _render_scene(self, backdrop: str) -> Image.Image:
        """The whole window at :data:`_SUPERSAMPLE` times its final size."""
        layout, sample = self._layout, self._sample
        size = (layout.width * sample, layout.height * sample)
        scene = Image.new("RGB", size, _hex_to_rgb(backdrop))
        left, top, right, bottom = (edge * sample for edge in layout.housing_box())
        housing = (left, top, right - 1, bottom - 1)
        radius = layout.housing_radius * sample

        self._paint_shadow(scene, housing, radius)
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(housing, radius=radius, fill=255)
        scene.paste(
            _vertical_gradient(size, _HOUSING_TOP_COLOR, _HOUSING_BOTTOM_COLOR),
            mask=mask,
        )
        ImageDraw.Draw(scene).rounded_rectangle(
            housing,
            radius=radius,
            outline=_hex_to_rgb(_HOUSING_RIM_COLOR),
            width=layout.hairline * sample,
        )

        wells = ImageDraw.Draw(scene)
        for index in range(len(_LAMP_ORDER)):
            wells.ellipse(
                self._disc(
                    layout.lamp_center_x(index),
                    layout.lamp_center_y,
                    layout.lamp_radius + layout.well_ring,
                ),
                fill=_hex_to_rgb(_WELL_COLOR),
            )
        return scene

    def _paint_shadow(
        self, scene: Image.Image, housing: tuple[int, int, int, int], radius: int
    ) -> None:
        """Drop a soft shadow under the housing, onto ``scene``'s backdrop.

        Snapped to nothing below :data:`_SHADOW_CUTOFF` before it is painted. A
        Gaussian tail never reaches zero, and against a colour key the difference
        between "almost the backdrop" and "the backdrop" is the difference
        between a transparent window and an opaque one.
        """
        offset = round(self._length(_SHADOW_OFFSET))
        left, top, right, bottom = housing
        shadow = Image.new("L", scene.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (left, top + offset, right, bottom + offset), radius=radius, fill=_SHADOW_ALPHA
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(self._length(_SHADOW_BLUR)))
        scene.paste(
            (0, 0, 0), mask=shadow.point(lambda a: 0 if a < _SHADOW_CUTOFF else a)
        )


def _photo(image: Image.Image) -> tk.PhotoImage:
    """A Pillow image as a Tk one.

    Through PNG bytes rather than ``PIL.ImageTk`` so the window depends on
    nothing but Tk's own image support — and so the result is a real
    ``tk.PhotoImage``, whose pixels :meth:`TrafficLightApp.lamps` can read back.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buffer.getvalue()).decode("ascii"))


def _photo_pixel(photo: tk.PhotoImage, x: int, y: int) -> str:
    """One rendered pixel of ``photo``, as ``"#rrggbb"``.

    Tk answers with either a string or a tuple depending on the build, and with
    a fourth alpha component on some of them; both spellings are normalised here
    rather than at every call site.
    """
    value = photo.get(x, y)
    if isinstance(value, str):
        value = value.split()
    red, green, blue = (int(part) for part in tuple(value)[:3])
    return _rgb_to_hex(red, green, blue)


class TrafficLightApp:
    """Draws the signal face and the token figures, and repaints on a timer.

    Three timers, in fact, at deliberately different rates: `_tick` polls the
    monitor every ``refresh_ms``, `_flash_tick` blinks the yellow lamp every
    :data:`_FLASH_MS` in between, and `_click_through_tick` watches the pointer
    every :data:`_CLICK_THROUGH_POLL_MS` to decide whether the widget should be
    taking clicks at all. Sharing one timer would tie how fast the lamp flashes,
    and how quickly the ✕ answers, to how often the disk is read.
    """

    def __init__(self, root: tk.Tk, monitor: Monitor, refresh_ms: int = 2000):
        """Attach the app to ``root`` and poll ``monitor`` every ``refresh_ms``."""
        self._root = root
        self._monitor = monitor
        self._refresh_ms = refresh_ms
        self._after_id: str | None = None
        self._drag_offset: tuple[int, int] | None = None
        self._drag_origin: tuple[int, int] | None = None
        # Click-through: the window it is set on, whether it is currently set,
        # and the timer that follows the pointer. The applied state starts as
        # None rather than False so the first look always writes it, whatever
        # the window happened to open as.
        self._click_through_handle: int | None = None
        self._click_through_on: bool | None = None
        self._click_through_after_id: str | None = None
        # The blink: which half of it is showing, and how many sessions the last
        # refresh put on the flashing lamp. The count is kept because the blink
        # runs on its own timer between refreshes and must repaint that lamp
        # without asking the monitor anything — a flash may never contradict
        # what the face was last told.
        self._flash_after_id: str | None = None
        self._flash_on = True
        self._flash_count = 0

        root.title("CLI Traffic Light")
        root.overrideredirect(True)
        #: Whether the backdrop is keyed out of the window. Public because it is
        #: the only thing that says which of the two looks the user is getting.
        self.transparent = self._enable_transparency()
        self._set_alpha(_WINDOW_ALPHA)
        backdrop = _KEY_COLOR if self.transparent else _OPAQUE_BACKDROP
        root.configure(background=backdrop)
        try:
            root.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        # The window manager is not involved any more, so WM_DELETE_WINDOW can
        # never fire: the close button and this key are the only ways out.
        root.bind("<Escape>", lambda _event: self.close())

        self._layout = _Layout(self._display_scale())
        self._canvas = tk.Canvas(
            root,
            width=self._layout.width,
            height=self._layout.height,
            highlightthickness=0,
            background=backdrop,
        )
        self._canvas.pack()
        # Every photo this window shows, by its Tk name. Tk images are not owned
        # by the canvas that draws them: dropping the last Python reference frees
        # the pixels and the item goes blank, so they are all kept here — which
        # is also what lets :meth:`lamps` read back what is on screen.
        self._images: dict[str, tk.PhotoImage] = {}
        self._lamp_photos: dict[SessionState, dict[bool, tk.PhotoImage]] = {}

        face = _Face(self._layout, backdrop)
        self._build_face(face)
        self._tokens_text, self._rate_text = self._build_stats()
        self._build_buttons(face)
        #: Whether the widget is currently the bar rather than the full face.
        #: Public because it is the other thing, besides `transparent`, that says
        #: which of two looks is on screen.
        self.minimized = False
        self._bar_layout = _BarLayout(self._layout.scale)
        self._bar_canvas = tk.Canvas(
            root,
            width=self._bar_layout.width,
            height=self._bar_layout.height,
            highlightthickness=0,
            background=backdrop,
        )
        self._build_bar(_Face(self._bar_layout, backdrop))
        self._canvas.tag_bind(_DRAG_TAG, "<ButtonPress-1>", self._start_drag)
        self._canvas.tag_bind(_DRAG_TAG, "<B1-Motion>", self._drag)
        self._canvas.tag_bind(_DRAG_TAG, "<ButtonRelease-1>", self._end_drag)
        # The ✕ drags as well as closes. Once the rest of the widget stops
        # taking clicks it is the only solid thing left, and `_start_drag` is
        # the only code in the app that ever positions the window — so without
        # this the light would sit wherever it opened for the rest of its life.
        self._canvas.tag_bind(_CLOSE_TAG, "<ButtonPress-1>", self._start_drag)
        self._canvas.tag_bind(_CLOSE_TAG, "<B1-Motion>", self._drag)
        self._canvas.tag_bind(_CLOSE_TAG, "<ButtonRelease-1>", self._release_close)
        # The ─ and the miniature light carry the same three bindings for the
        # same reason: while clicks pass through everything else, a button is
        # also the only thing left to move the window by.
        self._canvas.tag_bind(_MINIMIZE_TAG, "<ButtonPress-1>", self._start_drag)
        self._canvas.tag_bind(_MINIMIZE_TAG, "<B1-Motion>", self._drag)
        self._canvas.tag_bind(_MINIMIZE_TAG, "<ButtonRelease-1>", self._release_minimize)
        #: Whether clicks pass through the widget everywhere but the ✕. Public
        #: alongside `transparent`, and — like it — a statement about what this
        #: window can do: passing clicks on is a property of the *layered*
        #: window `-transparentcolor` just made, so it follows that answer.
        self.click_through = sys.platform == "win32" and self.transparent
        if self.click_through:
            # Straight away rather than on the timer, so the widget spends as
            # little time as possible solid over whatever it opened on top of.
            self._click_through_tick()
        self._schedule()

    def refresh(self) -> None:
        """Pull a fresh snapshot, relight the lamps and update the figures."""
        sessions = self._monitor.snapshot()
        self._relight(Counter(session.state for session in sessions))
        # What this window has watched being spent, not what the transcripts on
        # disk add up to: the figure starts at zero every time the app opens, so
        # it answers "what has this cost me since I sat down?".
        totals = self._monitor.totals_since_start()
        self._canvas.itemconfig(
            self._tokens_text, text=f"{totals.total_tokens:,} tokens"
        )
        # Summed rather than recomputed off a pooled counter, so a session
        # ending cannot drop a shared cumulative total and fake a negative
        # interval. The terms are addable even though each is measured over its
        # own newest step: they are speeds running side by side right now, and
        # two sessions burning tokens at once do burn them at the combined rate.
        #
        # Over the RUNNING sessions alone. Only a turn in flight can be billing
        # tokens, so this is what the figure claims to measure — and it is what
        # makes the two halves of the face agree by construction: a dark green
        # lamp now *means* zero, instead of merely tending towards it. A rate
        # ages with the silence after its last billed record rather than
        # stopping dead, so every other state carries the decaying tail of a
        # burst that is already over: a finished session for the minute after
        # it ends, and an idle one for the minute after its turn does. Summing
        # those printed a speed under three lamps reading zero.
        rate = sum(
            session.tokens_per_sec
            for session in sessions
            if session.state is SessionState.RUNNING
        )
        rate_text = f"{rate:.1f} tok/s"
        self._canvas.itemconfig(self._rate_text, text=rate_text)
        # The one figure the bar keeps: minimized, the question is still "how
        # fast is this costing me?", where the running total is something to go
        # and look at rather than to watch.
        self._bar_canvas.itemconfig(self._bar_rate_text, text=rate_text)

    def lamps(self) -> dict[SessionState, dict[str, str]]:
        """Each lamp's rendered ``{"fill", "text", "text_fill"}``, off the canvas.

        It reports what is actually drawn rather than what was intended, so a
        test cannot pass on a value the canvas rejected. ``fill`` is the pixel at
        the middle of the lamp read back out of the image the canvas is currently
        showing — which is why the lamp face is left a flat, exact
        :data:`~.state.STATE_COLORS` colour and all the shading kept outside it.
        ``text_fill`` is included because the count is only legible while it
        contrasts with the lamp under it, which no other reading would catch.
        """
        middle = self._layout.tile_half
        return {
            state: {
                "fill": _photo_pixel(
                    self._images[self._canvas.itemcget(image, "image")], middle, middle
                ),
                "text": self._canvas.itemcget(text, "text"),
                "text_fill": self._canvas.itemcget(text, "fill"),
            }
            for state, (image, text) in self._lamps.items()
        }

    def stats(self) -> dict[str, str]:
        """The token figures under the light, as rendered."""
        return {
            "tokens": self._canvas.itemcget(self._tokens_text, "text"),
            "rate": self._canvas.itemcget(self._rate_text, "text"),
        }

    def bar(self) -> dict[str, object]:
        """What the minimized bar is showing: each lamp's fill, and the rate.

        Read back off the bar's own canvas and its own middle pixel, exactly as
        `lamps()` is: the bar's lamps are smaller, so sampling them at the full
        face's `tile_half` would read a pixel outside the lamp altogether.
        """
        middle = self._bar_layout.tile_half
        return {
            "lamps": {
                state: _photo_pixel(
                    self._images[self._bar_canvas.itemcget(item, "image")], middle, middle
                )
                for state, item in self._bar_lamps.items()
            },
            "rate": self._bar_canvas.itemcget(self._bar_rate_text, "text"),
        }

    def minimize(self) -> None:
        """Shrink the widget to the bar: the light, small, and the rate."""
        if self.minimized:
            return
        self.minimized = True
        self._canvas.pack_forget()
        self._bar_canvas.pack()
        self._resize(self._bar_layout)

    def restore(self) -> None:
        """Bring the full face back, where the bar was standing."""
        if not self.minimized:
            return
        self.minimized = False
        self._bar_canvas.pack_forget()
        self._canvas.pack()
        self._resize(self._layout)

    def stop(self) -> None:
        """Cancel every pending ``after()`` callback this app scheduled."""
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None
        if self._flash_after_id is not None:
            self._root.after_cancel(self._flash_after_id)
            self._flash_after_id = None
        if self._click_through_after_id is not None:
            self._root.after_cancel(self._click_through_after_id)
            self._click_through_after_id = None

    def close(self) -> None:
        """Stop the timer and tear the window down."""
        self.stop()
        self._root.destroy()

    def _display_scale(self) -> float:
        """How many real pixels the display gives per logical one.

        1.0 unless the screen is denser than 96 dpi *and* the process was allowed
        to find out — see `enable_hidpi`. Never below 1.0, which would draw the
        widget smaller than designed rather than sharper.
        """
        return max(1.0, self._root.winfo_fpixels("1i") / 96.0)

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

    def _set_alpha(self, alpha: float) -> None:
        """Make the whole window slightly see-through, where that is supported.

        Best-effort on purpose: an X11 session with no compositor simply ignores
        it, and the widget is fully legible at full opacity anyway.
        """
        try:
            self._root.wm_attributes("-alpha", alpha)
        except tk.TclError:
            pass

    def _font(self, pixels: int, *styles: str) -> tuple:
        """The desktop's own font at an exact pixel height.

        Negative sizes are Tk's spelling for pixels. Points would be scaled again
        by ``tk scaling``, which is a second, independent guess at the display's
        density — and the housing is sized around these numbers.
        """
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        return (family, -pixels, *styles)

    def _add_image(self, photo: tk.PhotoImage) -> tk.PhotoImage:
        """Keep a photo alive for as long as the window can show it."""
        self._images[str(photo)] = photo
        return photo

    def _build_face(self, face: _Face) -> None:
        """Draw the housing and the lamps; record each state's ``(image, text)``.

        Every item is created here and only ever reconfigured afterwards. Canvas
        items are not garbage collected, so recreating them on each of the ~30
        repaints a minute would pile up unboundedly behind the visible ones; it
        also keeps the ids stable, which is what lets :meth:`lamps` read them.
        Both variants of every lamp are rendered now for the same reason —
        relighting then swaps a ready image instead of rasterising one.
        """
        layout = self._layout
        self._canvas.create_image(
            0, 0, image=self._add_image(_photo(face.base())), anchor="nw", tags=_DRAG_TAG
        )
        # The three lamps answer "what do I have to do?" — nothing, go and
        # reply, or type the next prompt. Every session on the face is one you
        # could act on, whichever CLI it belongs to; :data:`_OFF_FACE_STATES`
        # says which states are deliberately absent and why.
        self._lamps: dict[SessionState, tuple[int, int]] = {}
        for index, state in enumerate(_LAMP_ORDER):
            self._lamp_photos[state] = {
                lit: self._add_image(
                    _photo(
                        face.lamp(
                            index,
                            STATE_COLORS[state]
                            if lit
                            else _UNLIT_COLORS.get(state, _UNLIT_FALLBACK),
                            lit,
                        )
                    )
                )
                for lit in (False, True)
            }
            center_x = layout.lamp_center_x(index)
            image = self._canvas.create_image(
                center_x,
                layout.lamp_center_y,
                image=self._lamp_photos[state][False],
                anchor="center",
                tags=_DRAG_TAG,
            )
            text = self._canvas.create_text(
                center_x,
                layout.lamp_center_y,
                text="0",
                fill=_UNLIT_TEXT_COLOR,
                font=self._font(layout.count_font_px, "bold"),
                tags=_DRAG_TAG,
            )
            self._lamps[state] = (image, text)

    def _build_stats(self) -> tuple[int, int]:
        """The two figures under the light, as their canvas ids.

        They sit inside the housing rather than on the keyed backdrop: text is
        the one thing Tk anti-aliases, and an anti-aliased edge over a colour key
        keeps the blended pixels the key does not match, leaving a rim around
        every glyph.
        """
        layout = self._layout
        tokens = self._canvas.create_text(
            layout.center_x,
            layout.tokens_center_y,
            text="",
            fill=_TOKENS_TEXT_COLOR,
            font=self._font(layout.tokens_font_px, "bold"),
            tags=_DRAG_TAG,
        )
        rate = self._canvas.create_text(
            layout.center_x,
            layout.rate_center_y,
            text="",
            fill=_RATE_TEXT_COLOR,
            font=self._font(layout.rate_font_px),
            tags=_DRAG_TAG,
        )
        return tokens, rate

    def _build_buttons(self, face: _Face) -> None:
        """Place the ─ and the ✕, last, so nothing can be drawn over them.

        A keyed pixel is not merely invisible but click-through, so each disc is
        opaque; and a canvas hands a click to the topmost item, so both are
        created after the lamps whose tiles their corners reach into. Either
        mistake leaves a button that is drawn and does nothing.
        """
        layout = self._layout
        for center_x, center_y, mark, tag in (
            (layout.minimize_center_x, layout.minimize_center_y, "dash", _MINIMIZE_TAG),
            (layout.close_center_x, layout.close_center_y, "cross", _CLOSE_TAG),
        ):
            self._canvas.create_image(
                center_x,
                center_y,
                image=self._add_image(
                    _photo(
                        face.button(
                            center_x, center_y, layout.close_radius, layout.close_half, mark
                        )
                    )
                ),
                anchor="center",
                tags=tag,
            )

    def _build_bar(self, face: _Face) -> None:
        """Draw the minimized bar on its own canvas: three lamps and the rate.

        Its own canvas rather than more items on the one already there, so that
        only one of the two sizes is ever packed and everything else — the item
        ids `lamps()` reads, the "nothing is drawn outside the canvas" rule, the
        count of items a repaint may not add to — keeps meaning exactly what it
        meant before. Both sizes are built now and neither is ever rebuilt: the
        one that is hidden is repainted alongside the one that is showing, so a
        toggle can never reveal a stale light.
        """
        layout, canvas = self._bar_layout, self._bar_canvas
        canvas.create_image(
            0, 0, image=self._add_image(_photo(face.base())), anchor="nw", tags=_DRAG_TAG
        )
        self._bar_lamps: dict[SessionState, int] = {}
        self._bar_lamp_photos: dict[SessionState, dict[bool, tk.PhotoImage]] = {}
        for index, state in enumerate(_LAMP_ORDER):
            self._bar_lamp_photos[state] = {
                lit: self._add_image(
                    _photo(
                        face.lamp(
                            index,
                            STATE_COLORS[state]
                            if lit
                            else _UNLIT_COLORS.get(state, _UNLIT_FALLBACK),
                            lit,
                        )
                    )
                )
                for lit in (False, True)
            }
            self._bar_lamps[state] = canvas.create_image(
                layout.lamp_center_x(index),
                layout.lamp_center_y,
                image=self._bar_lamp_photos[state][False],
                anchor="center",
                tags=_RESTORE_TAG,
            )
        self._bar_rate_text = canvas.create_text(
            layout.rate_center_x,
            layout.rate_center_y,
            text="",
            fill=_RATE_TEXT_COLOR,
            font=self._font(layout.rate_font_px, "bold"),
            tags=_DRAG_TAG,
        )
        # The light itself is the way back, so it takes the same press/move/
        # release trio the ✕ does; the housing and the figure beside it drag
        # only, which is what they do on the full face too.
        canvas.tag_bind(_RESTORE_TAG, "<ButtonPress-1>", self._start_drag)
        canvas.tag_bind(_RESTORE_TAG, "<B1-Motion>", self._drag)
        canvas.tag_bind(_RESTORE_TAG, "<ButtonRelease-1>", self._release_restore)
        canvas.tag_bind(_DRAG_TAG, "<ButtonPress-1>", self._start_drag)
        canvas.tag_bind(_DRAG_TAG, "<B1-Motion>", self._drag)
        canvas.tag_bind(_DRAG_TAG, "<ButtonRelease-1>", self._end_drag)

    def _relight(self, counts: Counter) -> None:
        """Light each lamp whose state has sessions and dim the rest.

        :data:`_FLASHING_STATE` is the exception: while it has sessions it also
        blinks, so a repaint puts whichever half of the blink is currently
        showing on it rather than always the lit one.
        """
        flashing = counts[_FLASHING_STATE]
        if flashing and not self._flash_count:
            # It has just come on. Start the blink lit, so the lamp is yellow the
            # instant a session starts waiting rather than up to _FLASH_MS later.
            self._flash_on = True
        self._flash_count = flashing
        for state in self._lamps:
            self._paint_lamp(state, counts[state])
        self._schedule_flash()

    def _paint_lamp(self, state: SessionState, count: int) -> None:
        """Put one lamp's image and count on the canvas.

        The count stays on a blinking lamp through both halves — it is the
        answer to "how many?", and a number that came and went would be harder
        to read than the blink is to notice — but its colour follows the lamp
        under it, which is the only thing keeping it legible on either shade.
        """
        image, text = self._lamps[state]
        lit = bool(count) and (self._flash_on or state is not _FLASHING_STATE)
        self._canvas.itemconfig(image, image=self._lamp_photos[state][lit])
        self._canvas.itemconfig(
            text,
            text=str(count),
            fill=_LIT_TEXT_COLOR if lit else _UNLIT_TEXT_COLOR,
        )
        # The bar carries the same three lamps, blink included, and is painted
        # here rather than when it is shown: whichever size is hidden has to be
        # right already, or minimizing would show the light as it was one refresh
        # ago. It carries no count — there is no room to read one.
        self._bar_canvas.itemconfig(
            self._bar_lamps[state], image=self._bar_lamp_photos[state][lit]
        )

    def _schedule_flash(self) -> None:
        """Run the blink timer exactly while the flashing lamp has sessions.

        Started and stopped from the count rather than left ticking forever, so
        a face with nothing waiting on it does no work at all — and so the lamp
        cannot be left stranded on the dark half of a blink that stopped.
        """
        if self._flash_count and self._flash_after_id is None:
            self._flash_after_id = self._root.after(_FLASH_MS, self._flash_tick)
        elif not self._flash_count and self._flash_after_id is not None:
            self._root.after_cancel(self._flash_after_id)
            self._flash_after_id = None
            self._flash_on = True

    def _flash_tick(self) -> None:
        """Swap the flashing lamp to the other half of its blink.

        The reschedule is in a ``finally`` for the same reason `_tick`'s is: a
        repaint that raises must not leave the lamp frozen on whichever half it
        happened to be showing.
        """
        self._flash_after_id = None
        try:
            self._flash_on = not self._flash_on
            self._paint_lamp(_FLASHING_STATE, self._flash_count)
        finally:
            self._schedule_flash()

    def _resize(self, layout: _Geometry) -> None:
        """Give the window the size of whichever face is now packed.

        Set outright rather than left to Tk's geometry propagation: `_drag` has
        very likely already called ``wm geometry``, and Tk documents a toplevel
        whose geometry has been set as no longer following the size its children
        ask for. The position is read back and written unchanged, so the widget
        stays where it was standing — the two sizes share their top-left corner
        rather than their centre, which is also what makes a drag of the bar and
        a drag of the face mean the same thing.
        """
        self._root.update_idletasks()
        self._root.geometry(
            f"{layout.width}x{layout.height}"
            f"+{self._root.winfo_x()}+{self._root.winfo_y()}"
        )

    def _solid_spots(self) -> tuple[tuple[int, int, int], ...]:
        """Every ``(x, y, half)`` square this widget still takes clicks on.

        Whichever size is showing: the two buttons on the full face, and the
        miniature light on the bar. Everything else is passed through, so this
        list *is* the widget as far as the mouse is concerned — a button left out
        of it is drawn, bound, and unclickable.
        """
        if self.minimized:
            bar = self._bar_layout
            return tuple(
                (bar.lamp_center_x(index), bar.lamp_center_y, bar.tile_half)
                for index in range(len(_LAMP_ORDER))
            )
        layout = self._layout
        return (
            (layout.close_center_x, layout.close_center_y, layout.close_half),
            (layout.minimize_center_x, layout.minimize_center_y, layout.close_half),
        )

    def _pointer_over_solid(self, x: int, y: int) -> bool:
        """Whether a window-relative point is on any of :meth:`_solid_spots`."""
        return any(
            abs(x - center_x) <= half and abs(y - center_y) <= half
            for center_x, center_y, half in self._solid_spots()
        )

    def _pointer_over_close(self, x: int, y: int) -> bool:
        """Whether a window-relative point is on the ✕.

        The whole of the ✕'s canvas item, not just the disc drawn inside it: a
        canvas hands a click to any item whose *rectangle* covers the pointer,
        so anything smaller would leave a ring that looks clickable, is
        clickable, and would nonetheless be passed straight through.
        """
        layout = self._layout
        return (
            abs(x - layout.close_center_x) <= layout.close_half
            and abs(y - layout.close_center_y) <= layout.close_half
        )

    def _click_through_tick(self) -> None:
        """Take clicks only while the pointer is on the ✕; pass the rest on.

        Polled rather than answered on demand, because a window that is not
        taking clicks is not told the pointer crossed it either — there is no
        event to bind. The reschedule is in a ``finally`` for the same reason
        `_tick`'s is: giving up here would leave the widget stuck solid, or
        stuck with a ✕ that cannot be pressed.
        """
        self._click_through_after_id = None
        try:
            # Not mid-drag: the pointer leaves the ✕ immediately, and a window
            # that stopped taking clicks halfway through would drop the gesture
            # that is moving it.
            if self._drag_offset is None:
                pointer_x, pointer_y = self._root.winfo_pointerxy()
                over = self._pointer_over_solid(
                    pointer_x - self._root.winfo_rootx(),
                    pointer_y - self._root.winfo_rooty(),
                )
                self._apply_click_through(not over)
        finally:
            self._click_through_after_id = self._root.after(
                _CLICK_THROUGH_POLL_MS, self._click_through_tick
            )

    def _apply_click_through(self, through: bool) -> None:
        """Set the pass-through bit, but only when it is not already right.

        The pointer sits still for whole seconds at a time, and this runs 25
        times a second: without the comparison it would be writing a window
        style on nearly every one of them.

        The window is looked up here rather than in ``__init__`` because Tk
        builds the toplevel that carries the style lazily — before the window is
        realised there is no such window to find, and the handle that answers is
        a different one that would take the bit and do nothing with it. A lookup
        that fails is simply dropped and tried again on the next tick, which is
        also what happens if Tk ever replaces the window underneath us.
        """
        if through is self._click_through_on:
            return
        if self._click_through_handle is None:
            self._click_through_handle = _layered_toplevel(self._root)
        if self._click_through_handle and _set_click_through(
            self._click_through_handle, through
        ):
            self._click_through_on = through
        else:
            self._click_through_handle = None

    def _was_a_click(self, event: tk.Event) -> bool:
        """End the gesture, and say whether it was a press rather than a drag.

        Every button the widget has serves both — a button is the only solid
        thing left to move the window by — so each one ends here, and a hand is
        never perfectly still, which is why the answer is a tolerance rather than
        an equality.
        """
        origin, self._drag_origin = self._drag_origin, None
        self._drag_offset = None
        return origin is None or (
            abs(event.x_root - origin[0]) <= _CLOSE_DRAG_SLOP
            and abs(event.y_root - origin[1]) <= _CLOSE_DRAG_SLOP
        )

    def _release_close(self, event: tk.Event) -> None:
        """Close, unless the press this ends was really a drag of the window."""
        if self._was_a_click(event):
            self.close()

    def _release_minimize(self, event: tk.Event) -> None:
        """Shrink to the bar, unless the press this ends was really a drag."""
        if self._was_a_click(event):
            self.minimize()

    def _release_restore(self, event: tk.Event) -> None:
        """Come back to the full face, unless this was really a drag."""
        if self._was_a_click(event):
            self.restore()

    def _start_drag(self, event: tk.Event) -> None:
        """Remember where inside the window, and on screen, the drag began."""
        self._drag_offset = (event.x, event.y)
        self._drag_origin = (event.x_root, event.y_root)

    def _end_drag(self, _event: tk.Event) -> None:
        """Forget the gesture, so the widget may stop taking clicks again.

        A press with no matching release would leave `_click_through_tick`
        believing a drag is still in progress and holding the window solid for
        good — Tk's implicit grab is what guarantees this arrives.
        """
        self._drag_offset = None
        self._drag_origin = None

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
