"""Token accounting for both CLIs and the sliding-window tokens/sec rate.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running, waiting for
their input or finished, along with total token usage and a tokens-per-second
rate.

``RATE_WINDOW_SECONDS`` lives here rather than in ``state`` because
``tokens_per_second`` needs it as a default argument and ``state`` already
imports from this module.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "RATE_WINDOW_SECONDS",
    "TokenUsage",
    "claude_usage_from_record",
    "codex_usage_from_total",
    "tokens_per_second",
]

RATE_WINDOW_SECONDS = 60


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


def tokens_per_second(
    samples: list[tuple[float, int]],
    now: float,
    window: int = RATE_WINDOW_SECONDS,
) -> float:
    """Rate over the trailing ``window`` seconds of cumulative-total samples.

    ``samples`` is ``[(epoch_seconds, cumulative_total_tokens), ...]`` in ascending
    time order. The rate is the sum of the per-step deltas clamped to be
    non-negative (so a counter reset contributes zero, not a negative number)
    divided by the time span the in-window samples actually cover.
    """
    recent = [(t, total) for t, total in samples if t >= now - window]
    if len(recent) < 2:
        return 0.0
    span = recent[-1][0] - recent[0][0]
    if span <= 0:
        return 0.0
    gained = sum(
        max(0, later - earlier)
        for (_, earlier), (_, later) in zip(recent, recent[1:])
    )
    return gained / span
