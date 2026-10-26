"""The insights fixtures and models behave as alr/insights/CONTRACT.md describes."""
from datetime import datetime, timedelta, timezone

from alr.insights.models import HistoryPoint
from alr.schema import EnrichedListing


def test_listings_parse(insights_rows, insights_listings):
    assert len(insights_listings) == len(insights_rows) == 21
    assert all(isinstance(l, EnrichedListing) for l in insights_listings)
    assert len({l.listing_key for l in insights_listings}) == 21
    assert all(l.listing_key == f"{l.source}:{l.source_id}" for l in insights_listings)
    assert all(l.crawled_at.utcoffset() == timedelta(0) for l in insights_listings)


def test_history_is_aware_utc(insights_history_rows, insights_history):
    assert len(insights_history) == len(insights_history_rows) == 28
    assert all(h.crawl_ts.utcoffset() == timedelta(0) for h in insights_history)
    assert len({h.crawl_ts for h in insights_history}) == 4
    ts = [h.crawl_ts for h in insights_history]
    assert ts != sorted(ts)  # the file is deliberately not in crawl order


def test_insights_con_holds_every_history_row(insights_con, insights_history_rows):
    assert insights_con.execute("SELECT count(*) FROM history").fetchone()[0] == len(insights_history_rows)
    first = insights_history_rows[0]
    got = insights_con.execute(
        "SELECT crawl_ts FROM history WHERE listing_key = ? ORDER BY crawl_ts DESC LIMIT 1",
        [first["listing_key"]]).fetchone()[0]
    assert got.tzinfo is None
    assert got.replace(tzinfo=timezone.utc) == datetime.fromisoformat(first["crawl_ts"])


def test_history_point_converts_crawl_ts_to_utc():
    common = dict(listing_key="a", effective_monthly=None, days_on_market=None,
                  price_drops=None, favorites=None)
    naive = HistoryPoint(crawl_ts=datetime(2026, 10, 1, 12), **common)
    assert naive.crawl_ts == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    assert naive.crawl_ts.tzinfo == timezone.utc

    est = timezone(timedelta(hours=-5))
    aware = HistoryPoint(crawl_ts=datetime(2026, 10, 1, 7, tzinfo=est), **common)
    assert aware.crawl_ts.utcoffset() == timedelta(0)
    assert aware.crawl_ts.replace(tzinfo=None) == datetime(2026, 10, 1, 12)

    iso = HistoryPoint(crawl_ts="2026-10-01T14:00:00+02:00", **common)
    assert iso.crawl_ts == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
