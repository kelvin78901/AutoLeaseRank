# Project rules for macc workers (AutoLeaseRank)
- Interpreter is `/Users/kelvin/Downloads/autoleaserank/.venv/bin/python` (absolute: worktrees have no .venv); run it as `PYTHONPATH=. /Users/kelvin/Downloads/autoleaserank/.venv/bin/python`, tests with `PYTHONPATH=. /Users/kelvin/Downloads/autoleaserank/.venv/bin/python -m pytest -q <path>`.
- Offline only: no crawlers, no Playwright, no HTTP to the outside, nothing under alr/adapters/.
- DuckDB in tests: `duckdb.connect(":memory:")`; create tables through the same function production uses.
- Pydantic v2 models live in alr/schema.py; new models go in the module the contract names.
- Keep public function signatures exactly as the contract task's document specifies.
