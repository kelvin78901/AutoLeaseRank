# Market insights — interface contract

Read-only analytics over the current listing snapshot (`S.listings`, a
`list[EnrichedListing]`) and the append-only DuckDB `history` table: price-band
percentiles per segment, per-listing price history, "dropping now", nearest
comparables, and per-source data quality. This document is the single source of
truth for the segments / history / comparables / quality / API tasks and their
tests; names, signatures and semantics below are binding. Sections marked
*(clarification)* add precision but do not change anything.

## Models (alr/insights/models.py; owner: insights_contract — already landed)

```python
GROUP_BY_FIELDS = ("make", "body", "state")
DEFAULT_MIN_COUNT = 1
DEFAULT_DROPPING_LIMIT = 20;  MAX_DROPPING_LIMIT = 500
DEFAULT_K = 5;                MAX_K = 50
DEFAULT_STALE_DAYS = 7;       MAX_STALE_DAYS = 3650

class SegmentStats(BaseModel):     key: str; count: int; p10: float; p50: float; p90: float
class SegmentsResponse(BaseModel): group_by: str; min_count: int; groups: list[SegmentStats]

class HistoryPoint(BaseModel):
    crawl_ts: datetime                    # always aware UTC: naive input is taken as UTC,
                                          # aware input is converted to UTC (field validator)
    listing_key: str
    effective_monthly: Optional[float]    # required keys, but the values may be None
    days_on_market: Optional[int]
    price_drops: Optional[int]
    favorites: Optional[int]
class HistoryResponse(BaseModel):  listing_key: str; count: int; points: list[HistoryPoint]

class PriceDrop(BaseModel):
    listing_key: str
    previous_crawl_ts: datetime; latest_crawl_ts: datetime
    previous_effective_monthly: float; latest_effective_monthly: float
    drop_dollars: float; drop_pct: float
class DroppingResponse(BaseModel): count: int; limit: int; items: list[PriceDrop]

class Comparable(BaseModel):
    listing_key: str; source: str; make: str; model: str; body: str; hp: int
    effective_monthly: float; distance: float
class ComparablesResponse(BaseModel): listing_key: str; body: str; k: int; count: int
                                      comparables: list[Comparable]

class SourceQuality(BaseModel):
    source: str; count: int
    vin_fill: float; hp_fill: float; state_fill: float; year_fill: float
    stale_count: int; stale_share: float
class QualityResponse(BaseModel):  stale_days: int; as_of: datetime; sources: list[SourceQuality]
```

Every model has exactly these fields; none has a default, so every field is required
when constructing. Only `HistoryPoint` has a validator. `PriceDrop.*_crawl_ts` are
copied from `HistoryPoint.crawl_ts`, so they are aware UTC too.

## Rules for all analysis modules
- The analysis modules are alr/insights/{segments,history,comparables,quality}.py, one owner task each.
- Each module imports only the stdlib, pydantic, alr.schema and alr.insights.models (history.py may also
  use duckdb connection objects that are passed in). No analysis module imports another analysis module.
  Nothing imports fastapi, and nothing imports alr.api, alr.rank, alr.pipeline or alr.adapters.
- No module-level mutable state. Functions do not mutate their inputs. Except for
  `history.load_history`, every function is pure and does no I/O.
- Invalid arguments raise ValueError. A missing listing raises KeyError (only in comparables).

*(clarification)*
- history.py does not need `import duckdb`; it only calls `con.execute(sql, params).fetchall()`
  on the connection it is given. It never opens, closes or writes to a connection.
- "Do not mutate inputs": the input sequence keeps its order and length, and every element's
  `model_dump()` is unchanged. Returned objects are new model instances.
- Sort keys that mention a rounded output field always sort on the *unrounded* value;
  the rounding happens only when building the output model.
- ValueError checks happen before anything else, including for empty input
  (e.g. `segment_stats([], "color")` raises; `dropping_now([], 0)` raises).
- `round` is Python's built-in `round(x, n)`.

## segments (alr/insights/segments.py)
- `percentile(values: Sequence[float], q: float) -> float`: linear interpolation on the sorted values
  (numpy "linear" / statistics.quantiles method="inclusive"): h=(n-1)*q, lo=floor(h), hi=min(lo+1,n-1),
  x[lo] + (h-lo)*(x[hi]-x[lo]). n==1 returns that value. ValueError for empty values or q outside [0,1].
  The result is not rounded.
