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

## Git hooks

Optional, and worth the one command:

```bash
uv run pre-commit install --allow-missing-config
```

Cloning installs nothing, so skipping this leaves everything exactly as
it was — CI is still the gate. What it buys is finding a formatting slip
in half a second instead of after a push and a runner wait.

`--allow-missing-config` matters more than it looks: without it, every
commit on a branch that predates this file fails with "No
`.pre-commit-config.yaml` file was found", including merges of older
branches. With it, the hooks quietly do nothing there.

The hooks are split by what they cost:

| When | What runs | Roughly |
|---|---|---|
| every commit | `gitleaks`, `ruff format`, `ruff check`, `typos` | 0.6 s |
| commit message | `cog verify` | instant |
| every push | `basedpyright`, `pytest` | 14 s |

`docker compose config` joins the commit hooks only when a compose file is
staged, and skips itself if the Docker daemon isn't running.

Two things worth knowing:

- **The hooks run the tools from `uv.lock`**, via `uv run --locked` — the
  same commands and versions as `.github/workflows/ci.yml`. There are no
  tool versions in `.pre-commit-config.yaml` to drift out of step, so
  `uv lock --upgrade` moves the hooks and CI together.
- **`git commit --no-verify` skips all of it**, deliberately. A gate you
  can't bypass is a gate people stop using. Installing over hooks you
  already have keeps yours too: pre-commit moves them to
  `.git/hooks/*.legacy` and runs them first.

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

With the hooks installed, pushing has already run the last three of
these, so this list is the belt to their braces — and the one to reach
for when you've been using `--no-verify`.

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

## Releasing

Versions and the changelog are both derived from the commit history by
[cocogitto](https://docs.cocogitto.io), configured in `cog.toml`. Nothing
here is written by hand, which is the reason the commit conventions above
are enforced rather than encouraged.

One GitHub milestone is one minor version. A release is cut when its
milestone closes:

```bash
git switch main && git pull
cog bump --dry-run --auto      # inspect the version it derives
cog bump --auto                # or --version 0.2.0-beta.1 to pin it
git push --follow-tags
```

That writes `CHANGELOG.md`, updates the version in `pyproject.toml` and
`uv.lock`, commits, and tags. Then publish a GitHub release against the
tag, with prose that says what the version *does* — the generated
changelog says what changed, which is not the same thing.

Two things that will stop a bump, both deliberately:

- **A dirty or untracked tree.** Releases are cut from exactly what is
  committed. Working files belong in `.gitignore`.
- **Being on the wrong branch.** `branch_whitelist` is `main`.

Tags are SemVer (`v0.2.0-beta.1`) because that is what cog and the
milestones use. `pyproject.toml` is PEP 440 (`0.2.0b1`) because that is
what Python packaging requires. `uv version` converts between them; the
two spellings are the same version, not a drift.

Before 1.0, a minor version may change behaviour a deployment has to act
on. Say so in the release notes, under its own heading, with the change
required — see `v0.2.0-beta.1` and the port binding.

## Conduct

Participation is covered by the
[Code of Conduct](CODE_OF_CONDUCT.md) (Contributor Covenant 3.0).
Report a possible violation by email to q.marolleau-dev@pm.me rather than
in a public issue.

## Docs

- Component usage docs live in `docs/` (one file per component, e.g.
  `docs/grafana.md`, `docs/mock-sensor.md`).
- See [`AGENTS.md`](AGENTS.md) for a condensed, agent-oriented version of
  this document.

## License

GPLv3 — see [LICENSE](LICENSE). By contributing, you agree your
contribution is licensed under the same terms.
