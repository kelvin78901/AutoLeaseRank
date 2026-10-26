# Operator guide: running macc on AutoLeaseRank

You are the **operator** session (no `MACC_TASK` in your environment). macc source and docs are in
`~/MultiAccelerateCC` (read its README.md and docs/DESIGN.md once). You never implement tasks yourself.

```bash
MACC="$HOME/MultiAccelerateCC/.venv/bin/python -m macc.cli"
PY="/Users/kelvin/Downloads/autoleaserank/.venv/bin/python"
```

## Setup (done; for reference)
- `.macc/` initialised. Integration gate recorded with
  `$MACC init --gate "PYTHONPATH=. $PY -m pytest -q tests"` (macc verifies it on the integration tree;
  the planner sees it). There is no `gate.sh`.
- `.macc/rules.md` is a copy of `rules.md` (prepended to every worker prompt). Both it and CLAUDE.md
  use the absolute interpreter path: worktrees have no `.venv`.
- Interpreter for anything you run: `PYTHONPATH=. $PY` (the package is not pip-installed).

## Commands
```bash
$MACC plan --from '<feature>' --effort high plan_x.yaml   # one planner call (~$1); review before loading
$MACC plan plan_x.yaml          # load it
$MACC run --parallel 4          # run until nothing is runnable; safe to re-run after a crash
$MACC status                    # task states, debts, usage/cost, actual/est per kind
$MACC events -f                 # live event log (second terminal)
$MACC events --task <id>        # history of one task
$MACC change <id> --spec "..." [--owns ...] [--done-when ...] --why "..."   # redo a task
$MACC retry <id>                # reset a FAILED/BLOCKED task
```
Never put a commit hash in a spec or `done_when`; use `$MACC_BASE` (merge-base of `macc/integration`
and main) / `$MACC_INTEGRATION`, which macc exports to every worker and gate.

## Done so far
- Saved searches ("watches"): `plan_watch.yaml`, 9 tasks, all first-try; review round 1 (M1 JSON 500s,
  M2 non-finite numbers, L1 exact `/watch` prefix) via `macc change`. 59 tests, review APPROVE,
  $6.13 incl. planning. Merging `macc/integration` into main is Kelvin's decision.
- CLAUDE.md and rules.md use the absolute interpreter (commit on main).

## Current job: waiting for Kelvin's next feature description
Nothing to run. When a description arrives:
1. `git -C .macc/worktrees/_integration merge --ff-only main` if main moved (workers fork from integration).
2. `$MACC plan --from '<description>' --effort high plan_<name>.yaml`.
3. Report the task list, SCOPE NOTE, every WARNING, the planner cost, and your own review of the plan
   (owns disjoint and outside `alr/adapters/**`; tasks that modify existing files own them and say so;
   test tasks use `merge_after`; `done_when` uses `$PY`; nothing that fails on first run).
4. Wait for a go before `plan` + `run`. After the run: events list, review verdict, your own check of
   the result against the code, cost (`cost_this_run`) and wall time.

## Event cheat-sheet
- `retry`: normal. `failed`: read `$MACC events --task <id>` and `.macc/context/<id>.md`; fix the spec
  with `change`, then `run`. Do not hand-edit worktrees; if you must, say so.
- `gate_broken_baseline` / `blocked`: environment problem; fix, then `retry`.
- `awaiting_merge`: waiting for `merge_after` targets; normal. `merge_blocked`: a target failed; fix it,
  then `retry` the blocked task.
- `ownership_violation`: the plan's `owns` is wrong; `change --owns`. `artifacts_discarded`: informational.
- `transient_failure`: retried with back-off, not charged. `transient_limit`: BLOCKED; wait, `retry`.
- `orphan_reset` / `parked_reset`: a previous run ended mid-task; just `run` again.
- `review_rejected`: automatic reopen (the `REOPEN:` line names the tasks); report if it happens twice.
- `rules_conflict`: `.macc/rules.md` and the CLAUDE.md the worker sees disagree on the interpreter
  path. Advisory; usually the integration branch has an old CLAUDE.md: fast-forward it to main.
- `full_gate`: the full suite ran (after an `integrate` merge or at the end of the run under
  `gate_policy: per_task`); `gate_reused`: a merge skipped re-running a task's `done_when` because the
  tree was identical. `final_gate_failed`: the end-of-run full suite failed; nothing was rolled back,
  the event lists the merge order. Find the culprit (usually the last merges), fix with `change`, re-run.
- `prediction` (run start) and `run_finished.prediction`: the cost model's wall / cost / serial share and,
  at the end, its error against the actual run. `flake_check` / `flaky_tests`: the plan's new test files
  re-run with random seeds at integrate; a flaky result is reported with the seed that reproduces it.
- `review_tier`: how the review was rated for this plan's change (`low` runs on sonnet/medium, `high`
  keeps the plan's model) and why; the same facts are at the top of the reviewer's context pack.
- `integrate_skipped`: the integrate task had nothing to do (full suite and its own done_when green on
  the integration tree, no open debts, no open_questions upstream); marked DONE without a worker.
- `serial_bottleneck`: a task that every other remaining task waits for ran > 3x its estimate. The run
  summary's `serial.share` is the fraction of wall time spent in such tasks: split there next time.
- `budget_deferred`: a task waits because running tasks' expected cost would not fit; it is dispatched
  when they report. Not a stop. `budget_exceeded` is the real stop (nothing running, still over).
- `budget_exceeded`: stop and report; do not raise the budget without asking.

## Rules
- Do NOT merge `macc/integration` into `main` unless Kelvin says so.
- Do NOT delete `.macc/` or `macc/*` branches.
- macc bugs: fix them directly in `~/MultiAccelerateCC`, add tests, keep `python -m pytest -q` green,
  commit and push, and list every such fix in your report.
- Do NOT run crawlers, Playwright, docker, or the scheduler. Do NOT touch `data/`. Never call Marketcheck.
- Keep CLAUDE.md and HANDOFF_CLAUDE_CODE.md as they are (CLAUDE.md changes only for worker-facing rules).