- `segment_stats(listings: Sequence[EnrichedListing], group_by: str, min_count: int = DEFAULT_MIN_COUNT)
  -> list[SegmentStats]`: group_by must be in GROUP_BY_FIELDS and min_count >= 1, else ValueError.
  Group key = str(getattr(listing, group_by)), unmodified ("NA" is an ordinary group). For each group:
  count, and p10/p50/p90 of effective_monthly via percentile(q=0.1/0.5/0.9), each rounded to 2 decimals.
  Groups with count < min_count are dropped. Sorted by count desc, then key asc. Empty input returns [].

*(clarification)*
- `percentile` sorts a copy (`sorted(values)`); the input order is irrelevant and unchanged.
  q=0 returns the minimum, q=1 the maximum. Returns a `float`.
- Keys are case-sensitive and not stripped: "BMW" and "bmw" are different groups.
- Listings with `effective_monthly == 0` are included like any other value (no filtering).
- `percentile([10, 20, 30, 40], 0.1) == 13.0`; `percentile([10, 20, 30, 40], 0.5) == 25.0`;
  `percentile([5], 0.9) == 5.0`.

## history (alr/insights/history.py)
- `load_history(con, listing_key: str | None = None) -> list[HistoryPoint]`: parameterized
  `SELECT crawl_ts, listing_key, effective_monthly, days_on_market, price_drops, favorites FROM history`
  [`WHERE listing_key = ?`] `ORDER BY crawl_ts, listing_key`. Naive TIMESTAMPs are UTC.
- `listing_timeline(rows: Sequence[HistoryPoint], listing_key: str) -> list[HistoryPoint]`: the rows with
  that listing_key, ordered by crawl_ts asc (stable). Unknown key returns [].
- `dropping_now(rows: Sequence[HistoryPoint], limit: int = DEFAULT_DROPPING_LIMIT) -> list[PriceDrop]`:
  limit < 1 raises ValueError. For each listing_key, take its rows with non-null effective_monthly
  ordered by crawl_ts; skip keys with fewer than 2. previous = second-to-last, latest = last. The key is
  "dropping" iff latest.effective_monthly < previous.effective_monthly and previous.effective_monthly > 0.
  drop_dollars = round(prev - latest, 2); drop_pct = round((prev - latest) / prev * 100, 2).
  Sorted by unrounded drop desc, then listing_key asc. Return the first `limit`.

*(clarification)*
- `load_history` builds each point as `HistoryPoint(crawl_ts=..., listing_key=..., ...)` from the
  row tuple; the model's validator makes the naive TIMESTAMP aware UTC. Rows with NULL
  effective_monthly / days_on_market / price_drops / favorites come back as `None`.
  `listing_key=None` returns every row; a key with no rows returns `[]`. The key is bound as a
  `?` parameter, never interpolated into the SQL.
- `listing_timeline` uses a stable sort on `crawl_ts` only, so rows with equal crawl_ts keep
  their input order. It returns the same `HistoryPoint` objects (no copies needed). Rows with
  null effective_monthly are kept.
- `dropping_now` does not require its input to be sorted, and it works on rows for keys that are
  absent from the newest crawl (a listing that dropped and then sold still counts). The "rows
  ordered by crawl_ts" step is a stable sort, so with duplicate crawl_ts the later input row is
  "latest". `limit > len(drops)` returns all drops. `MAX_DROPPING_LIMIT` is enforced by the
  HTTP layer only.
- Unchanged (`latest == previous`) and risen (`latest > previous`) keys are not dropping, even if
  they dropped at an earlier crawl.

## comparables (alr/insights/comparables.py)
- `HP_SCALE = 100.0`, `EFF_SCALE = 100.0` (module constants).
- `distance(a: EnrichedListing, b: EnrichedListing) -> float` =
  sqrt(((a.hp - b.hp) / HP_SCALE) ** 2 + ((a.effective_monthly - b.effective_monthly) / EFF_SCALE) ** 2),
  so 100 hp counts the same as $100/mo. It is symmetric and 0 for identical hp and effective_monthly.
- `find_comparables(listings: Sequence[EnrichedListing], listing_key: str, k: int = DEFAULT_K)
  -> list[Comparable]`: k < 1 raises ValueError. If no listing has that key, raise KeyError(listing_key).
  Candidates are listings with exactly the same `body` string as the target and a listing_key different
  from the target's. Sort by (unrounded distance, listing_key) asc and return the first k, with
  Comparable.distance rounded to 4 decimals. If the target is the only listing of its body, return [].

