#!/usr/bin/env bash
# Copy the stack's CA root certificate out for distribution to clients.
#
# Every client verifies the server against this one file. It is a public
# certificate, not a secret — the private key stays in the caddy-data
# volume and never leaves the server — so it can travel by any route that
# reliably delivers the *right* file. That is the property to protect: a
# substituted root is what a man-in-the-middle would need.
#
# Usage:
#     ./scripts/export-ca.sh [DESTINATION]
#
# Writes ./labmon-ca.crt by default.

set -euo pipefail

DESTINATION="${1:-labmon-ca.crt}"

# Where Caddy's embedded CA keeps the root it signs leaves with. The
# intermediate beside it is served during the handshake, so clients need
# only this one.
ROOT_IN_CONTAINER="/data/caddy/pki/authorities/local/root.crt"

if ! docker compose ps --format '{{.Name}}' | grep -qx caddy; then
    echo "export-ca: the caddy container is not running." >&2
    echo "  Start it with COMPOSE_PROFILES=...,tls docker compose up -d" >&2
    exit 1
fi

docker compose cp "caddy:${ROOT_IN_CONTAINER}" "$DESTINATION"

# Fail loudly rather than hand over something unusable: a truncated or
# wrong-format file would only surface later, on a client, as a confusing
# verification error.
if ! openssl x509 -in "$DESTINATION" -noout -subject >/dev/null 2>&1; then
    echo "export-ca: $DESTINATION is not a readable certificate." >&2
    exit 1
fi

echo "Wrote $DESTINATION"
openssl x509 -in "$DESTINATION" -noout -subject -dates
echo
echo "Copy it to each client and point INFLUXDB_TLS_CA at it."
echo "See docs/client-setup.md."
