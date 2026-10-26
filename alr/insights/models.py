"""Market insights data contract: constants and the response models returned by
the analysis modules and the /insights HTTP routes. See CONTRACT.md for the
functions built on these models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, field_validator

GROUP_BY_FIELDS = ("make", "body", "state")
DEFAULT_MIN_COUNT = 1
DEFAULT_DROPPING_LIMIT = 20
MAX_DROPPING_LIMIT = 500
DEFAULT_K = 5
MAX_K = 50
DEFAULT_STALE_DAYS = 7
MAX_STALE_DAYS = 3650


class SegmentStats(BaseModel):
    """effective_monthly percentiles for one group (make / body / state value)."""
    key: str
    count: int
    p10: float
    p50: float
    p90: float


class SegmentsResponse(BaseModel):
    group_by: str
    min_count: int
    groups: list[SegmentStats]


class HistoryPoint(BaseModel):
    """One row of the `history` table (one listing seen in one crawl)."""
    crawl_ts: datetime                     # always timezone-aware UTC
    listing_key: str
    effective_monthly: Optional[float]
    days_on_market: Optional[int]
    price_drops: Optional[int]
    favorites: Optional[int]

    @field_validator("crawl_ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        # history.crawl_ts is a naive TIMESTAMP holding UTC, so naive values are
        # taken to be UTC; aware values are converted to UTC.
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class HistoryResponse(BaseModel):
    listing_key: str
    count: int
    points: list[HistoryPoint]


class PriceDrop(BaseModel):
    """A listing whose latest effective_monthly is below its previous one."""
    listing_key: str
    previous_crawl_ts: datetime
    latest_crawl_ts: datetime
    previous_effective_monthly: float
    latest_effective_monthly: float
    drop_dollars: float
    drop_pct: float


class DroppingResponse(BaseModel):
    count: int
    limit: int
    items: list[PriceDrop]


class Comparable(BaseModel):
    """A same-body listing near the target in (hp, effective_monthly) space."""
    listing_key: str
    source: str
    make: str
    model: str
    body: str
    hp: int
    effective_monthly: float
    distance: float


class ComparablesResponse(BaseModel):
    listing_key: str
    body: str
    k: int
    count: int
    comparables: list[Comparable]


class SourceQuality(BaseModel):
    """Field fill rates and staleness for one source."""
    source: str
    count: int
    vin_fill: float
    hp_fill: float
    state_fill: float
    year_fill: float
    stale_count: int
    stale_share: float


class QualityResponse(BaseModel):
    stale_days: int
    as_of: datetime
    sources: list[SourceQuality]