*(clarification)*
- Order of checks: `k < 1` (ValueError) first, then the key lookup (KeyError).
- The target is the first listing (in input order) whose `listing_key` equals the argument.
  Every listing with that key is excluded from the candidates. hp == 0 listings are ordinary
  candidates (no filtering on missing data).
- `Comparable` fields other than `distance` are copied unchanged from the candidate listing.
- `distance` uses `math.sqrt` (or `math.hypot`) and returns a non-rounded float. Example:
  hp 300 / $600 vs hp 330 / $640 → 0.5 exactly (both orders).
- `MAX_K` is enforced by the HTTP layer only; `k > len(candidates)` returns all candidates.

## quality (alr/insights/quality.py)
- `source_quality(listings: Sequence[EnrichedListing], stale_days: int = DEFAULT_STALE_DAYS,
  now: datetime | None = None) -> list[SourceQuality]`: stale_days < 0 raises ValueError. now=None means
  datetime.now(timezone.utc); naive `now` and naive crawled_at are taken as UTC. Per source:
  count; vin_fill = share with (vin or "").strip() non-empty; hp_fill = share with hp > 0;
  state_fill = share with (state or "").strip().upper() not in ("", "NA"); year_fill = share with year > 0;
  stale_count = number with crawled_at < now - timedelta(days=stale_days) (strictly older);
  stale_share = stale_count / count. Shares are rounded to 4 decimals. Sorted by count desc, then
  source asc. Empty input returns [].

*(clarification)*
- "Naive is taken as UTC" means `dt.replace(tzinfo=timezone.utc)`; aware values are compared
  as-is (aware datetimes compare correctly across offsets). Never compare naive with aware.
- `stale_days=0`: every listing crawled strictly before `now` is stale. A listing whose
  `crawled_at` equals the cutoff exactly is NOT stale.
- `MAX_STALE_DAYS` is enforced by the HTTP layer only.

## HTTP API (alr/api/insights.py, mounted in alr/api/main.py; owner: insights_api)
- `router = APIRouter()`. Dependencies: `get_insights_con()` (a generator that yields `db.connect()` and
  closes it afterwards) and `get_insights_snapshot() -> list[EnrichedListing]` (lazily `from .main import S`
  and return `S.listings`). Tests override both through `app.dependency_overrides`.
- GET /insights/segments?group_by=make|body|state (default "make"; any other value gives 422)
  &min_count=N (default DEFAULT_MIN_COUNT, >= 1) -> 200 SegmentsResponse
- GET /insights/history/{listing_key:path} -> 200 HistoryResponse (points from
  listing_timeline(load_history(con, key), key)). No rows -> 404 "no history for listing <key>"
- GET /insights/dropping?limit=N (default DEFAULT_DROPPING_LIMIT, 1..MAX_DROPPING_LIMIT) -> 200
  DroppingResponse (limit echoes the request)
- GET /insights/comparables/{listing_key:path}?k=N (default DEFAULT_K, 1..MAX_K) -> 200 ComparablesResponse.
  Unknown key -> 404 "listing <key> not found"
- GET /insights/quality?stale_days=N (default DEFAULT_STALE_DAYS, 0..MAX_STALE_DAYS) -> 200
  QualityResponse, where as_of is the `now` used (aware UTC)
- `install_insights_error_handlers(app)` is called in main.py AFTER the existing
  `install_error_handlers(app)`. It reads the handlers already in `app.exception_handlers` for
  starlette HTTPException, RequestValidationError and Exception. For paths equal to "/insights" or
  starting with "/insights/" it returns JSON {"error": str} (HTTPException: its detail and status;
  validation errors: 422 with a message built by `alr.api.watch._validation_message`; unexpected errors:
  500 {"error": "internal error"}). Every other path is delegated to the previously registered handler,
  so /watch and the default FastAPI formats are byte-for-byte unchanged. alr/api/watch.py is not modified.
- Routes are registered flat (like the /watch loop in main.py) BEFORE the static `app.mount("/", ...)`.

*(clarification)*
- Query parameters: `group_by: str = Query("make", pattern="^(make|body|state)$")` (or an equivalent
  `Literal["make", "body", "state"]`), `min_count: int = Query(DEFAULT_MIN_COUNT, ge=1)`,
  `limit: int = Query(DEFAULT_DROPPING_LIMIT, ge=1, le=MAX_DROPPING_LIMIT)`,
  `k: int = Query(DEFAULT_K, ge=1, le=MAX_K)`,
  `stale_days: int = Query(DEFAULT_STALE_DAYS, ge=0, le=MAX_STALE_DAYS)`.
  Out-of-range / non-integer values are 422 `{"error": <non-empty str>}`.
