# Watches (saved searches) — interface contract

A *watch* is a named set of criteria (make, model, max effective $/mo, min score,
max miles/month) that is matched against the current snapshot after it has been
scored exactly the way `/top_deals` scores it. This document is the single source of
truth for the store, matcher and HTTP tasks; names, signatures and semantics below are
binding. Sections marked *(clarification)* add precision but do not change anything.

## Models (alr/watch/models.py; owner: watch_contract — already landed)

```python
DEFAULT_MATCH_LIMIT = 50
MAX_MATCH_LIMIT = 500

class WatchCreate(BaseModel):
    name: str                       # whitespace-stripped, 1..100 chars after strip
    make: Optional[str] = None      # stripped; "" / whitespace-only -> None
    model: Optional[str] = None     # stripped; "" / whitespace-only -> None
    max_effective_monthly: float    # required, > 0
    min_score: int = 0              # 0 <= x <= 99
    max_miles_per_month: Optional[int] = None   # if set, > 0

class Watch(WatchCreate):
    id: int
    created_at: datetime            # timezone-aware UTC; naive input is taken as UTC,
                                    # aware input is converted to UTC
```

Invalid input raises `pydantic.ValidationError` (HTTP: 422). Rejected examples:
`name=""`, `name="   "`, `name` > 100 chars, missing `max_effective_monthly`,
`max_effective_monthly <= 0`, `min_score < 0`, `min_score > 99`, `max_miles_per_month <= 0`.
Extra keys are ignored (pydantic default). `make`/`model` keep the client's case; case
folding happens only in the matcher.

## Store (alr/store/db.py + alr/store/watches.py; owner: watch_store)
- `db.setup(con) -> None`: runs every CREATE TABLE/SEQUENCE IF NOT EXISTS (existing tables
  current/history/vin_cache/feature_log, plus new `watch_id_seq` sequence and `watches` table).
- `db.connect(path: str | os.PathLike | None = None)`: `duckdb.connect(str(path or DB_PATH))`,
  then `setup(con)`, return con. `db.connect(":memory:")` gives a fresh in-memory DB.
  Existing no-arg callers keep working unchanged.
- `watches` table: id BIGINT PRIMARY KEY DEFAULT nextval('watch_id_seq'), name VARCHAR NOT NULL,
  make VARCHAR, model VARCHAR, max_effective_monthly DOUBLE NOT NULL, min_score INTEGER NOT NULL,
  max_miles_per_month INTEGER, created_at TIMESTAMPTZ NOT NULL.
- alr/store/watches.py:
  `create_watch(con, wc: WatchCreate) -> Watch` (created_at = now UTC; id from sequence)
  `list_watches(con) -> list[Watch]` ordered by id ascending
  `get_watch(con, watch_id: int) -> Watch | None`
  `delete_watch(con, watch_id: int) -> bool` (True if a row was deleted)

*(clarification)*
- `setup` is idempotent: calling it twice on the same connection is a no-op and must not
  raise. `CREATE SEQUENCE IF NOT EXISTS watch_id_seq` runs before the `watches` table.
- `create_watch` persists the already-validated/normalised `wc` fields as-is and returns
  the stored row as a `Watch` (use `INSERT ... RETURNING` or re-read by id). Ids are
  strictly increasing; ids of deleted watches are never reused within a DB.
- `get_watch` / `delete_watch` on an unknown id return `None` / `False`; they never raise.
- `Watch.created_at` read back from DuckDB is converted to UTC by the model validator, so
  the store may pass the TIMESTAMPTZ value straight through.
- The store does no matching and imports nothing from `alr.watch.matcher` or `alr.api`.

## Matcher (alr/watch/matcher.py; owner: watch_matcher)
- "score" means `ScoredListing.personalized_score` (what /top_deals sorts by).
- `scored_snapshot(listings: list[EnrichedListing], scorer=None) -> list[ScoredListing]`:
  `rank(listings, Prefs(budget=10**9, max_months=10**6, top_k=len(listings) or 1), ltr_scorer=scorer).ranked`
  (same whole-snapshot scoring as alr/api/main.py `_scored_in_context`).
