# Review: market insights (alr/insights, /insights API)

Base: `a18cbc6` (before `insights_contract`) .. `c5e2775` (after `integrate_insights`).
Reference: `alr/insights/CONTRACT.md` and the review_insights task spec.

## Summary

The four analysis modules, the models and the HTTP layer match the contract. Every point the task
asked me to check holds (see the checklist below). The integration gate passed (147 tests). I did not
re-run the full suite. My extra checks were in-process `TestClient` probes, offline, with no real DB
(see "Probes run").

I found no correctness or contract violations. All findings are maintainability issues or test gaps.
The main one is that the stacked error handlers only work if they are installed in a set order, and
main.py doesn't show that. I recommend a follow-up refactor (below). It needs edits to
`alr/api/watch.py`, which the contract (line 184) put out of scope for this feature.

## Contract checklist

| Check | Result | Where |
|---|---|---|
| Modules are independent: imports are stdlib, pydantic, `alr.schema` and `alr.insights.models` only; no module imports another; no fastapi/api/rank/pipeline/adapters | OK | `alr/insights/{segments,history,comparables,quality}.py` import lines |
| No module-level mutable state | OK | only `_COLUMNS` (str), `HP_SCALE`, `EFF_SCALE` |
| `load_history` SQL is parameterized | OK: the key is bound as `?`; `_COLUMNS` is a constant; the injection-looking key is tested | `alr/insights/history.py:14-29` |
| Percentile: h=(n-1)q, floor, min(lo+1,n-1), linear; not rounded; sorted copy | OK | `alr/insights/segments.py:11-22` |
| Rounding points: p-values to 2 places at model build; drop $/% to 2; distance to 4; shares to 4. Sorting uses the unrounded values | OK | `segments.py:42-44`, `history.py:57-71`, `comparables.py:38-55`, `quality.py:37-43` |
| segments: min_count filter, then count desc / key asc; ValueError checks before empty input | OK | `segments.py:31-49` |
| dropping_now uses each key's own last two non-null crawls (stable sort, so later input wins on equal ts); `prev > 0`; limit check first | OK | `history.py:42-72` |
| comparables: exact body match; every row with the target key excluded; tie-break (distance, key) asc; `k<1` checked before KeyError | OK | `comparables.py:33-45` |
| Naive datetimes taken as UTC: `HistoryPoint` validator (naive → replace, aware → astimezone); quality `_utc` for both `now` and `crawled_at` | OK | `models.py:45-52`, `quality.py:12-13,23,33` |
| Routes added flat, before the static `"/"` mount | OK, and tested | `alr/api/main.py:44-46` vs `:285`; `tests/test_insights_api.py:40-49` |
| Chained handlers pass non-/insights paths to the earlier handlers | OK (probed, below) | `alr/api/insights.py:110-134` |
| `/insights` errors are `{"error": str}`: 404 unknown key, 422 bad params | OK | `insights.py:63,85,117-128`; tests `test_insights_api.py:83-85,103-106,131-133,159-168,206-208` |
| No changes to adapters / scheduler / pipeline / rank / watch / `alr/api/watch.py`; no new deps | OK (`git diff --stat` touches only README, `alr/api/{insights,main}.py`, `alr/insights/*`, tests) | |
| Tests never touch the real DB | OK: `mem_con` uses `db.connect(":memory:")`; the API tests override `get_insights_con`, `get_insights_snapshot` and `watch.get_con`, and use `TestClient(app)` without `with`, so startup never runs | `tests/conftest.py:29-36`, `tests/test_insights_api.py:22-30` |

Root cause vs symptom: `integrate_insights` fixed a test that used the `insights_con` and `mem_con`
fixtures together. They are the same connection, so its "empty table" assertion was wrong. Splitting
the test fixed the root cause, and no assertion was weakened. No hidden stubs: there is no
`TODO(macc)`, no `pass`/`NotImplementedError` placeholder, and no skipped tests in the diff.

## Findings

### F1 (medium, maintainability): install order matters, and main.py does not show it
`alr/api/main.py:47-48` calls `install_error_handlers(app)` and then
`install_insights_error_handlers(app)`, with no comment. The order matters because of an asymmetry:
- `install_insights_error_handlers` copies `app.exception_handlers` at install time and passes other
  paths on to those handlers (`alr/api/insights.py:112-115`, `:122,128,134`).
- `install_error_handlers` keeps nothing from before. Its fallbacks are hard-coded to FastAPI's
  defaults (`alr/api/watch.py:110`, `:116`, `:124`).

If the two calls are swapped, the watch handlers silently replace the insights ones, and `/insights`
errors go back to FastAPI's `{"detail": ...}` format. I confirmed this with a probe:
`/insights/dropping?limit=0` returned `422 {"detail":[...]}`. The existing 422/404 tests in
`tests/test_insights_api.py` would catch the swap, so this is not a live bug. But the only place the
rule is written down is CONTRACT.md:178. Anyone reading main.py has no way to know.

### F2 (medium, maintainability): the chaining pattern does not scale to a third router
A third router with its own error format would have to:
- copy the capture-and-delegate code a third time (about 25 lines); and
- be installed last, because only the chaining installers are safe to install after others, and
  watch's installer is not one of them.

Each new prefix adds another closure that every error on an unrelated path passes through. The prefix
rule ends up spread across N modules, and no single place lists which prefixes use the
`{"error": ...}` format. A few other things get no consistent home:
- the 204/304 no-body case: FastAPI's `http_exception_handler` handles it, the custom branches at
  `watch.py:108` and `insights.py:120` do not;
- the `Exception` handler, which runs inside `ServerErrorMiddleware`;
- the logging note at `watch.py:120-121`.

The chain works today, but it doesn't scale.

