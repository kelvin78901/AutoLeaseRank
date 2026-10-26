"""Per-source data quality: field fill rates and staleness."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Sequence

from alr.insights.models import DEFAULT_STALE_DAYS, SourceQuality
from alr.schema import EnrichedListing


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def source_quality(
    listings: Sequence[EnrichedListing],
    stale_days: int = DEFAULT_STALE_DAYS,
    now: datetime | None = None,
) -> list[SourceQuality]:
    if stale_days < 0:
        raise ValueError("stale_days must be >= 0")
    now = datetime.now(timezone.utc) if now is None else _utc(now)
    cutoff = now - timedelta(days=stale_days)

    groups: dict[str, list[EnrichedListing]] = defaultdict(list)
    for l in listings:
        groups[l.source].append(l)

    out: list[SourceQuality] = []
    for source, rows in groups.items():
        n = len(rows)
        stale = sum(1 for l in rows if _utc(l.crawled_at) < cutoff)
        out.append(SourceQuality(
            source=source,
            count=n,
            vin_fill=round(sum(1 for l in rows if (l.vin or "").strip()) / n, 4),
            hp_fill=round(sum(1 for l in rows if l.hp > 0) / n, 4),
            state_fill=round(
                sum(1 for l in rows if (l.state or "").strip().upper() not in ("", "NA")) / n, 4),
            year_fill=round(sum(1 for l in rows if l.year > 0) / n, 4),
            stale_count=stale,
            stale_share=round(stale / n, 4),
        ))
    out.sort(key=lambda s: (-s.count, s.source))
    return out