- `matches(watch: WatchCreate, scored: list[ScoredListing]) -> bool`: True iff every set criterion holds:
  make (case-insensitive, whitespace-stripped equality with listing.make) if set;
  model (same rule against listing.model) if set; effective_monthly <= max_effective_monthly;
  personalized_score >= min_score; miles_per_month <= max_miles_per_month if set.
  (signature: `matches(watch, listing: ScoredListing) -> bool`)
- `match_watch(watch: WatchCreate, scored: list[ScoredListing], limit: int = DEFAULT_MATCH_LIMIT) -> list[ScoredListing]`:
  filter with `matches`, sort by personalized_score desc then listing_key asc, return first `limit`.
  Raise ValueError if limit < 1. Pure function, does not mutate inputs.

*(clarification)*
- The authoritative `matches` signature is `matches(watch: WatchCreate, listing: ScoredListing) -> bool`
  (one listing). `Watch` is a subclass of `WatchCreate`, so both are accepted everywhere.
- "Case-insensitive, whitespace-stripped equality" = `a.strip().casefold() == b.strip().casefold()`.
  No substring / prefix matching: make `"BMW"` does not match `"BMW M"`, model `"Model"`
  does not match `"Model 3"`.
- All bounds are inclusive (`<=`, `>=`). Unset (`None`) criteria do not filter; `min_score=0`
  never filters.
- `match_watch` returns the same `ScoredListing` objects it was given (no copies needed,
  no field changes); it does not re-assign `rank`. `limit > len(matches)` returns all matches.
  `MAX_MATCH_LIMIT` is enforced by the HTTP layer, not by `match_watch`.
- `scored_snapshot([])` returns `[]`. It does not mutate the input listings (`rank` builds
  new `ScoredListing`s).
- Allowed imports: `alr.schema`, `alr.rank.pipeline` (`Prefs`, `rank`), `alr.watch.models`.
  No DB, no FastAPI.

## HTTP API (alr/api/watch.py, mounted in alr/api/main.py; owner: watch_api)
- `router = APIRouter()`; dependencies `get_con()` (generator: yields `db.connect()`, closes after)
  and `get_snapshot() -> tuple[list[EnrichedListing], scorer]` (lazily `from .main import S`,
  returns `(S.listings, S.scorer)`). Tests override both via `app.dependency_overrides`.
- `install_error_handlers(app)`: for requests whose path starts with "/watch", HTTPException and
  RequestValidationError render as JSON `{"error": "<message>"}` with the original status code;
  all other paths delegate to FastAPI's default handlers (fastapi.exception_handlers).
- POST /watch, body WatchCreate -> 201, Watch JSON
- GET /watch -> 200, list of Watch (id asc)
- GET /watch/{watch_id} -> 200 Watch | 404 {"error": "watch <id> not found"}
- DELETE /watch/{watch_id} -> 204 empty body | 404 {"error": ...}
- GET /watch/{watch_id}/matches?limit=N (default DEFAULT_MATCH_LIMIT, 1..MAX_MATCH_LIMIT, else 422)
  -> 200 {"watch": Watch, "count": <int>, "matches": [ScoredListing dicts]} | 404
- Path param name is exactly `watch_id: int`. Router is included in main.py BEFORE the static
  `app.mount("/", ...)`, and `install_error_handlers(app)` is called in main.py.

*(clarification)*
- Every 404 on these routes uses the exact message `"watch <id> not found"` (e.g.
  `{"error": "watch 7 not found"}`), for GET, DELETE and /matches alike.
- 422 bodies are exactly one key, `{"error": <str>}`; the string is a human-readable summary
  of the validation errors (e.g. `"body.min_score: Input should be less than or equal to 99"`).
  Tests assert only the status, the single `error` key and that its value is a non-empty str.
  Non-integer `watch_id` (e.g. `/watch/abc`) is a 422 in the same shape.
