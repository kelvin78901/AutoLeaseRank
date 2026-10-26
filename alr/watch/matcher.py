"""Pure watch matcher over the scored snapshot (see alr/watch/CONTRACT.md)."""
from __future__ import annotations

from alr.rank.pipeline import Prefs, rank
from alr.schema import EnrichedListing, ScoredListing
from alr.watch.models import DEFAULT_MATCH_LIMIT, WatchCreate


def scored_snapshot(listings: list[EnrichedListing], scorer=None) -> list[ScoredListing]:
    """Score the whole snapshot the way /top_deals does (no user preferences)."""
    prefs = Prefs(budget=10**9, max_months=10**6, top_k=len(listings) or 1)
    return rank(listings, prefs, ltr_scorer=scorer).ranked


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def matches(watch: WatchCreate, listing: ScoredListing) -> bool:
    """True iff every criterion set on the watch holds for the listing (bounds inclusive)."""
    if watch.make is not None and not _same(watch.make, listing.make or ""):
        return False
    if watch.model is not None and not _same(watch.model, listing.model or ""):
        return False
    if listing.effective_monthly > watch.max_effective_monthly:
        return False
    if listing.personalized_score < watch.min_score:
        return False
    if (watch.max_miles_per_month is not None
            and listing.miles_per_month > watch.max_miles_per_month):
        return False
    return True


def match_watch(watch: WatchCreate, scored: list[ScoredListing],
                limit: int = DEFAULT_MATCH_LIMIT) -> list[ScoredListing]:
    """Matching listings, best score first (ties by listing_key), at most `limit`."""
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    hits = [l for l in scored if matches(watch, l)]
    hits.sort(key=lambda l: (-l.personalized_score, l.listing_key))
    return hits[:limit]
