"""Token accounting shared by both CLI readers.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with total token usage and a tokens-per-second
rate.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TokenUsage",
    "claude_usage_from_record",
    "codex_usage_from_total",
    "growth",
]


@dataclass
class TokenUsage:
    """Token counts for one session, in the shape both CLIs are normalised to."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Billable total: input + output + cache creation. Cache reads excluded."""
        return self.input_tokens + self.output_tokens + self.cache_creation_tokens

    def add(self, other: "TokenUsage") -> None:
        """Accumulate ``other``'s counts into this one, field by field."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens


def growth(earlier: TokenUsage, later: TokenUsage) -> TokenUsage:
    """How much each count rose between two readings, never below zero.

    Both CLIs report token usage as a running total, so the only way to know
    what a session spent *during* some stretch of time is to subtract two
    readings of it.

    Clamped per field because these counters are read out of files another
    process rewrites, and a transcript that is truncated or replaced makes a
    count fall. A negative term would then subtract work that really happened
    from whatever is accumulating these.
    """
    return TokenUsage(
        input_tokens=max(0, later.input_tokens - earlier.input_tokens),
        output_tokens=max(0, later.output_tokens - earlier.output_tokens),
        cache_creation_tokens=max(
            0, later.cache_creation_tokens - earlier.cache_creation_tokens
        ),
        cache_read_tokens=max(0, later.cache_read_tokens - earlier.cache_read_tokens),
    )


def _count(usage: dict, key: str) -> int:
    """One token count from an untrusted record; anything non-integer reads as 0.

    These records are written by another process and may be truncated or carry a
    ``null``; an un-coerced value would raise deep inside the totals arithmetic.
    """
    value = usage.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def claude_usage_from_record(usage: dict) -> TokenUsage:
    """Normalise one Claude transcript record's ``message.usage`` mapping."""
    return TokenUsage(
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cache_creation_tokens=_count(usage, "cache_creation_input_tokens"),
        cache_read_tokens=_count(usage, "cache_read_input_tokens"),
    )


def codex_usage_from_total(total_token_usage: dict) -> TokenUsage:
    """Normalise a Codex ``total_token_usage`` mapping.

    Codex reports ``cached_input_tokens`` as a subset of ``input_tokens``, unlike
    Claude, so the cached part is subtracted back out of the input count.
    """
    cached = _count(total_token_usage, "cached_input_tokens")
    return TokenUsage(
        # Clamped: a record claiming more cached than total input would otherwise
        # yield a negative count and a negative headline total.
        input_tokens=max(0, _count(total_token_usage, "input_tokens") - cached),
        output_tokens=_count(total_token_usage, "output_tokens"),
        cache_creation_tokens=_count(total_token_usage, "cache_write_input_tokens"),
        cache_read_tokens=cached,
    )