- Handlers and their data sources (`con` = `Depends(get_insights_con)`,
  `listings` = `Depends(get_insights_snapshot)`):
  - segments: `groups = segment_stats(listings, group_by, min_count)` →
    `SegmentsResponse(group_by=group_by, min_count=min_count, groups=groups)`.
  - history: `points = listing_timeline(load_history(con, key), key)`; `[]` → 404
    `"no history for listing <key>"`; else `HistoryResponse(listing_key=key, count=len(points), points=points)`.
  - dropping: `items = dropping_now(load_history(con), limit)` →
    `DroppingResponse(count=len(items), limit=limit, items=items)`.
  - comparables: `comps = find_comparables(listings, key, k)`; `KeyError` → 404
    `"listing <key> not found"`; else `ComparablesResponse(listing_key=key, body=<target's body>, k=k,
    count=len(comps), comparables=comps)`, where the target is the first listing with that key.
  - quality: `now = datetime.now(timezone.utc)`;
    `QualityResponse(stale_days=stale_days, as_of=now, sources=source_quality(listings, stale_days, now))`.
  `count` is always the length of the returned list. Only history and dropping use the DB;
  segments / comparables / quality use only the snapshot (they still may not open the real DB:
  tests override `get_insights_snapshot` and must not need `get_insights_con` for them).
- Datetimes serialise as ISO-8601 with an offset (pydantic default); clients/tests parse them with
  `datetime.fromisoformat` and must not compare raw strings.
- 404 message examples: `{"error": "no history for listing leasehackr:9999"}`,
  `{"error": "listing nope:1 not found"}`. `{listing_key:path}` lets keys containing ":" or "/"
  through unchanged.
- The /insights handlers are thin: any ValueError from a module (which valid Query bounds make
  impossible) would surface as the 500 `{"error": "internal error"}`.
- Each `install_insights_error_handlers` handler: `if path == "/insights" or path.startswith("/insights/")`
  → JSON as above; else `return await previous(request, exc)` (the handler that was in
  `app.exception_handlers` for that class when install ran; fall back to FastAPI's
  `http_exception_handler` / `request_validation_exception_handler` / a plain-text 500 if none).
  `/insightsX` and `/watch...` are not insights paths.
- main.py adds the five routes to its module docstring endpoint list.

## Shared test fixtures (tests/conftest.py; owner: insights_contract — already landed)

| fixture | value |
|---|---|
| `INSIGHTS_LISTINGS` / `INSIGHTS_HISTORY` | `Path`s of the two JSON fixtures (module constants) |
| `insights_rows` | raw `list[dict]` from `tests/fixtures/insights_listings.json` (fresh per test) |
| `insights_listings` | `[EnrichedListing(**r) for r in insights_rows]` (21 listings) |
| `insights_history_rows` | raw `list[dict]` from `tests/fixtures/insights_history.json` (28 rows) |
| `insights_history` | `[HistoryPoint(**h) for h in insights_history_rows]` (file order, aware UTC) |
| `insights_now` | `datetime(2026, 10, 15, tzinfo=timezone.utc)` |
| `insights_con` | `mem_con` with every history row inserted (crawl_ts as naive UTC) |

Existing fixtures (`watch_rows`, `enriched_listings`, `scored_listings`, `mem_con`) are unchanged.

### insights_listings.json — 21 listings (file order is NOT key order)

Every row has `crawled_at` at 12:00:00+00:00 except `leasehackr:2004` (00:00:00+00:00).

