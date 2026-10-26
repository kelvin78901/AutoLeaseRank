"""CRUD for saved searches (watches) in the `watches` table."""
from __future__ import annotations

import duckdb

from ..watch.models import Watch, WatchCreate

_COLS = ("id, name, make, model, max_effective_monthly, min_score, "
         "max_miles_per_month, created_at AT TIME ZONE 'UTC'")
# created_at is read as naive UTC (and written via now() in SQL): binding or
# returning tz-aware datetimes would need pytz, and Watch treats naive as UTC.


def _to_watch(row: tuple) -> Watch:
    return Watch(id=row[0], name=row[1], make=row[2], model=row[3],
                 max_effective_monthly=row[4], min_score=row[5],
                 max_miles_per_month=row[6], created_at=row[7])


def create_watch(con: duckdb.DuckDBPyConnection, wc: WatchCreate) -> Watch:
    """Insert a watch (id from watch_id_seq, created_at = now UTC) and return it."""
    row = con.execute(
        "INSERT INTO watches (name, make, model, max_effective_monthly, min_score, "
        "max_miles_per_month, created_at) VALUES (?, ?, ?, ?, ?, ?, now()) "
        f"RETURNING {_COLS}",
        [wc.name, wc.make, wc.model, wc.max_effective_monthly, wc.min_score,
         wc.max_miles_per_month]).fetchone()
    return _to_watch(row)


def list_watches(con: duckdb.DuckDBPyConnection) -> list[Watch]:
    rows = con.execute(f"SELECT {_COLS} FROM watches ORDER BY id").fetchall()
    return [_to_watch(r) for r in rows]


def get_watch(con: duckdb.DuckDBPyConnection, watch_id: int) -> Watch | None:
    row = con.execute(f"SELECT {_COLS} FROM watches WHERE id = ?", [watch_id]).fetchone()
    return _to_watch(row) if row else None


def delete_watch(con: duckdb.DuckDBPyConnection, watch_id: int) -> bool:
    """True if a row existed and was deleted."""
    return bool(con.execute("DELETE FROM watches WHERE id = ? RETURNING id",
                            [watch_id]).fetchall())
