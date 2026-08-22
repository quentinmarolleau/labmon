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

The other profile is `logs`, which adds Loki and Alloy to collect every
container's output. It is off by default and is independent of `demo`, so
a server that wants log aggregation but no simulated sensors sets
`COMPOSE_PROFILES=logs`. See [`docs/logging.md`](logging.md), including
what mounting the Docker socket costs.

Note that a profile is needed to stop its services as well as to start
them. `docker compose down` on a stack brought up with
`COMPOSE_PROFILES=demo,logs` leaves Loki and Alloy running unless the same
profiles are set.

`GRAFANA_PLUGINS` is the other variable to leave blank on a server. It
lists Grafana panel plugins to fetch at startup, and the demo sets it to
the one plugin its detuning gauge needs. Unset, Grafana installs nothing
and needs no network when it boots — which is usually what you want on a
server, at the cost of that one panel not rendering. See
[`docs/demo-stack.md`](demo-stack.md#the-dashboard-needs-one-panel-plugin).

## Why InfluxDB's healthcheck opens a socket

It connects to port 8181 and closes again, rather than calling `/health`.

Every influxdb3 endpoint answers 401 without a token, and the token is
issued by InfluxDB itself, so during first-time setup there is none to
send — a check that required one could never go healthy on a fresh install.
An HTTP probe therefore had to accept 401 as proof of life, and influxdb3
logged `the request was not authenticated` at ERROR for every one of them,
once per interval, forever. A TCP connect sends no request, so there is
nothing to reject and nothing to log.

It proves the listener is bound rather than that HTTP answers, which is all
the 401 probe established either.

Worth knowing when reading logs: `the request was not authenticated` now
indicates a *real* auth failure. It used to be healthcheck noise that had to
be filtered out first, which masked genuine ones.

## One thing in the logs that looks wrong and is not

**A container called `labmon-init-state-dirs` exits immediately.** That is
correct — it prepares `.influxdb3/data` and `.grafana/data` and stops. Docker
creates a missing bind-mount source as `root:root`, which would leave
InfluxDB (uid 1500) and Grafana (uid 472) unable to write to their own data
directories, so on a fresh clone InfluxDB would fail to start at all. The
other services wait for it via `service_completed_successfully`.

## Exposing the server to the LAN

InfluxDB and Grafana bind to `127.0.0.1` by default, so a fresh install
is reachable only from the machine running it. Making the server
reachable from the rest of the LAN takes three things:

1. **Set `LABMON_BIND_ADDRESS=0.0.0.0` in `.env`**, which publishes both
   ports on every interface.

   The default is loopback because the quickstart is run on laptops as
   well as servers, and Grafana answers a login prompt whose default
   password is `admin`. Publishing that to a café or campus network by
   default is the wrong trade; a server is a deliberate deployment and
   can say so in one line.

   **Set a real `GRAFANA_ADMIN_PASSWORD` at the same time.** Opening the
   port is the moment the default password stops being a local
   convenience.

2. **A stable address for the server.** Either set a DHCP reservation for
   its LAN IP on your router, or give it a fixed hostname (e.g. via
   `/etc/hosts` on each client, or your router's local DNS if it has one).
   Clients point at this address directly (see
   [`docs/client-setup.md`](client-setup.md)) — there's no service
   discovery (mDNS/Avahi) here, by design, to keep the setup simple.
3. **Open the two ports on the server's firewall**, if one is active.
   e.g. on a `ufw`-managed host:
   ```bash
   sudo ufw allow 8181/tcp   # InfluxDB
   sudo ufw allow 3000/tcp   # Grafana
   ```
   With the `tls` profile on these become 8443 and 3443 — see
   [Encrypting client and viewer
   traffic](#encrypting-client-and-viewer-traffic).

Viewers reach Grafana at `http://<server-ip-or-hostname>:3000`; clients
write to InfluxDB at `http://<server-ip-or-hostname>:8181`.

## Encrypting client and viewer traffic

By default the stack talks plain HTTP/gRPC on 8181 and 3000, so
`INFLUXDB3_AUTH_TOKEN` crosses the LAN in clear text. On a trusted lab
network that is an accepted tradeoff rather than an oversight, and it
stays the default.

The `tls` profile removes it. One extra container, a Caddy reverse proxy,
terminates TLS in front of both services — 8443 for InfluxDB, 3443 for
Grafana — while the plain ports keep answering exactly as before. Nothing
about turning it on breaks a client that has not moved yet, so a running
deployment migrates one machine at a time.

Certificates come from Caddy's own embedded CA rather than from Let's
Encrypt. A lab server has no public DNS name, so ACME has no way to
validate it ([#66](https://github.com/quentinmarolleau/labmon/issues/66)
tracks the case where that changes). Instead the stack signs its own, and
each client is given one root certificate to trust.

### Turning it on

1. **List the addresses clients use.** A certificate is only valid for the
   names and addresses it was issued for, so put them in the server's
   `.env` — one list per port, entries separated by a comma *and* a
   space:

   ```bash
   LABMON_TLS_INFLUXDB_SITES="https://192.168.1.50:8443, https://lab-server:8443"
   LABMON_TLS_GRAFANA_SITES="https://192.168.1.50:3443, https://lab-server:3443"
   LABMON_TLS_DEFAULT_SNI=192.168.1.50
   ```

   Set `LABMON_TLS_DEFAULT_SNI` to whichever address clients actually
   dial. A client connecting to a bare IP sends no server name along with
   the handshake, leaving the proxy nothing to pick a certificate with —
   and it then drops the connection, which surfaces as the server being
   unreachable rather than as a trust problem.

2. **Start the profile and export the root:**

   ```bash
   COMPOSE_PROFILES=tls docker compose up -d --wait
   ./scripts/export-ca.sh
   ```

   Add `tls` to whatever profiles the deployment already uses. The script
   writes `labmon-ca.crt`. That file is a public certificate, not a
   secret — the private key stays inside the `caddy-data` volume and
   never leaves the server — but a *substituted* root is exactly what an
   interceptor would need, so copy it by a route that reliably delivers
   the right file.

3. **Point each client at the proxy** and hand it the root — see
   [`docs/client-setup.md`](client-setup.md#connecting-over-tls).

4. **Send viewers to `https://<server>:3443`.** A browser that has not
   been given the root treats the site like any other self-signed one and
   warns before loading it; importing `labmon-ca.crt` into the browser's
   or the operating system's trust store clears that. Nothing else about
   Grafana changes.

5. **Move the firewall over.** Open 8443 and 3443; close 8181 and 3000 to
   the LAN once the last client and viewer has switched. Add 3444 if
   clients will ship logs as well as readings — see [Logs from other
   machines](logging.md#logs-from-other-machines), which also covers the
   credential that port needs before it accepts anything.

### What is encrypted, and what is not

The boundary, and only the boundary. Grafana still reaches InfluxDB, and
Alloy still reaches Loki, over plain HTTP inside the Docker network — one
bridge on one host, where encryption buys nothing and would mean handing
trust to every service as well as to every client. That is why
`insecureGrpc: true` stays in the provisioned datasource with the profile
on (see [`docs/grafana.md`](grafana.md)).

TLS also does nothing about the token itself. It stops the credential
being readable in transit; every client still holds an admin credential
at rest, which is the subject of the next section.

### Certificate lifecycle

Nothing here needs renewing by hand, and nothing needs redistributing
after the first time:

| | Valid for | Rotated by |
|---|---|---|
| Leaf, served to clients | 12 hours | Caddy, automatically |
| Intermediate | 7 days | Caddy, automatically |
| Root, distributed to clients | ~10 years | Nothing — it is the anchor |

Clients verify against the root, so the leaf and intermediate churning
underneath is invisible to them. What matters instead is the `caddy-data`
volume, which holds the CA's private key. Delete it and Caddy generates a
fresh root on next start, at which point every client rejects the server
until it is given the new file. Back it up with the rest of the stack's
volumes, or accept that losing it means one round of visiting every
client.

## One token, and it is an admin token

Every client holds the *same* `INFLUXDB3_AUTH_TOKEN`, and that token
grants full control of the database: write, delete, reconfigure, mint
further tokens.

So the blast radius of one mislaid client is the whole historical record.
A machine taped to an optical table, shared between students, or carried
between buildings has the same authority over your data as the server
does.

This is a platform constraint rather than a choice. **InfluxDB 3 Core
issues admin tokens only** — the per-client, write-only, least-privilege
tokens a deployment like this would otherwise use are an Enterprise
feature. There is no narrower credential to hand a sensor.

What follows from that:

- **Site clients accordingly.** Treat any machine holding the token as
  trusted infrastructure, because it is.
- **Keep port 8181 off network segments that do not need it.** This is
  the strongest argument for running the `tls` profile: once clients
  reach InfluxDB through the proxy, the database's own port need not be
  reachable from the LAN at all.
- **TLS does not solve this.** It protects the token in transit. Every
  client still holds an admin credential at rest.
- **Rotate on any suspicion**, using the drill below.

### Rotating the token

One command on the server, then every client. Expect writes to fail in
between, which is why this is worth rehearsing before you need it:

```bash
# On the server — mint the replacement first, so there is no window
# with no valid token at all.
docker compose exec influxdb influxdb3 create token --admin
```

Put the new value in the server's `.env`, restart the stack, then update
each client's env file and restart its sensor. Revoke the old token last,
once every client is confirmed writing again:

```bash
# Both need a valid token themselves; the container already has the
# server's in its environment.
docker compose exec influxdb sh -c \
  'influxdb3 show tokens --token "$INFLUXDB3_AUTH_TOKEN"'
docker compose exec influxdb sh -c \
  'influxdb3 delete token --token-name <name> --token "$INFLUXDB3_AUTH_TOKEN"'
```

Clients buffer while they cannot write (see
[`docs/latency.md`](latency.md)), so a rotation completed within the
queue's depth loses nothing.

## Distributing the auth token

Copy it out of band — e.g. `scp` the value directly into each client's
own env file (see [`docs/client-setup.md`](client-setup.md)) — never
commit it, and never send it over an unencrypted channel you don't
already trust (email, chat, etc.).