| key | make | model | body | hp | eff $/mo | state | year | vin | crawled_at |
|---|---|---|---|---|---|---|---|---|---|
| leasehackr:2001 | BMW | X5 | SUV | 335 | 845 | CA | 2024 | set | 10-12 |
| leasehackr:2002 | BMW | i4 | Sedan | 335 | 520 | NJ | 2025 | null | 10-10 |
| leasehackr:2003 | BMW | 330i | Sedan | 255 | 462 | NA | 0 | "" | 09-05 |
| leasehackr:2004 | BMW | X3 | SUV | 300 | 600 | CA | 2024 | set | 10-08 00:00 |
| leasehackr:2005 | Tesla | Model Y | SUV | 330 | 640 | WA | 2024 | set | 10-14 |
| leasehackr:2006 | Tesla | Model 3 | Sedan | 283 | 365 | WA | 2024 | null | 09-20 |
| leasehackr:2007 | Honda | Civic | Sedan | 158 | 258 | NA | 2023 | "   " | 10-01 |
| leasehackr:2008 | Ford | F-150 | Truck | 400 | 690 | TX | 2024 | set | 10-13 |
| leasehackr:2009 | Volvo | V60 | Wagon | 247 | 540 | NY | 2024 | null | 09-12 |
| swapalease:3001 | Toyota | RAV4 | SUV | 203 | 381 | TX | 0 | "" | 10-09 |
| swapalease:3002 | Toyota | Highlander | SUV | 260 | 570 | FL | 2023 | null | 09-15 |
| swapalease:3003 | Toyota | Tacoma | Truck | 0 | 495 | NA | 2022 | null | 10-11 |
| swapalease:3004 | Toyota | Camry | Sedan | 0 | 299 | NJ | 2024 | set | 09-01 |
| swapalease:3005 | Honda | CR-V | SUV | 190 | 352 | NY | 2023 | "" | 10-02 |
| swapalease:3006 | Ford | Mustang | Coupe | 315 | 610 | CA | 2024 | set | 10-13 |
| swapalease:3007 | Kia | Telluride | SUV | 291 | 545 | TX | 2024 | null | 09-25 |
| marketcheck:4001 | BMW | M4 | Coupe | 473 | 1180 | CA | 2025 | set | 10-14 |
| marketcheck:4002 | Tesla | Model S | Sedan | 670 | 1099 | WA | 2024 | set | 09-28 |
| marketcheck:4003 | Ford | Explorer | SUV | 0 | 720 | NA | 0 | set | 10-05 |
| marketcheck:4004 | Ford | Maverick | Truck | 250 | 410 | TX | 2024 | set | 10-12 |
| marketcheck:4005 | Toyota | GR Supra | Coupe | 382 | 760 | FL | 2025 | set | 10-07 |

All dates are 2026. Sources: leasehackr 9, swapalease 7, marketcheck 5. Bodies: SUV 8, Sedan 6,
Coupe 3, Truck 3, Wagon 1. Makes: BMW 5, Toyota 5, Ford 4, Tesla 3, Honda 2, Kia 1, Volvo 1.
In the file, `swapalease:3002` comes before `leasehackr:2005` (equidistant tie, see comparables).

### insights_history.json — 28 rows over 4 crawls

Crawls: C1 = 2026-10-01T06:00Z, C2 = 10-05T06:00Z, C3 = 10-09T06:00Z, C4 = 10-13T06:00Z.
File order: all C4 rows, then C1, then C3, then C2 (deliberately unsorted).
`effective_monthly` per crawl ("–" = no row):

| key | C1 | C2 | C3 | C4 | dropping? |
|---|---|---|---|---|---|
| leasehackr:2001 | 900 | 880 | 870 | 845 | yes, 25 |
| leasehackr:2004 | – | 650 | 640 | 600 | yes, 40 (tie) |
| swapalease:3006 | 650 | – | – | 610 | yes, 40 (tie) |
| marketcheck:4001 | 1250 | 1220 | 1250 | 1180 | yes, 70 |
| leasehackr:2008 | 720 | 680 | 680 | 690 | no (dropped C1→C2, rose at C4) |
| swapalease:3001 | 381 | 381 | 381 | 381 | no (unchanged) |
| leasehackr:2005 | – | – | – | 640 | no (single row) |
| swapalease:3999 | 480 | 450 | – | – | yes, 30 (sold: not in the listings fixture, absent from C4) |
| marketcheck:4002 | null | 1150 | 1130 | 1099 | yes, 31 (the C1 row has null eff/dom/drops/favorites) |

Other columns: `days_on_market = 4 * crawl_number` (C1=4 … C4=16); `price_drops` = 0-based index of the
row within that key; `favorites = crawl_number + price_drops`; all three are null on the
`marketcheck:4002` C1 row.

## Expected results on the fixtures

### segments — `segment_stats(insights_listings, g)` (min_count=1)

`group_by="make"`:

