"""Frozen acceptance tests for the per-CLI state mapping rules.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running (green), waiting
for their input (yellow) or finished (red).
"""

import pytest

from cli_traffic_light.state import (
    SessionState,
    claude_status_to_state,
    codex_events_to_state,
)

NOW = 1_753_700_000.0


@pytest.mark.parametrize(
    "status, expected",
    [
        ("busy", SessionState.RUNNING),
        ("shell", SessionState.RUNNING),
        ("idle", SessionState.NEEDS_INPUT),
        ("compacting", SessionState.UNKNOWN),
        ("BUSY", SessionState.UNKNOWN),
        ("", SessionState.UNKNOWN),
    ],
)
def test_claude_status_maps_to_expected_state(status, expected):
    assert claude_status_to_state(status) == expected


def test_unrecognised_claude_status_is_unknown_not_needs_input():
    result = claude_status_to_state("waiting_for_approval")
    assert result == SessionState.UNKNOWN
    assert result != SessionState.NEEDS_INPUT


def test_shell_status_is_running_not_needs_input():
    result = claude_status_to_state("shell")
    assert result == SessionState.RUNNING
    assert result != SessionState.NEEDS_INPUT


@pytest.mark.parametrize(
    "last_turn_event, age_seconds, expected",
    [
        ("task_started", 0.0, SessionState.RUNNING),
        ("task_started", 899.0, SessionState.RUNNING),
        ("task_complete", 10.0, SessionState.NEEDS_INPUT),
        ("turn_aborted", 10.0, SessionState.NEEDS_INPUT),
        ("task_complete", 899.0, SessionState.NEEDS_INPUT),
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


def test_codex_turn_aborted_is_needs_input_not_finished():
    result = codex_events_to_state("turn_aborted", NOW - 5.0, NOW)
    assert result == SessionState.NEEDS_INPUT
    assert result != SessionState.FINISHED


def test_codex_task_started_with_stale_mtime_is_finished():
    fresh = codex_events_to_state("task_started", NOW - 5.0, NOW)
    stale = codex_events_to_state("task_started", NOW - 901.0, NOW)
    assert fresh == SessionState.RUNNING
    assert stale == SessionState.FINISHED


def test_codex_staleness_boundary_is_exclusive_on_both_sides():
    assert codex_events_to_state("task_started", NOW - 899.0, NOW) == SessionState.RUNNING
    assert codex_events_to_state("task_started", NOW - 901.0, NOW) == SessionState.FINISHED
