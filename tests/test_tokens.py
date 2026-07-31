"""Frozen acceptance tests for token accounting and the tokens/sec rate.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, its total token usage and how fast it
is currently burning tokens.
"""

import pytest

from cli_traffic_light.tokens import (
    TokenUsage,
    claude_usage_from_record,
    codex_usage_from_total,
    growth,
    tokens_per_second,
)

NOW = 1_753_700_000.0


def test_growth_reports_the_rise_in_every_count():
    rise = growth(TokenUsage(10, 5, 2, 1), TokenUsage(30, 6, 2, 100))
    assert (rise.input_tokens, rise.output_tokens) == (20, 1)
    assert (rise.cache_creation_tokens, rise.cache_read_tokens) == (0, 99)
    assert rise.total_tokens == 21


def test_growth_clamps_each_count_rather_than_the_total():
    """A rewritten transcript makes a cumulative count fall.

    Clamped per field, so a count that dropped contributes zero instead of
    cancelling out a different one that really rose — which is what clamping the
    billable total alone would do.
    """
    rise = growth(TokenUsage(100, 5, 0, 0), TokenUsage(0, 9, 0, 0))
    assert rise.input_tokens == 0
    assert rise.output_tokens == 4
    assert rise.total_tokens == 4


def test_growth_leaves_both_readings_untouched():
    """One of them is the running total the monitor keeps between snapshots."""
    earlier, later = TokenUsage(1, 2, 3, 4), TokenUsage(5, 6, 7, 8)
    growth(earlier, later)
    assert earlier == TokenUsage(1, 2, 3, 4)
    assert later == TokenUsage(5, 6, 7, 8)


@pytest.mark.parametrize(
    "input_tokens, output_tokens, cache_creation, cache_read, expected",
    [
        (10, 5, 2, 0, 17),
        (0, 0, 0, 50_000, 0),
        (100, 200, 300, 400, 600),
    ],
)
def test_total_tokens_excludes_cache_reads(
    input_tokens, output_tokens, cache_creation, cache_read, expected
):
    usage = TokenUsage(input_tokens, output_tokens, cache_creation, cache_read)
    assert usage.total_tokens == expected


def test_claude_record_with_dominant_cache_read_excludes_it_from_the_total():
    usage = claude_usage_from_record(
        {
            "input_tokens": 12,
            "output_tokens": 7,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 24_000,
        }
    )
    assert usage.input_tokens == 12
    assert usage.output_tokens == 7
    assert usage.cache_creation_tokens == 5
    assert usage.cache_read_tokens == 24_000
    assert usage.total_tokens == 24


def test_claude_record_missing_fields_default_to_zero():
    usage = claude_usage_from_record({"input_tokens": 9})
    assert usage.input_tokens == 9
    assert usage.output_tokens == 0
    assert usage.cache_creation_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.total_tokens == 9


def test_codex_total_treats_cached_input_as_a_subset_of_input():
    usage = codex_usage_from_total(
        {
            "input_tokens": 18_372,
            "cached_input_tokens": 17_152,
            "output_tokens": 150,
            "cache_write_input_tokens": 0,
            "total_tokens": 18_522,
        }
    )
    assert usage.input_tokens == 1_220
    assert usage.output_tokens == 150
    assert usage.cache_creation_tokens == 0
    assert usage.cache_read_tokens == 17_152
    assert usage.total_tokens == 1_370


def test_codex_total_counts_cache_writes_as_cache_creation():
    usage = codex_usage_from_total(
        {
            "input_tokens": 1_000,
            "cached_input_tokens": 400,
            "output_tokens": 50,
            "cache_write_input_tokens": 300,
            "total_tokens": 1_050,
        }
    )
    assert usage.input_tokens == 600
    assert usage.cache_creation_tokens == 300
    assert usage.cache_read_tokens == 400
    assert usage.total_tokens == 950


def test_tokens_per_second_is_only_the_newest_step():
    """The request this froze: the rate must read now, not the last minute.

    Two steps, and only the newest one counts — 300 tokens over the 30 s since
    the step began. Averaging both in would drag in the 100 from two minutes ago
    and report 400/110 instead.
    """
    samples = [(NOW - 120.0, 0), (NOW - 30.0, 100), (NOW - 10.0, 400)]
    assert tokens_per_second(samples, NOW) == pytest.approx(300.0 / 30.0)


def test_tokens_per_second_ignores_history_however_long():
    """Everything before the newest step is irrelevant, so it cannot move the rate."""
    newest = [(NOW - 30.0, 100), (NOW - 10.0, 400)]
    assert tokens_per_second([(NOW - 5_000.0, 0)] + newest, NOW) == pytest.approx(
        tokens_per_second(newest, NOW)
    )


