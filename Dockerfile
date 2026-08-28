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
# Pinned like every other executable this build pulls in. `latest` has
# broken builds before, and a build that breaks with no change in the
# repository is expensive to diagnose precisely because the diff is empty.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

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

# Everything above this line runs as root because installing does. What
# the image is built to run does not: a sensor reads a device and writes
# to a database over the network, and neither needs uid 0. Container root
# is not host root, so the practical risk was modest — but this is the
# first finding any container scan reports, and a lab that runs one
# before deploying the stack should find nothing rather than this.
#
# `dialout` owns the serial device nodes on Debian and Ubuntu, where its
# gid is 20 and matches the group the host puts on /dev/tty*. A host that
# numbers it differently — Arch, Fedora — passes its own gid through with
# `group_add`, since a bind-mounted device carries the host's numbers
# rather than the container's names. See docs/serial-sensor.md.
RUN groupadd --system --gid 10001 labmon     && useradd --system --uid 10001 --gid labmon --shell /usr/sbin/nologin        --home-dir /home/labmon --create-home labmon     && usermod --append --groups dialout labmon     && chown labmon:labmon /app

# The roster cache lands under $HOME, which for uid 0 was /root and is
# now a directory this user owns. Set explicitly because a container
# started without a login shell inherits no HOME at all.
ENV HOME=/home/labmon

USER labmon

ENTRYPOINT ["labmon", "mock-sensor"]
