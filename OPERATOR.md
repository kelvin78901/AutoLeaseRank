# Operator guide: running macc on AutoLeaseRank

You are the **operator** session (no `MACC_TASK` in your environment). macc source and docs are in
`~/MultiAccelerateCC` (read its README.md and docs/DESIGN.md once). You never implement tasks yourself.

```bash
MACC="$HOME/MultiAccelerateCC/.venv/bin/python -m macc.cli"
PY="$HOME/Downloads/AutoLeaseRank/.venv/bin/python"
```

## Current job: first real-repo trial (plan only, then stop)

1. **Baseline**
   - `$PY -m pip install -q pytest` (pytest is missing from the project venv).
   - `$PY -m pytest -q tests` must print `2 passed`. If not, stop and report.
   - `git status --short` should show only `.DS_Store`; leave it.
2. **Init macc**
   - `$MACC init`
   - Write `.macc/gate.sh` containing exactly:
     ```
     #!/usr/bin/env bash
     cd "$(dirname "$0")/.." 2>/dev/null || true
     "$HOME/Downloads/AutoLeaseRank/.venv/bin/python" -m pytest -q tests; rc=$?; [ $rc -eq 0 ] || [ $rc -eq 5 ]
     ```
     then `chmod +x .macc/gate.sh`. Note: gates run inside `.macc/worktrees/_integration`, so the
     script must `cd` to that worktree root (the line above does) and must use the absolute venv python.
   - Copy the provided `rules.md` to `.macc/rules.md` (it is prepended to every worker prompt).
   - Verify the gate on the integration worktree: `bash .macc/gate.sh; echo rc=$?` → expect `2 passed`, rc=0.
3. **Plan** (one planner call, ~$0.6; do NOT load or run):
   ```
   $MACC plan --from 'Add saved searches ("watches") to AutoLeaseRank. A watch is a set of criteria:
   optional make, optional model, max_effective_monthly (float), min_score (0-99), max_miles_per_month,
   plus a name and created_at. Persist watches in the existing DuckDB (new table; create it through the
   same connect/setup path production uses). Matching: given the current scored snapshot
   (store.load_current + the ranker output as already exposed by /top_deals), return listings that
   satisfy every set criterion, ordered by score desc, capped by a limit. HTTP API in a new APIRouter
   mounted in alr/api/main.py: POST /watch (create, 201), GET /watch (list), GET /watch/{id},
   DELETE /watch/{id} (204 / 404), GET /watch/{id}/matches?limit=N. All errors JSON {"error": str}.
   Everything must be testable offline with an in-memory DuckDB and fixture listings; tests for the
   store, the matcher, and the API (FastAPI TestClient) in tests/. Do not touch alr/adapters, the
   scheduler, or the pipeline runner. Keep stdlib + existing deps only.' --effort high plan_watch.yaml
   ```
4. **Report back, then stop** (do not `macc plan plan_watch.yaml`, do not `macc run`):
   - the printed task list, any SCOPE NOTE, and every WARNING line;
   - the planner cost from `$MACC status` (usage line) and wall time;
   - your own read of `plan_watch.yaml`: are `owns` disjoint and do they exclude `alr/adapters/**`?
     Does the task that edits `alr/api/main.py` own it and say it *modifies* it? Do test tasks use
     `merge_after`? Are `done_when` commands using `.venv/bin/python`? Is the integration gate
     `bash .macc/gate.sh` reused? Anything that would fail on first run?
   - anything about macc itself that looks wrong.

## Rules
- Do NOT merge `macc/integration` into `main`.
- Do NOT delete `.macc/` or `macc/*` branches. Do NOT edit `~/MultiAccelerateCC`.
- Do NOT run crawlers, Playwright, docker, or the scheduler. Do NOT touch `data/`.
- Keep CLAUDE.md and HANDOFF_CLAUDE_CODE.md as they are.

## Event cheat-sheet (for the run phase, later)
retry = normal; failed = read `$MACC events --task <id>` and `.macc/context/<id>.md`;
gate_broken_baseline / blocked = environment, fix then `retry`; awaiting_merge = waiting for merge_after;
ownership_violation = plan's owns wrong, `change --owns`; transient_failure = retried, ignore;
review_rejected = automatic; budget_exceeded = stop and report.
