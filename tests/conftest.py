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
