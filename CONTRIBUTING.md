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

## Signed commits

> [!CAUTION]
> **Every commit that reaches `main` must be signed**, and this is
> enforced rather than requested: `main` has GitHub's
> `required_signatures` protection turned on, so an unsigned commit
> makes the merge button refuse no matter who is merging or how green
> the checks are.
>
> It applies to every commit in a pull request, not just the tip — one
> unsigned commit five back blocks the whole branch. Set this up before
> you start, or you will be re-signing history later.

Either signing method GitHub accepts works. SSH is the shorter setup if
you already push over SSH:

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub   # your key
git config --global commit.gpgsign true
```

Signing now works, but *checking your own* signatures locally does not
yet — git needs to be told which keys it should trust, and with SSH
there is no keyring to consult. Without this one extra step the check
below reports `N`, as though nothing had been signed at all:

```bash
printf '%s %s\n' "your@email" "$(cat ~/.ssh/id_ed25519.pub)" \
  >> ~/.config/git/allowed_signers
git config --global gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
```

This affects only your local view. A commit signed without it is a
properly signed commit, and GitHub verifies it either way.

For GPG, see [GitHub's guide][gh-signing]. Whichever you pick, the key
also has to be added to your GitHub account, under the **Signing keys**
section rather than the authentication one — a key that signs locally
but is not registered shows as *Unverified*, which counts as unsigned.

Check before pushing:

```bash
git log --show-signature -1                # one commit, in detail
git log --format='%h %G? %s' main..HEAD    # your commits on this branch
```

`G` is what you want on each of your own commits; `N` means unsigned —
or, if you sign over SSH, that `allowed_signers` above is missing.
`E` shows up on merge commits GitHub created, and is not a problem —
GitHub signs those with a key your machine has no copy of, so git cannot
check it locally even though GitHub reports it verified.

If you have already committed unsigned, re-sign the whole branch rather
than adding a new commit on top:

```bash
git rebase --exec 'git commit --amend --no-edit -S' main
```

[gh-signing]: https://docs.github.com/en/authentication/managing-commit-signature-verification

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

Check your commits are signed while you are here — see [Signed
commits](#signed-commits). It is the one requirement no CI run reports,
because it is the merge that refuses rather than a check that fails.

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
