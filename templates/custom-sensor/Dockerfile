# A sensor container built on the labmon image.
#
# The base image has to exist locally first, since it is built from this
# repository rather than pulled:
#
#   docker compose build          # from the repo root, creates labmon:latest
#   docker compose -f docker-compose.client.yml build
#
# Then build this one from inside templates/custom-sensor/:
#
#   docker build -t my-sensor .
FROM labmon:latest

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

# Overrides the base image's ENTRYPOINT ["mock-sensor"]. Swap in
# sensor_triggered.py for a container a timer starts and stops.
ENTRYPOINT ["python", "/app/sensor_continuous.py"]
