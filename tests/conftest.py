"""Shared offline fixtures. Listings come from tests/fixtures/watch_listings.json
(13 leasehackr rows, 5 makes); DuckDB is always in-memory, never data/."""
import json
from pathlib import Path

import pytest

from alr.schema import EnrichedListing, ScoredListing

WATCH_LISTINGS = Path(__file__).parent / "fixtures" / "watch_listings.json"


@pytest.fixture
def watch_rows() -> list[dict]:
    return json.loads(WATCH_LISTINGS.read_text())


@pytest.fixture
def enriched_listings(watch_rows) -> list[EnrichedListing]:
    return [EnrichedListing(**r) for r in watch_rows]


@pytest.fixture
def scored_listings(watch_rows) -> list[ScoredListing]:
    return [ScoredListing(**r) for r in watch_rows]


@pytest.fixture
def mem_con():
    # Imported here so collection doesn't depend on the store task having landed.
    from alr.store import db
    con = db.connect(":memory:")
    try:
        yield con
    finally:
        con.close()


# --- market insights (alr/insights; see alr/insights/CONTRACT.md) ---
from datetime import datetime, timezone  # noqa: E402

INSIGHTS_LISTINGS = Path(__file__).parent / "fixtures" / "insights_listings.json"
INSIGHTS_HISTORY = Path(__file__).parent / "fixtures" / "insights_history.json"


@pytest.fixture
def insights_rows() -> list[dict]:
    return json.loads(INSIGHTS_LISTINGS.read_text())


@pytest.fixture
def insights_listings(insights_rows) -> list[EnrichedListing]:
    return [EnrichedListing(**r) for r in insights_rows]


@pytest.fixture
def insights_history_rows() -> list[dict]:
    return json.loads(INSIGHTS_HISTORY.read_text())


@pytest.fixture
def insights_history(insights_history_rows):
    # Imported here so collection doesn't depend on the insights package.
    from alr.insights.models import HistoryPoint
    return [HistoryPoint(**h) for h in insights_history_rows]


@pytest.fixture
def insights_now() -> datetime:
    return datetime(2026, 10, 15, tzinfo=timezone.utc)


@pytest.fixture
def insights_con(mem_con, insights_history_rows):
    """mem_con with every insights_history.json row in `history` (file order).
    crawl_ts is stored as naive UTC, matching the TIMESTAMP column."""
    rows = []
    for h in insights_history_rows:
        ts = datetime.fromisoformat(h["crawl_ts"]).astimezone(timezone.utc).replace(tzinfo=None)
        rows.append((ts, h["listing_key"], h["effective_monthly"], h["days_on_market"],
                     h["price_drops"], h["favorites"]))
    mem_con.executemany("INSERT INTO history VALUES (?, ?, ?, ?, ?, ?)", rows)
    return mem_con
