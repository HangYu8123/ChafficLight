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
)


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
