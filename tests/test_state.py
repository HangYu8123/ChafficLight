"""Frozen acceptance tests for the per-CLI state mapping rules.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running (green), waiting
for their input (yellow) or finished (red).
"""

import pytest

from cli_traffic_light.state import (
    STATE_COLORS,
    SessionState,
    _CLAUDE_STATUS_STATES,
    claude_status_to_state,
    codex_events_to_state,
)

NOW = 1_753_700_000.0


@pytest.mark.parametrize(
    "status, expected",
    [
        ("busy", SessionState.RUNNING),
        ("shell", SessionState.RUNNING),
        ("waiting", SessionState.NEEDS_INPUT),
        ("idle", SessionState.IDLE),
        ("compacting", SessionState.UNKNOWN),
        ("BUSY", SessionState.UNKNOWN),
        ("", SessionState.UNKNOWN),
    ],
)
def test_claude_status_maps_to_expected_state(status, expected):
    assert claude_status_to_state(status) == expected


def test_waiting_is_the_only_claude_status_that_needs_input():
    """Yellow means the agent asked something — nothing else may claim it.

    Asserted against the mapping table rather than a list of statuses spelled
    out here, so a *future* status wired to NEEDS_INPUT fails this too: the
    guarantee is "exactly one status is yellow", not "these five are not".
    """
    assert claude_status_to_state("waiting") == SessionState.NEEDS_INPUT
    yellow = [
        status
        for status, state in _CLAUDE_STATUS_STATES.items()
        if state is SessionState.NEEDS_INPUT
    ]
    assert yellow == ["waiting"]


def test_every_state_has_a_colour():
    """The GUI indexes ``STATE_COLORS`` by state, so a gap is a repaint crash."""
    assert set(STATE_COLORS) == set(SessionState)


def test_each_signal_colour_means_what_the_light_says():
    """The three lamps are a promise about what the user has to do.

    Spelled out as literal hex rather than compared to `STATE_COLORS` itself,
    because the requirement *is* the specific colour: an assertion that reads
    the value it is checking would hold whatever the table said.
    """
    assert STATE_COLORS[SessionState.RUNNING] == "#2ecc40"      # moving, nothing wanted
    assert STATE_COLORS[SessionState.NEEDS_INPUT] == "#ffdc00"  # your attention, answer it
    assert STATE_COLORS[SessionState.IDLE] == "#c2564e"         # stopped, awaiting a prompt


def test_states_that_are_not_a_signal_do_not_borrow_a_signal_colour():
    """A session that ended, or that this build cannot read, is not one of the three.

    Sharing a colour would make the count on a lamp mean two different things.
    """
    signals = {
        STATE_COLORS[state]
        for state in (SessionState.RUNNING, SessionState.NEEDS_INPUT, SessionState.IDLE)
    }
    assert len(signals) == 3
    for state in (SessionState.FINISHED, SessionState.UNKNOWN):
        assert STATE_COLORS[state] not in signals


def test_idle_claude_status_is_idle_not_needs_input_or_finished():
    result = claude_status_to_state("idle")
    assert result == SessionState.IDLE
    assert result != SessionState.NEEDS_INPUT
    assert result != SessionState.FINISHED


def test_unrecognised_claude_status_is_unknown_not_needs_input():
    result = claude_status_to_state("waiting_for_approval")
    assert result == SessionState.UNKNOWN
    assert result != SessionState.NEEDS_INPUT


def test_shell_status_is_running_not_idle():
    """``shell`` is a finished turn with a background command still executing."""
    result = claude_status_to_state("shell")
    assert result == SessionState.RUNNING
    assert result != SessionState.IDLE


@pytest.mark.parametrize(
    "last_turn_event, age_seconds, expected",
    [
        ("task_started", 0.0, SessionState.RUNNING),
        ("task_started", 899.0, SessionState.RUNNING),
        ("task_complete", 10.0, SessionState.IDLE),
        ("turn_aborted", 10.0, SessionState.IDLE),
        ("task_complete", 899.0, SessionState.IDLE),
        ("task_complete", 901.0, SessionState.FINISHED),
        ("turn_aborted", 3600.0, SessionState.FINISHED),
        (None, 10.0, SessionState.UNKNOWN),
        ("token_count", 10.0, SessionState.UNKNOWN),
        (None, 901.0, SessionState.FINISHED),
    ],
)
def test_codex_last_event_and_mtime_map_to_expected_state(
    last_turn_event, age_seconds, expected
):
    assert codex_events_to_state(last_turn_event, NOW - age_seconds, NOW) == expected


def test_codex_turn_aborted_is_idle_not_finished_while_fresh():
    """A cancelled turn leaves the session alive and free, not gone."""
    result = codex_events_to_state("turn_aborted", NOW - 5.0, NOW)
    assert result == SessionState.IDLE
    assert result != SessionState.FINISHED


def test_no_codex_event_can_ever_need_input():
    """Codex never persists its approval prompts, so it has no red signal."""
    for event in ("task_started", "task_complete", "turn_aborted", "token_count", None):
        for age in (10.0, 901.0):
            state = codex_events_to_state(event, NOW - age, NOW)
            assert state != SessionState.NEEDS_INPUT


def test_codex_task_started_with_stale_mtime_is_finished():
    fresh = codex_events_to_state("task_started", NOW - 5.0, NOW)
    stale = codex_events_to_state("task_started", NOW - 901.0, NOW)
    assert fresh == SessionState.RUNNING
    assert stale == SessionState.FINISHED


def test_codex_staleness_boundary_is_exclusive_on_both_sides():
    assert codex_events_to_state("task_started", NOW - 899.0, NOW) == SessionState.RUNNING
    assert codex_events_to_state("task_started", NOW - 901.0, NOW) == SessionState.FINISHED
