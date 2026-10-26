"""Price history timeline and "dropping now" list over the `history` table.
See CONTRACT.md. Only `load_history` does I/O; it never opens a connection."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from alr.insights.models import DEFAULT_DROPPING_LIMIT, HistoryPoint, PriceDrop

_COLUMNS = "crawl_ts, listing_key, effective_monthly, days_on_market, price_drops, favorites"


def load_history(con: Any, listing_key: Optional[str] = None) -> list[HistoryPoint]:
    """Rows of `history` (optionally one listing) ordered by (crawl_ts, listing_key)."""
    sql = f"SELECT {_COLUMNS} FROM history"
    params: list[str] = []
    if listing_key is not None:
        sql += " WHERE listing_key = ?"
        params.append(listing_key)
    sql += " ORDER BY crawl_ts, listing_key"
    return [
        HistoryPoint(
            crawl_ts=ts,
            listing_key=key,
            effective_monthly=eff,
            days_on_market=dom,
            price_drops=drops,
            favorites=fav,
        )
        for ts, key, eff, dom, drops, fav in con.execute(sql, params).fetchall()
    ]


def listing_timeline(rows: Sequence[HistoryPoint], listing_key: str) -> list[HistoryPoint]:
    """Points of one listing in crawl order (stable); unknown key gives []."""
    return sorted((p for p in rows if p.listing_key == listing_key), key=lambda p: p.crawl_ts)


def dropping_now(
    rows: Sequence[HistoryPoint], limit: int = DEFAULT_DROPPING_LIMIT
) -> list[PriceDrop]:
    """Listings whose latest priced row is below their previous priced row, biggest drop first."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    priced: dict[str, list[HistoryPoint]] = {}
    for p in rows:
        if p.effective_monthly is not None:
            priced.setdefault(p.listing_key, []).append(p)
    drops: list[tuple[float, str, PriceDrop]] = []
    for key, pts in priced.items():
        if len(pts) < 2:
            continue
        pts = sorted(pts, key=lambda p: p.crawl_ts)
        prev, latest = pts[-2], pts[-1]
        prev_eff, latest_eff = prev.effective_monthly, latest.effective_monthly
        if not (latest_eff < prev_eff and prev_eff > 0):
            continue
        raw = prev_eff - latest_eff
        drops.append((
            raw,
            key,
            PriceDrop(
                listing_key=key,
                previous_crawl_ts=prev.crawl_ts,
                latest_crawl_ts=latest.crawl_ts,
                previous_effective_monthly=prev_eff,
                latest_effective_monthly=latest_eff,
                drop_dollars=round(raw, 2),
                drop_pct=round(raw / prev_eff * 100, 2),
            ),
        ))
    drops.sort(key=lambda t: (-t[0], t[1]))
    return [d for _, _, d in drops[:limit]]
