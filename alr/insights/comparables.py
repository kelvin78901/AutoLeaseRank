"""Nearest comparable listings.

Candidates are listings with exactly the same ``body`` string as the target. They are
ranked by Euclidean distance in (hp, effective_monthly) space, each axis scaled:

    distance(a, b) = sqrt(((a.hp - b.hp) / HP_SCALE) ** 2
                          + ((a.effective_monthly - b.effective_monthly) / EFF_SCALE) ** 2)

With both scales 100.0, 100 hp counts the same as $100/mo. Ties break on listing_key asc.
"""
from __future__ import annotations

import math
from typing import Sequence

from alr.insights.models import DEFAULT_K, Comparable
from alr.schema import EnrichedListing

HP_SCALE = 100.0
EFF_SCALE = 100.0


def distance(a: EnrichedListing, b: EnrichedListing) -> float:
    return math.hypot(
        (a.hp - b.hp) / HP_SCALE,
        (a.effective_monthly - b.effective_monthly) / EFF_SCALE,
    )


def find_comparables(
    listings: Sequence[EnrichedListing], listing_key: str, k: int = DEFAULT_K
) -> list[Comparable]:
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    target = next((l for l in listings if l.listing_key == listing_key), None)
    if target is None:
        raise KeyError(listing_key)
    scored = sorted(
        (
            (distance(target, c), c.listing_key, c)
            for c in listings
            if c.body == target.body and c.listing_key != listing_key
        ),
        key=lambda t: (t[0], t[1]),
    )
    return [
        Comparable(
            listing_key=c.listing_key,
            source=c.source,
            make=c.make,
            model=c.model,
            body=c.body,
            hp=c.hp,
            effective_monthly=c.effective_monthly,
            distance=round(d, 4),
        )
        for d, _, c in scored[:k]
    ]
