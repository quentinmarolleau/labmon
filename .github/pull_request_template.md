<!--
Keep this short. A PR nobody reads carefully is worse than no template.
Delete any section that has nothing to say.
-->

## What and why

<!--
The diff already shows what changed — spend this space on why, the same way
CONTRIBUTING asks commit bodies to. If it fixes an issue, "Closes #n".
-->

## Test plan

- [ ] `uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=100`
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run basedpyright`
- [ ] `uv run typos`

<!--
Touching docker-compose.yml, the Dockerfile, grafana/ or demo/? Bring the
stack up and run `python3 scripts/smoke_dashboard.py` as well — the unit
tests cannot see anything that only breaks once containers are running.

Verified something by hand? Say what you actually observed, not just that
you checked. "All 14 dashboard queries returned rows" beats "tested locally".
-->

## Notes for the reviewer

<!--
Backlog item this addresses (BL-n), anything you deliberately left out, and
any decision here you would like pushed back on.
-->
