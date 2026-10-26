"""Watch persistence tests (in-memory DuckDB; file DB only under tmp_path)."""
from datetime import datetime, timedelta, timezone

from alr.store import db
from alr.store.watches import create_watch, delete_watch, get_watch, list_watches
from alr.watch.models import Watch, WatchCreate


def _wc(**kw) -> WatchCreate:
    base = dict(name="w", max_effective_monthly=500.0)
    base.update(kw)
    return WatchCreate(**base)


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _sequences(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT sequence_name FROM duckdb_sequences()").fetchall()}


def test_connect_creates_watches_and_existing_tables(mem_con):
    tables = _tables(mem_con)
    assert {"watches", "current", "history", "vin_cache", "feature_log"} <= tables
    assert "watch_id_seq" in _sequences(mem_con)


def test_memory_connections_are_independent(mem_con):
    other = db.connect(":memory:")
    try:
        create_watch(mem_con, _wc(name="only-here"))
        assert len(list_watches(mem_con)) == 1
        assert list_watches(other) == []
    finally:
        other.close()


def test_setup_is_idempotent_and_keeps_rows(mem_con):
    w = create_watch(mem_con, _wc(name="keep"))
    db.setup(mem_con)
    db.setup(mem_con)
    assert "watches" in _tables(mem_con)
    assert list_watches(mem_con) == [w]
    assert create_watch(mem_con, _wc()).id > w.id


def test_create_watch_returns_watch_with_roundtripped_criteria(mem_con):
    before = datetime.now(timezone.utc)
    wc = _wc(name="bmw", make="BMW", model="X5", max_effective_monthly=612.5,
             min_score=40, max_miles_per_month=1000)
    w = create_watch(mem_con, wc)
    after = datetime.now(timezone.utc)

    assert isinstance(w, Watch)
    assert isinstance(w.id, int) and w.id > 0
    assert w.name == "bmw"
    assert w.make == "BMW"
    assert w.model == "X5"
    assert w.max_effective_monthly == 612.5
    assert w.min_score == 40
    assert w.max_miles_per_month == 1000
    assert w.created_at.tzinfo is not None
    assert w.created_at.utcoffset() == timedelta(0)
    assert before - timedelta(seconds=5) <= w.created_at <= after + timedelta(seconds=5)
    assert get_watch(mem_con, w.id) == w


def test_optional_fields_roundtrip_as_none(mem_con):
    w = create_watch(mem_con, _wc(make="", model="   "))
    assert (w.make, w.model, w.max_miles_per_month) == (None, None, None)
    assert w.min_score == 0
    got = get_watch(mem_con, w.id)
    assert got == w
    assert (got.make, got.model, got.max_miles_per_month) == (None, None, None)


def test_ids_unique_increasing_and_list_ordered(mem_con):
    assert list_watches(mem_con) == []
    created = [create_watch(mem_con, _wc(name=f"w{i}")) for i in range(4)]
    ids = [w.id for w in created]
    assert len(set(ids)) == 4
    assert ids == sorted(ids)
    assert all(b > a for a, b in zip(ids, ids[1:]))
    assert [w.id for w in list_watches(mem_con)] == ids
    assert list_watches(mem_con) == created


def test_get_watch_missing_returns_none(mem_con):
    assert get_watch(mem_con, 1) is None
    w = create_watch(mem_con, _wc())
    assert get_watch(mem_con, w.id + 1000) is None
    assert get_watch(mem_con, -1) is None


def test_delete_watch_true_then_false_and_others_remain(mem_con):
    a = create_watch(mem_con, _wc(name="a"))
    b = create_watch(mem_con, _wc(name="b"))
    c = create_watch(mem_con, _wc(name="c"))

    assert delete_watch(mem_con, b.id) is True
    assert delete_watch(mem_con, b.id) is False
    assert delete_watch(mem_con, 987654) is False
    assert get_watch(mem_con, b.id) is None
    assert list_watches(mem_con) == [a, c]
    assert get_watch(mem_con, a.id) == a

    # ids of deleted watches are never reused
    d = create_watch(mem_con, _wc(name="d"))
    assert d.id > c.id


def test_connect_without_args_opens_db_path(tmp_path, monkeypatch):
    path = tmp_path / "default.duckdb"
    monkeypatch.setattr(db, "DB_PATH", path)
    con = db.connect()
    try:
        assert "watches" in _tables(con)
    finally:
        con.close()
    assert path.exists()


def test_persistence_across_connections(tmp_path):
    path = tmp_path / "w.duckdb"
    con = db.connect(path)
    try:
        w = create_watch(con, _wc(name="persist", make="Tesla", max_miles_per_month=1250))
    finally:
        con.close()

    con2 = db.connect(path)
    try:
        assert get_watch(con2, w.id) == w
        assert list_watches(con2) == [w]
        nxt = create_watch(con2, _wc(name="next"))
        assert nxt.id > w.id
    finally:
        con2.close()