- `limit` is declared as `Query(DEFAULT_MATCH_LIMIT, ge=1, le=MAX_MATCH_LIMIT)`.
- `/matches` flow: `get_watch` -> 404 if None; `scored = scored_snapshot(listings, scorer)`;
  `hits = match_watch(watch, scored, limit)`; respond `{"watch": watch.model_dump(mode="json"),
  "count": len(hits), "matches": [h.model_dump(mode="json") for h in hits]}`.
  `count` is the number of returned matches (i.e. after `limit`), so `count == len(matches)`.
- DELETE returns `Response(status_code=204)` (no body, no JSON `null`).
- `Watch.created_at` is serialised by pydantic as ISO-8601 UTC; clients/tests parse it with
  `datetime.fromisoformat` and must not compare the raw string format.
- Non-`/watch` paths keep FastAPI's default error bodies (`{"detail": ...}`), e.g.
  `GET /listing/nope` stays `{"detail": "no listing nope"}`.

## Shared test fixtures (tests/conftest.py; owner: watch_contract — already landed)

| fixture | value |
|---|---|
| `watch_rows` | raw `list[dict]` from `tests/fixtures/watch_listings.json` (fresh per test) |
| `enriched_listings` | `[EnrichedListing(**r) for r in watch_rows]` |
| `scored_listings` | `[ScoredListing(**r) for r in watch_rows]` |
| `mem_con` | `db.connect(":memory:")` (imports `alr.store.db` lazily), closed after the test |

`watch_listings.json` — 13 rows, all `source="leasehackr"`, keys `leasehackr:1001`..`1013`
(file order is NOT key order). Values below are the fixture's explicit
`personalized_score` (what `scored_listings` carries):

| key | make | model | eff $/mo | mi/mo | score |
|---|---|---|---|---|---|
| 1006 | Tesla | Model Y | 499 | 1250 | 95.0 |
| 1001 | BMW | X5 | 845 | 1000 | 88.0 |
| 1011 | Toyota | RAV4 | 381 | 1250 | 81.0 |
| 1002 | BMW | i4 | 520 | 1500 | 72.5 |
| 1005 | Tesla | Model 3 | 365 | 1000 | 72.5 |
| 1013 | Ford | Mustang Mach-E | 430 | 1500 | 68.0 |
| 1008 | Honda | Civic | 255 | 833 | 63.0 |
| 1003 | BMW | 330i | 462 | 833 | 55.0 |
| 1009 | Honda | Accord | 318 | 1000 | 47.0 |
| 1004 | BMW | X3 | 610 | 1250 | 41.0 |
| 1007 | Tesla | Model S | 1195 | 1500 | 30.0 |
| 1012 | Toyota | Camry | 299 | 600 | 25.0 |
| 1010 | Honda | CR-V | 352 | 625 | 12.0 |

The only score tie is 1002/1005 (72.5); 1005 precedes 1002 in the file, so a correct
tie-break (listing_key asc) puts 1002 first.

**Important:** `scored_snapshot(enriched_listings)` re-scores with the rules ranker, so its
`personalized_score`s differ from the table. Expectations over `scored_snapshot` output (and
therefore over the HTTP `/matches` endpoint) must be computed from that output (e.g. compare
with `match_watch(w, scored_snapshot(...))`, or check invariants), not hard-coded from the table.

## Test plans

### tests/test_watch_store.py (owner: test_watch_store) — uses `mem_con`
- `setup` idempotent: `db.setup(mem_con)` twice, no error; `watches` table and
  `watch_id_seq` exist (query `information_schema.tables` / `duckdb_sequences()`).
- `db.connect(":memory:")` twice gives independent DBs (a watch created in one is absent in the other).
- `create_watch` returns `Watch` with positive `id`, fields equal to the input, and
  `created_at` tz-aware UTC within a few seconds of now.