def test_tokens_per_second_skips_a_counter_reset_to_reach_the_real_step():
    """A reset is a step that lost tokens; it is skipped, not counted as zero.

    The counters reset on session resume and context compaction, and a reset
    landing as the newest step would otherwise blank the rate of a session that
    is still running.
    """
    samples = [(NOW - 20.0, 0), (NOW - 10.0, 1_000), (NOW, 300)]
    assert tokens_per_second(samples, NOW) == pytest.approx(1_000.0 / 20.0)


def test_tokens_per_second_is_not_last_minus_first():
    samples = [(NOW - 20.0, 1_000), (NOW - 10.0, 300), (NOW, 900)]
    rate = tokens_per_second(samples, NOW)
    assert rate == pytest.approx(600.0 / 10.0)
    assert rate != pytest.approx((900 - 1_000) / 20.0)


def test_tokens_per_second_uses_the_step_span_when_it_ends_ahead_of_now():
    """The CLI's clock can read a shade ahead of ours; the step still counts.

    Dividing by ``now - start`` alone would report a rate for a step that has
    not finished yet, or a negative span — for the one step the widget most
    needs to show, the one that just landed.
    """
    assert tokens_per_second([(NOW - 2.0, 0), (NOW + 1.0, 300)], NOW) == pytest.approx(100.0)


def test_tokens_per_second_decays_as_a_finished_burst_recedes():
    """The bug this guards: a burst held its peak rate until it aged out.

    All the tokens arrive in one 2 s burst, which is then over. Dividing by the
    burst's own span reports the speed it was produced at — 2,500 tok/s — no
    matter how long ago it stopped, so an idle session reads as the busiest one
    on the face. The rate must fall as the silence after it grows.
    """
    burst = [(NOW - 50.0, 0), (NOW - 48.0, 5_000)]
    just_over = tokens_per_second(burst, NOW - 47.0)
    long_over = tokens_per_second(burst, NOW)
    assert just_over == pytest.approx(5_000.0 / 3.0)
    assert long_over == pytest.approx(5_000.0 / 50.0)
    assert long_over < just_over


def test_tokens_per_second_counts_silence_since_the_last_sample():
    """Two sessions that burned the same tokens differ by how long ago they stopped."""
    still_going = [(NOW - 30.0, 0), (NOW - 1.0, 3_000)]
    stopped = [(NOW - 30.0, 0), (NOW - 25.0, 3_000)]
    assert tokens_per_second(still_going, NOW) == pytest.approx(100.0)
    assert tokens_per_second(stopped, NOW) == pytest.approx(100.0)
    # Same divisor — both opened at NOW-30 — so the *totals* match, and it is the
    # continuing silence that separates them as it lengthens.
    assert tokens_per_second(stopped, NOW + 30.0) == pytest.approx(3_000.0 / 60.0)


@pytest.mark.parametrize(
    "samples",
    [
        [],
        [(NOW, 500)],
        [(NOW - 20.0, 500), (NOW - 10.0, 500), (NOW, 500)],
    ],
)
def test_tokens_per_second_is_zero_without_a_step_that_gained(samples):
    assert tokens_per_second(samples, NOW) == 0.0


def test_tokens_per_second_is_zero_once_the_step_has_gone_quiet():
    """The bug this guards: an idle session kept reporting a rate for hours.

    Dividing by the growing silence decays but never arrives, so a burst that
    ended half an hour ago still read about 1 tok/s — and the face sums every
    session on it, so several of those never let it settle at zero.
    """
    burst = [(NOW - 32.0, 0), (NOW - 30.0, 2_000)]
    assert tokens_per_second(burst, NOW) > 0.0
    assert tokens_per_second(burst, NOW + 1_800.0) == 0.0


def test_tokens_per_second_survives_the_gap_between_two_billed_records():
    """A turn in flight must not blink to zero between records.

    Neither CLI bills while a message is in flight, so the newest step can be
    tens of seconds old on a session that is very much running. The cutoff sits
    past that gap, not inside it.
    """
    assert tokens_per_second([(NOW - 40.0, 0), (NOW - 30.0, 900)], NOW) > 0.0


def test_tokens_per_second_is_zero_when_the_step_span_is_zero():
    assert tokens_per_second([(NOW, 10), (NOW, 90)], NOW) == 0.0


def test_tokens_per_second_over_a_monotonic_series():
    samples = [(NOW - 40.0, 0), (NOW - 20.0, 200), (NOW, 600)]
    assert tokens_per_second(samples, NOW) == pytest.approx(20.0)
