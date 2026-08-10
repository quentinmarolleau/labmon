#!/usr/bin/env bash
# Parse both compose files, so a syntax error is caught before the push
# rather than by the smoke job twenty minutes later.
#
# docker-compose.client.yml is checked here because nothing else checks it:
# the smoke job starts the server stack and never touches the client one,
# so an error in it would ship and surface on a user's sensor machine.
#
# Skipped rather than failed when Docker is unavailable. This runs on every
# commit that touches a compose file, and a laptop with the daemon stopped
# is a normal state, not a reason to block the commit. CI has no such
# excuse and runs the same check with Docker guaranteed present.

set -euo pipefail

if ! docker version >/dev/null 2>&1; then
    echo "compose-config: docker unavailable, skipping" >&2
    exit 0
fi

for file in docker-compose.yml docker-compose.client.yml; do
    if ! docker compose -f "$file" config -q; then
        echo "compose-config: $file did not parse" >&2
        exit 1
    fi
done
