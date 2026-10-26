# AutoLeaseRank

Python 3.10+, FastAPI + DuckDB + Polars + LightGBM. Read HANDOFF_CLAUDE_CODE.md for how the
system works. Interpreter: `.venv/bin/python` (never bare `python3`). Tests: `.venv/bin/python -m pytest -q tests`.

Hard rules for any automated worker:
- No network. Never run crawlers, Playwright, or anything under `alr/adapters/`, `alr/scheduler.py`,
  `alr/pipeline/run.py`. Never call Marketcheck.
- Tests use an in-memory DuckDB (`duckdb.connect(":memory:")`) and fixtures; never the real `data/` DB.
- Do not add dependencies.

If MACC_TASK is set in your environment you are a macc worker: follow your context pack.
If it is not set, you are the operator: read OPERATOR.md.
