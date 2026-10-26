"""Price-band percentiles (p10/p50/p90 of effective_monthly) per make / body / state."""
from __future__ import annotations

import math
from typing import Sequence

from alr.insights.models import DEFAULT_MIN_COUNT, GROUP_BY_FIELDS, SegmentStats
from alr.schema import EnrichedListing


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (numpy "linear"); q in [0, 1]. Not rounded."""
    if len(values) == 0:
        raise ValueError("percentile of empty values")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    xs = sorted(values)
    n = len(xs)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def segment_stats(
    listings: Sequence[EnrichedListing],
    group_by: str,
    min_count: int = DEFAULT_MIN_COUNT,
) -> list[SegmentStats]:
    """Per-group count and p10/p50/p90 of effective_monthly, largest groups first."""
    if group_by not in GROUP_BY_FIELDS:
        raise ValueError(f"group_by must be one of {GROUP_BY_FIELDS}, got {group_by!r}")
    if min_count < 1:
        raise ValueError(f"min_count must be >= 1, got {min_count}")
    groups: dict[str, list[float]] = {}
    for l in listings:
        groups.setdefault(str(getattr(l, group_by)), []).append(l.effective_monthly)
    out = [
        SegmentStats(
            key=key,
            count=len(vals),
            p10=round(percentile(vals, 0.1), 2),
            p50=round(percentile(vals, 0.5), 2),
            p90=round(percentile(vals, 0.9), 2),
        )
        for key, vals in groups.items()
        if len(vals) >= min_count
    ]
    out.sort(key=lambda s: (-s.count, s.key))
    return out