| key | count | p10 | p50 | p90 |
|---|---|---|---|---|
| BMW | 5 | 485.2 | 600.0 | 1046.0 |
| Toyota | 5 | 331.8 | 495.0 | 684.0 |
| Ford | 4 | 470.0 | 650.0 | 711.0 |
| Tesla | 3 | 420.0 | 640.0 | 1007.2 |
| Honda | 2 | 267.4 | 305.0 | 342.6 |
| Kia | 1 | 545.0 | 545.0 | 545.0 |
| Volvo | 1 | 540.0 | 540.0 | 540.0 |

`group_by="body"`:

| key | count | p10 | p50 | p90 |
|---|---|---|---|---|
| SUV | 8 | 372.3 | 585.0 | 757.5 |
| Sedan | 6 | 278.5 | 413.5 | 809.5 |
| Coupe | 3 | 640.0 | 760.0 | 1096.0 |
| Truck | 3 | 427.0 | 495.0 | 651.0 |
| Wagon | 1 | 540.0 | 540.0 | 540.0 |

`group_by="state"`:

| key | count | p10 | p50 | p90 |
|---|---|---|---|---|
| CA | 4 | 603.0 | 727.5 | 1079.5 |
| NA | 4 | 319.2 | 478.5 | 652.5 |
| TX | 4 | 389.7 | 477.5 | 646.5 |
| WA | 3 | 420.0 | 640.0 | 1007.2 |
| FL | 2 | 589.0 | 665.0 | 741.0 |
| NJ | 2 | 321.1 | 409.5 | 497.9 |
| NY | 2 | 370.8 | 446.0 | 521.2 |

`min_count=2` on make drops Kia and Volvo (5 groups); on body drops Wagon (4 groups);
`min_count=9` on body → `[]`. Ties (BMW/Toyota at 5, Coupe/Truck at 3, CA/NA/TX at 4) are ordered
by key asc. All-listings percentile check: p10/p50/p90 of the 21 values are 352.0 / 545.0 / 845.0.

### history — `dropping_now(insights_history)` (default limit)

| # | listing_key | previous_crawl_ts | latest_crawl_ts | prev | latest | drop_dollars | drop_pct |
|---|---|---|---|---|---|---|---|
| 1 | marketcheck:4001 | C3 | C4 | 1250.0 | 1180.0 | 70.0 | 5.6 |
| 2 | leasehackr:2004 | C3 | C4 | 640.0 | 600.0 | 40.0 | 6.25 |
| 3 | swapalease:3006 | C1 | C4 | 650.0 | 610.0 | 40.0 | 6.15 |
| 4 | marketcheck:4002 | C3 | C4 | 1130.0 | 1099.0 | 31.0 | 2.74 |
| 5 | swapalease:3999 | C1 | C2 | 480.0 | 450.0 | 30.0 | 6.25 |
| 6 | leasehackr:2001 | C3 | C4 | 870.0 | 845.0 | 25.0 | 2.87 |

`limit=2` → [4001, 2004]; `limit=3` → [4001, 2004, 3006] (the $40 tie is broken by key asc,
not by drop_pct). Not present: 2008, 3001, 2005. The result is identical for any permutation
of the input rows.

`listing_timeline(insights_history, "leasehackr:2001")` → 4 points C1..C4 (900, 880, 870, 845).
`listing_timeline(insights_history, "marketcheck:4002")` → 4 points, the first with
`effective_monthly is None`. `listing_timeline(insights_history, "nope")` → `[]`.
`load_history(insights_con)` → 28 points ordered by (crawl_ts, listing_key), first
`(C1, "leasehackr:2001")`; `load_history(insights_con, "swapalease:3006")` → 2 points (C1, C4);
`load_history(insights_con, "x' OR '1'='1")` → `[]`.

### comparables — `find_comparables(insights_listings, key, k)`

| target | k | result `(listing_key, distance)` |
|---|---|---|
| leasehackr:2004 (SUV) | 5 | (leasehackr:2005, 0.5), (swapalease:3002, 0.5), (swapalease:3007, 0.5573), (swapalease:3001, 2.3952), (leasehackr:2001, 2.4749) |
| leasehackr:2004 | 1 | (leasehackr:2005, 0.5) — equidistant tie broken by key asc |
| leasehackr:2004 | 50 | all 7 other SUVs; last is marketcheck:4003 (3.2311) |
| swapalease:3001 (SUV) | 2 | (swapalease:3005, 0.3178), (swapalease:3007, 1.8612) |
| leasehackr:2003 (Sedan) | 3 | (leasehackr:2002, 0.9881), (leasehackr:2006, 1.0096), (leasehackr:2007, 2.2589) |
| swapalease:3006 (Coupe) | 5 | (marketcheck:4005, 1.6428), (marketcheck:4001, 5.9149) |
| swapalease:3003 (Truck, hp 0) | 5 | (marketcheck:4004, 2.6405), (leasehackr:2008, 4.45) |
| leasehackr:2009 (Wagon) | 5 | `[]` |
| swapalease:3999 / "nope" | 5 | KeyError |

