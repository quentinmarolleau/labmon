# AGENTS.md

Condensed operating notes for AI coding agents working in this repo. Full
detail (rationale, workflow philosophy) is in [CONTRIBUTING.md](CONTRIBUTING.md);
this file is the quick-reference version.

## Stack

- Python 3.12+, managed with `uv` (not pip/poetry/conda).
- Local infra: `docker compose up -d --wait` (InfluxDB 3 Core + Grafana).
- Package layout: `src/labmon/`, tests in `tests/`, local type stubs for
  untyped third-party deps in `typings/`.

## Commands

```bash
uv sync
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=100
uv run ruff check .
uv run basedpyright
uv run typos
```

All four must pass before a PR is opened — this is exactly what CI (`.github/workflows/ci.yml`) runs.

## Hard rules

- Never push directly to `main` — it's branch-protected (enforced for
  admins too). Always work on a branch, open a PR.
- Commit messages must be Conventional Commits format, verified with
  `cog verify "<message>"` before committing (cocogitto is installed).
- Every commit must be signed. `main` has `required_signatures` enabled,
  so an unsigned commit anywhere in a branch blocks the merge even with
  all checks green. Verify with `git log --format='%h %G? %s' main..HEAD`
  — your own commits must show `G` (`N` is unsigned; `E` on a GitHub
  merge commit is fine, its key is not held locally).
- Coverage must stay at 100% (`--cov-fail-under=100` in CI). Don't add
  `# pragma: no cover` to dodge writing a real test — only use it for
  code that's genuinely untestable in a meaningful way.
- One logical change per commit; one focused concern per PR.

## Where things are

- `src/labmon/influx.py` — shared InfluxDB client config
- `src/labmon/writer.py` — queue-backed async writer (`PointWriter`)
- `src/labmon/sensors/` — sensor scripts
- `grafana/` — provisioned datasource + dashboards (see `docs/grafana.md`)
- `docs/*.md` — one usage doc per component
