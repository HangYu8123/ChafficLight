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
    tokens_per_second,
)

NOW = 1_753_700_000.0


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


def test_tokens_per_second_clamps_a_counter_reset_to_zero():
    samples = [(NOW - 20.0, 1_000), (NOW - 10.0, 300), (NOW, 900)]
    assert tokens_per_second(samples, NOW) == pytest.approx(600.0 / 20.0)


def test_tokens_per_second_is_not_last_minus_first():
    samples = [(NOW - 20.0, 1_000), (NOW - 10.0, 300), (NOW, 900)]
    rate = tokens_per_second(samples, NOW)
    assert rate == pytest.approx(30.0)
    assert rate != pytest.approx((900 - 1_000) / 20.0)


def test_tokens_per_second_divides_by_the_whole_window_not_the_sample_span():
    """A pre-window sample is a baseline for the step that lands in the window.

    The step onto 100 was *reported* at NOW-30, inside the window, so its tokens
    count even though the sample it is measured against has aged out. Both steps
    are then spread over the full 60 s, not over the 20 s their two timestamps
    happen to span.
    """
    samples = [(NOW - 120.0, 0), (NOW - 30.0, 100), (NOW - 10.0, 400)]
    assert tokens_per_second(samples, NOW) == pytest.approx(400.0 / 60.0)


def test_tokens_per_second_widening_the_window_lengthens_the_divisor():
    """Same tokens, longer window: the rate falls because more idle time counts."""
    samples = [(NOW - 120.0, 0), (NOW - 30.0, 100), (NOW - 10.0, 400)]
    assert tokens_per_second(samples, NOW, window=300) == pytest.approx(400.0 / 120.0)


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
        [(NOW - 1_000.0, 10), (NOW - 900.0, 20)],
    ],
)
def test_tokens_per_second_is_zero_without_two_in_window_samples(samples):
    assert tokens_per_second(samples, NOW) == 0.0


def test_tokens_per_second_is_zero_when_the_window_span_is_zero():
    assert tokens_per_second([(NOW, 10), (NOW, 90)], NOW) == 0.0


def test_tokens_per_second_over_a_monotonic_series():
    samples = [(NOW - 40.0, 0), (NOW - 20.0, 200), (NOW, 600)]
    assert tokens_per_second(samples, NOW) == pytest.approx(15.0)