`distance(L["leasehackr:2004"], L["leasehackr:2005"]) == 0.5 == distance(L["leasehackr:2004"], L["swapalease:3002"])`.

### quality — `source_quality(insights_listings, stale_days, now=insights_now)`

`stale_days=7` (cutoff 2026-10-08T00:00Z; `leasehackr:2004` sits exactly on it and is not stale):

| source | count | vin_fill | hp_fill | state_fill | year_fill | stale_count | stale_share |
|---|---|---|---|---|---|---|---|
| leasehackr | 9 | 0.4444 | 1.0 | 0.7778 | 0.8889 | 4 | 0.4444 |
| swapalease | 7 | 0.2857 | 0.7143 | 0.8571 | 0.8571 | 4 | 0.5714 |
| marketcheck | 5 | 1.0 | 0.8 | 0.8 | 0.8 | 3 | 0.6 |

Stale at 7 days: leasehackr 2003, 2006, 2007, 2009; swapalease 3002, 3004, 3005, 3007;
marketcheck 4002, 4003, 4005. Fill columns do not depend on `stale_days`. Other `stale_days`:
`0` → stale_count 9 / 7 / 5 (share 1.0 each); `30` → 2 / 1 / 0 (0.2222 / 0.1429 / 0.0);
`60` → 0 / 0 / 0.

## Test plans

General: tests use only the fixtures above, `duckdb.connect(":memory:")` through `mem_con` /
`insights_con`, and hand-built models; no network, never the real `data/` DB. Build ad-hoc listings
with `EnrichedListing(**{**insights_rows[0], "listing_key": ..., ...})` or a minimal dict containing
the required NormalizedListing fields.

### tests/test_insights_segments.py (owner: test_insights_segments_quality)
- `percentile`: the examples in the segments clarification; n == 1; q = 0 / 1 give min / max;
  unsorted input gives the same result as sorted, and the input list is unchanged; matches
  `statistics.quantiles(v, n=10, method="inclusive")` at deciles for the 21 fixture values;
  result is not rounded (e.g. `percentile([1, 2], 1/3)` is `1.3333…`, not 1.33);
  `ValueError` for `[]`, q = -0.01, q = 1.01.
- `segment_stats`: the three tables above exactly (keys, counts, p10/p50/p90, order); min_count
  cases above; `[]` input → `[]`; group_by "model" / "MAKE" / "" → ValueError; min_count 0 / -1 →
  ValueError (also with `[]` input); "NA" is a normal state group; case-sensitive keys
  ("BMW" vs "bmw" ad-hoc listing → two groups); p-values are rounded to 2 decimals
  (ad-hoc values where the unrounded percentile has more decimals); purity (input order and
  `model_dump()`s unchanged).

### tests/test_insights_quality.py (owner: test_insights_segments_quality)
- The stale_days=7 table exactly (all fields, order); stale_days 0 / 30 / 60 stale counts.
- Boundary: `leasehackr:2004` (crawled exactly at the cutoff) is not stale at stale_days=7, but is
  stale with `now = insights_now + timedelta(microseconds=1)`.
- Naive `now` (`datetime(2026, 10, 15)`) gives the same result as `insights_now`; an aware non-UTC
  `now` for the same instant gives the same result; a naive `crawled_at` listing is treated as UTC.
- `now=None` with stale_days=MAX_STALE_DAYS → every stale_count 0 (uses the real clock).
- vin `"   "` counts as missing; state `" na "` / `""` count as missing (ad-hoc listings).
- `[]` → `[]`; stale_days=-1 → ValueError (also with `[]`); purity.
- Ordering: count desc, ties by source asc (ad-hoc listings with two sources of equal count).

### tests/test_insights_history.py (owner: test_insights_history) — uses `insights_con`, `insights_history`
- `load_history`: the expectations above (count 28, order by (crawl_ts, listing_key), filtered key,
  unknown key → `[]`, SQL-injection-looking key → `[]`); every `crawl_ts` aware UTC and equal to the
  fixture's ISO value; the null row of `marketcheck:4002` round-trips as `None`s; the table is not
  modified (count still 28 afterwards).