### F3 (low, maintainability): duplicated code between watch.py and insights.py
- `get_con` (`watch.py:29-34`) and `get_insights_con` (`insights.py:36-41`) are identical.
- `_is_watch_path` (`watch.py:88-90`) and `_is_insights_path` (`insights.py:101-103`) are the same
  prefix test with a different constant.
- The three handler bodies (`watch.py:105-124` vs `insights.py:117-134`) produce the same JSON
  shapes and status codes.
- `_default_unexpected` (`insights.py:106-107`) repeats the inline plain-text 500 at `watch.py:124`.
- `insights.py:31` imports the private `_validation_message` from `watch.py`. As a result, the
  insights API depends on the watch module for a shared formatting helper.
- The route-flattening loop is pasted twice in `main.py:41-46`.

### F4 (medium, test gap): no test proves the other paths are unchanged for 422 and 500
Here is what the current tests actually cover for paths that are neither /watch nor /insights:
- 404: covered (`test_insights_api.py:220-223`; `test_watch_api.py:167-172,198-204`).
- 422: **not tested.** CONTRACT.md:467-468 calls for `GET /top_deals?budget=abc` → FastAPI's
  `{"detail": [...]}`, and no test does it. I probed it and it returns `422 ['detail']`, which is
  correct, but nothing would catch a regression.
- 500: **not tested.** I probed it by making `/health` raise: it returns `500 text/plain
  "Internal Server Error"`, which is correct, but no test covers it.

/watch paths (the /insights handlers pass these on):
- 404, 422 and 500 are all covered (`test_insights_api.py:211-217`, `test_watch_api.py:175-188`).
  `test_watch_api` runs against the real `app`, so the insights handlers are already installed and
  are passing those requests on.

/insights itself:
- 500: **not tested.** CONTRACT.md:462-463 requires `/insights/segments` with a raising snapshot
  dependency → `500 {"error": "internal error"}`. I probed it and it works (`application/json`,
  `{"error":"internal error"}`), but `tests/test_insights_api.py` has no such test.
- The `/insightsX` lookalike is **not tested** (watch has one at `test_watch_api.py:198-204`). I
  probed it: `/insightsX` → `404 {"detail":"Not Found"}`, which is correct.

Smaller gaps in the same file:
- `test_insights_api.py:220-223` checks for `"detail" in body` but not for `"error" not in body`.
  The watch version of this test checks both.
- The contract's API test plan includes `/insights/history/marketcheck:4002` (first point has null
  `effective_monthly`); there is no such test. The module test covers it (`test_insights_history.py:66,105`).

None of these gaps hide a defect: every one of them behaves correctly when probed. They do leave the
"byte-for-byte unchanged" promise (CONTRACT.md:183-184) only partly enforced. I am not rejecting for
this, because no behaviour is wrong. Tests for these cases belong to the test task that owns
`tests/test_insights_api.py`.

### F5 (low): an extra scan of the listings in the comparables handler
`alr/api/insights.py:86` looks the target up again with `next(...)` after `find_comparables` has
already found it. That is correct, and it is what the contract says to do (CONTRACT.md:203-204). It is
an O(n) scan per request. I note it only; no change needed.

## Probes run (offline, in-process TestClient, no real DB, startup hook not fired)

| Request | Result |
|---|---|
| `/insights/segments` with the snapshot dependency raising `RuntimeError` | `500 application/json {"error":"internal error"}` |
| `/top_deals?budget=abc` | `422 {"detail": [...]}` (FastAPI default) |
| `/health` with `S.listings.__len__` raising | `500 text/plain "Internal Server Error"` (unchanged) |
| `/insightsX` | `404 {"detail":"Not Found"}` (not an insights path) |
| `/insights`, `/insights/nope` | `404 {"error":"Not Found"}` |
| Fresh app, install order reversed: `/insights/dropping?limit=0` | `422 {"detail":[...]}`, which confirms F1 |
| Same app: `/watch/abc` | `422 {"error": ...}` |

## Recommended structure (follow-up; not done here, because the contract froze `alr/api/watch.py`)

1. **One error module.** Create `alr/api/errors.py` containing:
   - `JSON_ERROR_PREFIXES: tuple[str, ...]`;
   - `_validation_message` (moved out of watch.py);
   - `_matches(path, prefixes)`, which returns true when `path == p or path.startswith(p + "/")`;
   - `install_json_errors(app, prefixes)`, which registers **one** handler each for
     `StarletteHTTPException`, `RequestValidationError` and `Exception`.

   Each handler returns `{"error": ...}` when the path matches a prefix. Otherwise it calls FastAPI's
   `http_exception_handler` or `request_validation_exception_handler` directly, or returns the
   plain-text 500. Nothing chains, so install order no longer matters.
2. **One call in main.py:** `install_json_errors(app, prefixes=("/watch", "/insights"))`. The full
   list of prefixes that use the JSON error format is then visible in one line. A third router just
   adds its prefix there. If a router ever needs a *different* format, change the prefix tuple to a
   `{prefix: formatter}` map. That is still one handler set and still one place to look.
3. **Shared helpers.** Move `get_con` into `alr/api/deps.py` (or `alr/store/db.py`) and use it from
   both routers. Add a `_add_flat(app, router)` helper so main.py doesn't repeat the route-copy loop.
4. **Tests:**
   - Unit-test `install_json_errors` once on a throwaway `FastAPI()`: a matching prefix, a lookalike
     prefix, and an unrelated path, each for 404, 422 and 500.
   - Keep one test per router that asserts its prefix is registered.
   - Add the missing tests from F4: `/top_deals` 422 shape, unrelated-path 500 shape, `/insights`
     500, and the `/insightsX` lookalike.

VERDICT: APPROVE
