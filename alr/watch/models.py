"""Watch (saved search) data contract. A watch is a named set of criteria
matched against the whole-snapshot scored listings; see CONTRACT.md for the
store / matcher / API interfaces built on these models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints, field_validator

DEFAULT_MATCH_LIMIT = 50
MAX_MATCH_LIMIT = 500


class WatchCreate(BaseModel):
    """What a client submits. Unset criteria (None) do not filter."""
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    make: Optional[str] = None             # case-insensitive equality with listing.make
    model: Optional[str] = None            # case-insensitive equality with listing.model
    max_effective_monthly: float = Field(gt=0, allow_inf_nan=False)
    min_score: int = Field(0, ge=0, le=99)  # vs ScoredListing.personalized_score
    max_miles_per_month: Optional[int] = Field(None, gt=0)

    @field_validator("make", "model", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        # "" / "   " mean "any", same as omitting the field.
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class Watch(WatchCreate):
    """A persisted watch."""
    id: int
    created_at: datetime                   # always timezone-aware UTC

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        # Naive values are taken to be UTC; aware values (e.g. DuckDB TIMESTAMPTZ
        # in the session time zone) are converted to UTC.
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
