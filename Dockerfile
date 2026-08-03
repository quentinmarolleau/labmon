# Builds labmon:latest — labmon's own code, and nothing beyond it.
#
# Every service in both compose files runs this one image: the mock
# sensors, serial-sensor, the demo feeder, and the client stack. It is
# also the base that templates/custom-sensor/ builds on.
#
# That makes it the wrong place for an instrument's own dependencies. A
# vendor SDK added here is installed into the image every other sensor
# runs from, so an SDK that fails to install stops the whole stack
# building; and this file is version-controlled, so the edit collides
# with every `git pull`. Put those in your own copy of
# templates/custom-sensor/Dockerfile, which starts FROM this image.

FROM python:3.14-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first so this layer is cache-hit on source-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# hatchling's build needs README.md present (pyproject.toml: readme = "README.md").
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Python block-buffers stdout when it is not a terminal, which it never is
# in a container. A sensor printing one short line per reading fills that
# buffer so slowly that `docker compose logs` shows nothing for twenty
# minutes, and a log collector has nothing to collect — both of which look
# like a dead sensor rather than a buffering artefact.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["mock-sensor"]
