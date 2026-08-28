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

# Resolve the destination against the caller's directory *before* moving,
# so `../labmon-ca.crt` means what the operator typed rather than
# something relative to the repository.
case "$DESTINATION" in
    /*) ;;
    *) DESTINATION="$PWD/$DESTINATION" ;;
esac

# `docker compose` finds the stack by looking for a compose file in the
# current directory, so run from the repository root. Without this the
# script works from there and nowhere else, failing with compose's own
# "no configuration file" rather than with anything said below.
cd "$(dirname "$0")/.."

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

# `cp` brings the mode out of the container with it, which is owner-only.
# The containers that read this file run as an unprivileged user, so an
# owner-only copy is one they cannot open — and there is nothing here to
# protect: the file is a public certificate, as the header above says.
chmod 644 "$DESTINATION"

# Without openssl there is no way to tell a good export from a truncated
# one. Say that, rather than reporting a file that is probably fine as
# unreadable — the check is the optional part here, not the export.
if ! command -v openssl >/dev/null 2>&1; then
    echo "Wrote $DESTINATION"
    echo
    echo "openssl is not installed, so it could not be checked. Confirm it"
    echo "looks like a certificate before handing it to a client."
    exit 0
fi

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
