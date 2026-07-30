# Deployment

Everything in the [Quickstart](../README.md#quickstart) runs on one
machine. A real lab setup is a small LAN with three roles:

- **Server** — one central machine running InfluxDB3 + Grafana
  (`docker-compose.yml`).
- **Clients** — separate machines (e.g. a Raspberry Pi wired to a sensor)
  pushing data to the server over the network — see
  [`docs/client-setup.md`](client-setup.md).
- **Viewers** — any other computer in the room opening Grafana in a
  browser.

This doc covers the server side: exposing it to the LAN, and the
`COMPOSE_PROFILES` toggle between "local demo" and "real server."

## Demo vs. server: `COMPOSE_PROFILES`

The five `mock-*` sensor services in `docker-compose.yml` are tagged
`profiles: [demo]`, so they only start when the `demo` profile is active.
`.env.example` ships with `COMPOSE_PROFILES=demo`, which Compose reads
automatically — no `--profile` flag needed — so `docker compose up -d
--wait` keeps giving you today's full local demo (InfluxDB, Grafana, and
all five mock sensors) if you copy `.env.example` as-is.

For a real server, leave `COMPOSE_PROFILES` unset (or blank) in that
machine's `.env`: `docker compose up -d --wait` then starts only
`influxdb` and `grafana` — nothing simulates sensor data, since real
clients will be doing that over the network instead.

`GRAFANA_PLUGINS` is the other variable to leave blank on a server. It
lists Grafana panel plugins to fetch at startup, and the demo sets it to
the one plugin its detuning gauge needs. Unset, Grafana installs nothing
and needs no network when it boots — which is usually what you want on a
server, at the cost of that one panel not rendering. See
[`docs/demo-stack.md`](demo-stack.md#the-dashboard-needs-one-panel-plugin).

## Two things in the logs that look wrong and are not

**InfluxDB logs `the request was not authenticated` once a second.** That is
its own healthcheck. The token is issued by InfluxDB, so during first-time
setup there is no token to send, and a healthcheck that required one could
never go healthy — it therefore calls `/health` unauthenticated and treats
any response, including 401, as proof the server is up. Every endpoint
returns 401 without a token, so there is no quieter alternative. Worth
knowing mainly because it masks *real* auth failures: to see those, filter
the line out rather than trusting a plain grep for "unauthenticated".

**A container called `labmon-init-state-dirs` exits immediately.** That is
correct — it prepares `.influxdb3/data` and `.grafana/data` and stops. Docker
creates a missing bind-mount source as `root:root`, which would leave
InfluxDB (uid 1500) and Grafana (uid 472) unable to write to their own data
directories, so on a fresh clone InfluxDB would fail to start at all. The
other services wait for it via `service_completed_successfully`.

## Exposing the server to the LAN

`docker-compose.yml`'s port bindings (`8181:8181` for InfluxDB, `3000:3000`
for Grafana) already bind to `0.0.0.0` by default — Compose's short port
syntax doesn't restrict to `127.0.0.1`. So making the server reachable
from the rest of the LAN needs no compose changes, just:

1. **A stable address for the server.** Either set a DHCP reservation for
   its LAN IP on your router, or give it a fixed hostname (e.g. via
   `/etc/hosts` on each client, or your router's local DNS if it has one).
   Clients point at this address directly (see
   [`docs/client-setup.md`](client-setup.md)) — there's no service
   discovery (mDNS/Avahi) here, by design, to keep the setup simple.
2. **Open the two ports on the server's firewall**, if one is active.
   e.g. on a `ufw`-managed host:
   ```bash
   sudo ufw allow 8181/tcp   # InfluxDB
   sudo ufw allow 3000/tcp   # Grafana
   ```

Viewers reach Grafana at `http://<server-ip-or-hostname>:3000`; clients
write to InfluxDB at `http://<server-ip-or-hostname>:8181`.

## Security: plain HTTP, by design (for now)

This stack talks plain HTTP/gRPC, with no TLS — the same as the local
demo (see [`docs/grafana.md`](grafana.md)'s note on `insecureGrpc`). On a
real LAN this means `INFLUXDB3_AUTH_TOKEN` travels unencrypted between
clients and the server. That's an accepted tradeoff for a trusted lab
network, not an oversight — TLS (e.g. via a reverse proxy, or InfluxDB's
own TLS support) is a reasonable future upgrade if the network model ever
changes, but isn't built here.

## Distributing the auth token

Every client needs the same `INFLUXDB3_AUTH_TOKEN` the server was set up
with (see the root [`.env.example`](../.env.example)). Copy it out of
band — e.g. `scp` the value directly into each client's own env file
(see [`docs/client-setup.md`](client-setup.md)) — never commit it, and
never send it over an unencrypted channel you don't already trust (email,
chat, etc.).