- `listing_timeline`: order and contents above; stable for equal crawl_ts (ad-hoc rows); unknown → `[]`.
- `dropping_now`: the table above exactly (all PriceDrop fields; `*_crawl_ts` equal C-values as aware
  UTC); limit 1 / 2 / 3 / 100; limit 0 / -1 → ValueError (also with `[]`); `[]` → `[]`;
  shuffled input gives the same result; not mutated; ad-hoc: prev == 0 → not dropping; a key whose
  latest row has null effective_monthly uses the last two non-null rows; a key with only one non-null
  row is skipped; drop_dollars / drop_pct rounded to 2 decimals while sorting uses the unrounded drop
  (e.g. drops 10.004 vs 10.001 on keys "b" and "a" → "b" first although both round to 10.0).
- `dropping_now(load_history(insights_con))` equals `dropping_now(insights_history)`.

### tests/test_insights_comparables.py (owner: test_insights_comparables)
- `HP_SCALE == EFF_SCALE == 100.0`.
- `distance`: the 0.5 example both ways (symmetric); identical hp/eff → 0.0; 100 hp only == $100 only == 1.0;
  result not rounded.
- `find_comparables`: the table above exactly (keys, distances, order, every Comparable field copied
  from the candidate); the target never appears in its own result; all results share the target's body;
  Wagon → `[]`; k 0 / -1 → ValueError (raised before the KeyError for an unknown key);
  unknown key / sold key `swapalease:3999` → KeyError; body match is exact (ad-hoc "suv" listing is not
  an SUV candidate); purity.

### tests/test_insights_api.py (owner: test_insights_api)
Setup: `from alr.api.main import app`; `from alr.api.insights import get_insights_con, get_insights_snapshot`;
`app.dependency_overrides[get_insights_con] = lambda: insights_con` (yield-less; the fixture closes it);
`app.dependency_overrides[get_insights_snapshot] = lambda: insights_listings`; clear overrides in teardown.
Use `TestClient(app)` WITHOUT a `with` block — entering it fires the startup event, which loads the real
`data/` DB and imports adapters. For the 500 case use `TestClient(app, raise_server_exceptions=False)`.
- `/insights/segments` default → group_by "make", min_count 1, groups == the make table;
  `?group_by=body&min_count=2` → 4 groups; `?group_by=state` → the state table;
  `?group_by=model`, `?min_count=0`, `?min_count=abc` → 422 `{"error": <non-empty str>}` (single key).
- `/insights/history/leasehackr:2001` → count 4, points in crawl order, crawl_ts parse to the C-values;
  `/insights/history/marketcheck:4002` → first point has `effective_monthly` null;
  `/insights/history/leasehackr:9999` → 404 `{"error": "no history for listing leasehackr:9999"}`.
- `/insights/dropping` → limit 20, count 6, item keys in the table order; `?limit=2` → count 2, limit 2;
  `?limit=0`, `?limit=501` → 422; `?limit=500` → 200.
- `/insights/comparables/leasehackr:2004` → body "SUV", k 5, count 5, keys/distances as in the table;
  `?k=1` → [leasehackr:2005]; `/insights/comparables/leasehackr:2009` → count 0, comparables [];
  `/insights/comparables/nope:1` → 404 `{"error": "listing nope:1 not found"}`; `?k=0`, `?k=51` → 422.
- `/insights/quality` → stale_days 7; `as_of` parses as aware UTC within a few seconds of now; `sources`
  equals `source_quality(insights_listings, 7, now=as_of)` dumped as JSON; `?stale_days=0` → 200;
  `?stale_days=-1`, `?stale_days=3651` → 422.
- 500: override `get_insights_snapshot` with a function that raises `RuntimeError` →
  `/insights/segments` gives 500 `{"error": "internal error"}`.
- Other paths unchanged: `GET /watch/abc` → 422 with a single `error` key (watch format);
  `GET /watch/999999` with `get_con` overridden to `mem_con` → 404 `{"error": "watch 999999 not found"}`;
  `GET /listing/does-not-exist` (with `S.listings` monkeypatched to `[]`) → 404 with a `detail` key and no
  `error` key; `GET /top_deals?budget=abc` (validation fails before the handler runs) keeps
  FastAPI's 422 `{"detail": [...]}` shape.
- Routes exist before the static mount: every `/insights/...` route path appears in `app.routes` before
  the `Mount` at "/".
