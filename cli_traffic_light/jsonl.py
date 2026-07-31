"""Reading the JSONL transcript/rollout files both CLIs append to, and their timestamps.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions, reading the state the CLIs themselves keep on disk; both keep
it in JSONL files, so the shared reading helpers live here.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterator

__all__ = ["parse_iso", "read_jsonl"]

#: Above this, a bare number is milliseconds rather than seconds: as seconds it
#: would be a date past the year 5000.
_MILLISECOND_THRESHOLD = 1e11


def parse_iso(stamp: str | int | float | None) -> float | None:
    """Epoch seconds for a timestamp field, or ``None`` if it is unusable.

    The CLIs write these fields either as ISO-8601 strings or as bare epoch
    numbers — Claude's own ``sessions/<pid>.json`` uses milliseconds — so both
    shapes are accepted.
    """
    if isinstance(stamp, bool) or not stamp:
        return None
    if isinstance(stamp, (int, float)):
        return stamp / 1000 if stamp > _MILLISECOND_THRESHOLD else float(stamp)
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_jsonl(
    path: Path, *, only_lines_with: str | tuple[str, ...] | None = None
) -> Iterator[dict]:
    """Yield the JSON objects in ``path``.

    Both CLIs append to these files while we read them, so a half-written final
    line is expected rather than exceptional and is skipped.

    ``only_lines_with`` skips any line containing none of those literals without
    parsing it. These transcripts run to tens of megabytes of records a caller
    that wants one field will discard, and JSON-parsing all of them is what made
    a snapshot slow; the substrings are a pre-filter, never the actual test. A
    tuple matches any of its members, for callers that need two kinds of record
    out of one pass.
    """
    wanted = (only_lines_with,) if isinstance(only_lines_with, str) else only_lines_with
    try:
        # errors="replace": a half-written final line can end mid multi-byte
        # character, and a UnicodeDecodeError there would abort the whole read.
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        if wanted is not None and not any(literal in line for literal in wanted):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record
