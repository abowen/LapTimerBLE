"""Formatting helpers for lap and race times."""

from __future__ import annotations


def format_lap(seconds: float) -> str:
    """Format a lap as ``ss.ms`` (seconds.milliseconds).

    Times >= 60 s are still shown in seconds with millisecond precision so that
    raw lap durations round-trip; the race-clock formatter is the place that
    uses minute granularity.
    """
    if seconds < 0:
        seconds = 0.0
    return f"{seconds:0.3f}"


def format_race(seconds: float) -> str:
    """Format a race time as ``mm:ss.ms``."""
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes:02d}:{rem:06.3f}"