- Optional fields round-trip as `None`; `make=""` was already normalised to `None`.
- Ids strictly increase across creates; after a delete, the next id is larger than any previous.
- `list_watches` returns id-ascending order; empty DB -> `[]`.
- `get_watch` hit returns equal `Watch`; miss -> `None`.
- `delete_watch` hit -> `True` and row gone; second delete / unknown id -> `False`.
- Existing tables (`current`, `history`, `vin_cache`, `feature_log`) still exist after `connect`.

### tests/test_watch_matcher.py (owner: test_watch_matcher) — uses `scored_listings`, `enriched_listings`
Expected key lists below are against `scored_listings` (fixture scores):
- `max_effective_monthly=10_000` only -> all 13, order 1006, 1001, 1011, 1002, 1005, 1013, 1008,
  1003, 1009, 1004, 1007, 1012, 1010 (tie-break 1002 before 1005).
- `make="bmw", max_effective_monthly=700` -> [1002, 1003, 1004] (X5 is 845 > 700).
- `make="  TESLA ", max_effective_monthly=600` -> [1006, 1005].
- `model="model 3", max_effective_monthly=10_000` -> [1005]; `model="Model"` -> [] (no substring).
- `make="BMW", model="X5", max_effective_monthly=845` -> [1001] (inclusive bound);
  same with `max_effective_monthly=844.99` -> [].
- `min_score=72, max_effective_monthly=10_000` -> [1006, 1001, 1011, 1002, 1005];
  `min_score=95` -> [1006] (inclusive).
- `max_miles_per_month=1000, max_effective_monthly=10_000` -> [1001, 1005, 1008, 1003, 1009, 1012, 1010].
- `make="Porsche"` -> [].
- `limit=2` on the all-13 watch -> [1006, 1001]; `limit=0` and `limit=-1` -> `ValueError`.
- Purity: input list order and every element's `model_dump()` are unchanged after `match_watch`.
- `matches` works on a `Watch` instance as well as a `WatchCreate`.
- `scored_snapshot(enriched_listings)` returns 13 `ScoredListing`s with unique keys, sorted by
  `personalized_score` desc; `scored_snapshot([]) == []`; inputs not mutated.
- A `scorer` stub (callable with `.contributions(l) -> []`) is passed through to `rank`
  (every result has `scorer == "ltr"`).

### tests/test_watch_api.py (owner: test_watch_api)
Setup: `from alr.api.main import app`; `from alr.api.watch import get_con, get_snapshot`;
`app.dependency_overrides[get_con] = lambda: mem_con` (yield-less override; the fixture closes it);
`app.dependency_overrides[get_snapshot] = lambda: (enriched_listings, None)`; clear overrides in
teardown. Use `TestClient(app)` WITHOUT a `with` block — entering it fires the startup event,
which loads the real `data/` DB and imports adapters.
- POST valid -> 201, body parses as `Watch`, id > 0; GET /watch/{id} -> 200 same body.
- POST invalid (each rejected example in "Models") -> 422 `{"error": str}`; missing body -> 422.
- GET /watch on empty DB -> `[]`; after two POSTs -> both, id asc.
- GET/DELETE/matches on unknown id -> 404 `{"error": "watch <id> not found"}`.
- DELETE -> 204, empty body; subsequent GET -> 404; second DELETE -> 404.
- GET /watch/abc -> 422 `{"error": str}`.
- /matches default limit: `count == len(matches) <= DEFAULT_MATCH_LIMIT`, keys equal
  `[s.listing_key for s in match_watch(w, scored_snapshot(enriched_listings))]`.
- /matches with `make="bmw"`: every match's make is BMW and effective_monthly <= bound.
- `limit=1` -> exactly 1; `limit=0`, `limit=MAX_MATCH_LIMIT+1` -> 422 `{"error": str}`.
- Non-watch path keeps default shape: `GET /listing/does-not-exist` (with `S.listings` empty or
  monkeypatched) -> 404 with a `detail` key and no `error` key.
