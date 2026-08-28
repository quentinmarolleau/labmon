# Your instrument, built on top of the labmon image.
#
# This is a layer over the repository's own Dockerfile, not a replacement
# for it:
#
#   python:3.14-slim  ->  labmon:latest  ->  this file
#                         (repo root)        (your copy)
#
# labmon:latest is built from the repository rather than pulled, so it has
# to exist locally first, or the FROM below fails with "pull access
# denied":
#
#   docker compose build          # from the repo root
#
# After that, `docker compose up` builds this image for you if the service
# pasted from compose.snippet.yml points its `build:` at this directory.
# Building by hand is only needed to test it on its own:
#
#   docker build -t my-sensor .
#
# The `.` is the build context — the directory COPY can reach. A wheel
# from your vendor has to sit next to this file; COPY cannot climb out.
FROM labmon:latest

# The base image ends as the unprivileged `labmon` user, so anything that
# installs has to ask for root back and hand it in again before the
# entrypoint. `COPY` is unaffected: it runs as root whatever USER is set.
USER root

# Your instrument's Python package. uv is already in the base image; the
# --python flag points it at the venv labmon itself was installed into,
# which is not the interpreter uv would pick on its own.
#
# A wheel on PyPI:
#   RUN uv pip install --python /app/.venv/bin/python vendor-sdk==1.4.0
#
# A wheel the vendor emailed you, copied in alongside this Dockerfile:
#   COPY vendor_sdk-1.4.0-py3-none-any.whl /tmp/
#   RUN uv pip install --python /app/.venv/bin/python /tmp/vendor_sdk-1.4.0-py3-none-any.whl
#
# System libraries the SDK links against:
#   RUN apt-get update && apt-get install -y --no-install-recommends libusb-1.0-0 \
#       && rm -rf /var/lib/apt/lists/*

COPY sensor_continuous.py sensor_triggered.py /app/

# Back to the unprivileged user the base image runs as.
USER labmon

# Overrides the base image's ENTRYPOINT ["mock-sensor"]. Swap in
# sensor_triggered.py for a container a timer starts and stops.
ENTRYPOINT ["python", "/app/sensor_continuous.py"]
