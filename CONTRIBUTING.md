# Contributing

## Workflow

- No direct pushes to `main` — it's branch-protected, enforced even for admins.
- One branch per change, one PR per branch. Branch names are free-form but
  descriptive (`feat/...`, `fix/...`, `docs/...`, `ci/...`, `chore/...`,
  `test/...`).
- Keep PRs atomic: one feature/fix/change per commit, and prefer one
  focused PR over a large mixed one. Split unrelated changes into
  separate PRs even if you noticed them at the same time.
- All required checks must pass before merge: `lint`, `typecheck`,
  `typos`, and `test` (run across Python 3.12/3.13/3.14). No approvals are
  currently required (solo maintainer) — that will change once there are
  other contributors.

## Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/),
enforced with [cocogitto](https://github.com/cocogitto/cocogitto):

```bash
cog verify "feat: add thing"   # check a message before committing
cog check                      # check the whole history
```

Common types used in this repo: `feat`, `fix`, `docs`, `test`, `ci`,
`chore`. Write the message body around *why*, not just *what* — the diff
already shows what changed.

## Before opening a PR

```bash
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=100
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run typos
```

All five must pass locally — they're exactly what CI runs. Drop
`--check` to have the formatter apply its changes instead of reporting
them.

## Testing

- **100% line coverage is enforced in CI** (`--cov-fail-under=100`). This
  is a floor, not a target to game: don't pad coverage with tests that
  don't assert anything meaningful.
- Mock at the I/O boundary, not the internals. For example, testing
  `run()` in `mock_sensor.py` patches `signal.signal`, `time.sleep`,
  and `get_client()` — enough to exercise the real logic without touching
  a live InfluxDB instance or blocking forever — rather than mocking
  `PointWriter` itself.
- **The smoke job is the exception to all of the above.** `.github/
  workflows/smoke.yml` starts the real stack from a fresh checkout,
  follows the quickstart's token bootstrap, and runs every dashboard query
  through Grafana. It has no coverage requirement and mocks nothing — its
  whole job is to catch what unit tests structurally cannot, like a bind
  mount created with the wrong owner. To run it yourself, bring the stack
  up and then `python3 scripts/smoke_dashboard.py` — adding
  `--password "$GRAFANA_ADMIN_PASSWORD"` if you set one in `.env`, which
  Compose reads but your shell does not.
- `# pragma: no cover` is reserved for code that is genuinely untestable
  in a meaningful way (e.g. the `if __name__ == "__main__":` guard, where
  testing it would only prove Python's own import mechanism works, not
  our code). Don't reach for it to avoid writing a real test.

## Docs

- Component usage docs live in `docs/` (one file per component, e.g.
  `docs/grafana.md`, `docs/mock-sensor.md`).
- See [`AGENTS.md`](AGENTS.md) for a condensed, agent-oriented version of
  this document.

## License

GPLv3 — see [LICENSE](LICENSE). By contributing, you agree your
contribution is licensed under the same terms.
