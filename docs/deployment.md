# Deploying a server

Everything in the [Quickstart](../README.md#quickstart) runs on one
machine. A real lab is a small network with three roles.

```
   CLIENT                                       SERVER
   one per instrument                           one machine, always on
  ┌──────────────────────────┐                 ┌──────────────────────────────┐
  │  instrument              │                 │                              │
  │      │                   │   readings      │   InfluxDB 3           :8181 │
  │      ▼                   │  ──────────────►│      ▲                       │
  │  sensor script           │                 │      │ internal only         │
  │                          │                 │   Grafana              :3000 │
  │  Alloy                   │   log lines     │                              │
  │  (ships this machine's ──┼────────────────►│   Loki      (logs profile)   │
  │   logs)                  │      :3444      │                              │
  └──────────────────────────┘                 └───────────────┬──────────────┘
                                                               │
   VIEWER                                                      │ browser
   any laptop in the room  ◄───────────────────────────────────┘
   http://server:3000
```

| Role | Runs | Setup |
|---|---|---|
| **Server** | InfluxDB + Grafana, plus Loki and Alloy under the `logs` profile | this page |
| **Client** | A sensor script, on the machine the instrument is wired to | [`client-setup.md`](client-setup.md) |
| **Viewer** | A browser | nothing to install |

### Ports

| Port | Service | Who connects | Under the `tls` profile |
|---|---|---|---|
| 8181 | InfluxDB | sensor clients | 8443 |
| 3000 | Grafana | viewers' browsers | 3443 |
| 3444 | Loki push | client Alloy | 3444 (TLS only) |
| 3100 | Loki | nothing outside Docker | unchanged |

Grafana reaches InfluxDB, and the server's Alloy reaches Loki, over the
Docker network — those never cross the LAN and are not published.

## Choosing what runs: `COMPOSE_PROFILES`

Services are tagged with Compose profiles, and only start when their
profile is active. `.env.example` ships `COMPOSE_PROFILES=demo`, which
Compose reads on its own — no `--profile` flag needed.

| Profile | Adds | On a server |
|---|---|---|
| *(none)* | InfluxDB, Grafana | always |
| `demo` | six mock sensors, plus the ADC feeder and `serial-sensor` pair | **off** — real clients supply the data |
| `logs` | Loki and Alloy, collecting every container's output | optional, see [`logging.md`](logging.md) |
| `tls` | a Caddy reverse proxy terminating HTTPS | optional, see [below](#encrypting-client-and-viewer-traffic) |

So a server with log collection and no simulated data:

```bash
COMPOSE_PROFILES=logs
```

> [!WARNING]
> A profile is needed to **stop** its services as well as to start them.
> `docker compose down` on a stack brought up with
> `COMPOSE_PROFILES=demo,logs` leaves Loki and Alloy running unless the
> same profiles are set.

`GRAFANA_PLUGINS` is the other variable to leave blank on a server. It
lists Grafana panel plugins to fetch at startup, and the demo sets it to
the one plugin its detuning gauge needs. Blank, Grafana installs nothing
and needs no network when it boots, at the cost of that one panel not
rendering. See
[`demo-stack.md`](demo-stack.md#the-dashboard-needs-one-panel-plugin).

## Opening the server to the network

A fresh install binds InfluxDB and Grafana to `127.0.0.1`, so they answer
only from the machine running them. Three things make the server reachable
from the rest of the lab.

<details>
<summary><b>What "binds to 127.0.0.1" means</b></summary>

<br>

A machine has several network interfaces: its LAN card (say
`192.168.1.50`), maybe a Wi-Fi card, and always `127.0.0.1` — the loopback
interface, which is wired back to the same machine and nothing else. When a
program opens a port it says which interface to accept connections on.
`127.0.0.1:3000` accepts only from this machine; `0.0.0.0:3000` accepts
from every interface, which is what makes it reachable from other
computers.

Loopback is the default here because the quickstart is often run on a
laptop, and Grafana ships with the password `admin`. A server is a
deliberate deployment and can say so in one line.

</details>

**1. Publish the ports on every interface.** In the server's `.env`:

```bash
LABMON_BIND_ADDRESS=0.0.0.0
GRAFANA_ADMIN_PASSWORD=<something real>
```

> [!CAUTION]
> Change the Grafana password in the same edit. `admin`/`admin` is a local
> convenience while the port answers only to the machine itself, and
> nothing more than that once it is open.

**2. Give the server a stable address.** Either reserve its LAN IP on the
router's DHCP, or give it a fixed hostname — through your router's local
DNS, or `/etc/hosts` on each client. Clients dial that address directly;
there is no mDNS or other service discovery here, deliberately, to keep the
setup something you can read off a diagram.

**3. Open the ports on the server's firewall**, if it runs one:

```bash
sudo ufw allow 8181/tcp   # InfluxDB — sensor clients write here
sudo ufw allow 3000/tcp   # Grafana  — browsers connect here
```

Viewers then reach Grafana at `http://<server>:3000`, and clients write to
`http://<server>:8181`.

## Encrypting client and viewer traffic

Plain HTTP is the default, which means `INFLUXDB3_AUTH_TOKEN` crosses the
LAN in clear text. On a trusted lab network that is an accepted tradeoff
rather than an oversight — worth checking against your actual network
rather than assuming.

The `tls` profile removes it. One extra container, a
[Caddy](https://caddyserver.com/) reverse proxy, terminates TLS in front of
the services: 8443 for InfluxDB, 3443 for Grafana, 3444 for Loki's push
endpoint.

```
   client ──https──► Caddy :8443 ──http──► InfluxDB :8181
                       (server)             (Docker network, not published)
```

<details>
<summary><b>Why a private certificate authority, and not Let's Encrypt</b></summary>

<br>

Let's Encrypt proves you control a **public DNS name** before it issues a
certificate. A lab server usually has no public name — it is
`192.168.1.50`, or `lab-server` in the router's DNS — so there is nothing
for that check to validate against, and no public CA will issue for it.
([#66](https://github.com/quentinmarolleau/labmon/issues/66) tracks the
case where a deployment does have one.)

So the stack runs its own certificate authority instead: Caddy generates a
root certificate, keeps the private key on the server, and signs the
server's own certificates with it. Each client is given a copy of the root
— the public half — and from then on verifies the server against it. Same
mechanism a browser uses with a public CA, with a trust list of exactly
one entry that you distributed yourself.

</details>

### Turning it on

**1. List the addresses clients will dial.** A certificate is only valid
for the names and addresses it was issued for, so they go in the server's
`.env` — one list per port, entries separated by a comma *and* a space:

```bash
LABMON_TLS_INFLUXDB_SITES="https://192.168.1.50:8443, https://lab-server:8443"
LABMON_TLS_GRAFANA_SITES="https://192.168.1.50:3443, https://lab-server:3443"
LABMON_TLS_DEFAULT_SNI=192.168.1.50
```

> [!IMPORTANT]
> Set `LABMON_TLS_DEFAULT_SNI` to whichever address clients actually use. A
> client connecting to a bare IP sends no server name with the handshake,
> leaving the proxy nothing to pick a certificate with — it then drops the
> connection, which looks like the server being down rather than a trust
> problem.

**2. Start the profile and export the root certificate:**

```bash
COMPOSE_PROFILES=tls docker compose up -d --wait
./scripts/export-ca.sh
```

Add `tls` to whatever profiles the deployment already uses. The script
writes `labmon-ca.crt`.

> [!NOTE]
> `labmon-ca.crt` is a public certificate, not a secret — the private key
> stays in the `caddy-data` volume and never leaves the server. But a
> *substituted* root is exactly what an interceptor would need, so copy it
> by a route that reliably delivers the right file.

**3. Point each client at the proxy** and hand it the root — see
[`client-setup.md`](client-setup.md#connecting-over-tls).

**4. Send viewers to `https://<server>:3443`.** A browser that has not been
given the root warns before loading, as it would for any self-signed site;
importing `labmon-ca.crt` into the browser's or the OS trust store clears
that. Nothing else about Grafana changes.

**5. Move the firewall over.** Open 8443 and 3443, and 3444 if clients ship
logs ([Logs from other machines](logging.md#logs-from-other-machines) covers
the credential that port needs). Close 8181 and 3000 to the LAN once the
last client and viewer has switched.

Turning the profile on does not break a client that has not moved yet, so a
running deployment migrates one machine at a time.

### What is encrypted, and what is not

The boundary, and only the boundary. Grafana still reaches InfluxDB, and
the server's Alloy still reaches Loki, over plain HTTP inside the Docker
network — one bridge on one host, where encryption would mean handing trust
to every service as well as to every client. That is why `insecureGrpc:
true` stays in the provisioned datasource with the profile on (see
[`grafana.md`](grafana.md)).

TLS also does nothing about the token itself. It stops the credential being
readable in transit; every client still holds an admin credential at rest,
which is the next section.

### Certificate lifecycle

Nothing needs renewing by hand, and nothing needs redistributing after the
first time:

| | Valid for | Rotated by |
|---|---|---|
| Leaf, served to clients | 12 hours | Caddy, automatically |
| Intermediate | 7 days | Caddy, automatically |
| Root, distributed to clients | ~10 years | nothing — it is the anchor |

Clients verify against the root, so the leaf and intermediate churning
underneath is invisible to them.

> [!WARNING]
> The `caddy-data` volume holds the CA's private key. Delete it and Caddy
> generates a fresh root on next start, at which point every client rejects
> the server until it is given the new file. Back it up with the stack's
> other volumes, or accept that losing it means one round of visiting every
> client.

## One token, and it is an admin token

Every client holds the *same* `INFLUXDB3_AUTH_TOKEN`, and that token grants
full control of the database: write, delete, reconfigure, mint further
tokens. The blast radius of one mislaid client is the whole historical
record.

This is a platform constraint rather than a choice. **InfluxDB 3 Core
issues admin tokens only** — per-client, write-only tokens are an
Enterprise feature. There is no narrower credential to hand a sensor.

What follows:

- **Treat any machine holding the token as trusted infrastructure**,
  including one taped to an optical table or shared between students.
- **Keep 8181 off network segments that do not need it.** This is the
  strongest argument for the `tls` profile: once clients reach InfluxDB
  through the proxy, the database's own port need not be reachable from
  the LAN at all.
- **Distribute the token out of band** — `scp` it straight into each
  client's env file. Never commit it, never send it over email or chat.
- **Rotate on any suspicion**, using the drill below.

### Rotating the token

One command on the server, then every client. Writes fail in between, which
is why it is worth rehearsing before you need it.

```bash
# On the server. Mint the replacement first, so there is never a window
# with no valid token at all.
docker compose exec influxdb influxdb3 create token --admin
```

Put the new value in the server's `.env` and restart the stack, then update
each client's env file and restart its sensor. Revoke the old token last,
once every client is confirmed writing again:

```bash
docker compose exec influxdb sh -c \
  'influxdb3 show tokens --token "$INFLUXDB3_AUTH_TOKEN"'
docker compose exec influxdb sh -c \
  'influxdb3 delete token --token-name <name> --token "$INFLUXDB3_AUTH_TOKEN"'
```

Clients buffer while they cannot write (see [`latency.md`](latency.md)), so
a rotation completed within the queue's depth loses nothing.

## Setting up, and starting over

Two commands cover the database's whole lifecycle.

`labmon init` prepares a fresh instance: it asks for the admin token,
writes it into `.env`, and creates the database. Run it once per instance,
against a server that is already up.

```bash
docker compose up -d --wait influxdb
labmon init --retention 1y
```

`--retention` is the reason the command exists at all. A database is
created by the first write if nothing created it first, but one that
appears that way keeps readings for ever, and a retention period can only
be set when the database is created. Re-running `labmon init --retention
30d` on a database that already exists changes it, which is the one thing
a second run does.

`labmon reset-database` empties it again:

```bash
labmon reset-database              # asks you to type the database name
labmon reset-database --yes        # for a script that means it
```

> [!WARNING]
> This deletes every reading. The retention period is read first and put
> back, so a database that kept a year of readings still does — but the
> readings themselves are gone.

<details>
<summary><b>What a reset actually does to the data</b></summary>

<br>

`DELETE /api/v3/configure/database`, then `POST` to create it again under
the same name. Both succeed immediately, which is what lets the two be one
command rather than two with a wait between them.

The delete is *soft*, which is InfluxDB's own default. What is on disk is
renamed to `<name>-<timestamp>` and reclaimed by the server in its own
time, so a reset run by mistake is recoverable from that copy for a while
— by hand, and by someone who knows what they are doing, but recoverable.
It also means the disk space does not come back at once. `--hard` asks for
it now instead, which is the answer when the reason for the reset is a
full disk.

Either way the catalogue keeps a record of the deletion, which
`influxdb3 show databases` lists alongside the live ones.

**The admin token is not touched.** A token belongs to the instance rather
than to a database, so every sensor machine's `.env` keeps working across a
reset and no client needs visiting. That is what makes this safe to run on
a stack with clients on it — unlike rotating the token, above.

</details>

<details>
<summary><b>Why there is no "delete these readings" command</b></summary>

<br>

Because InfluxDB 3 Core cannot do it. Its delete granularity is a
database, a table, a cache, a trigger or a token — there is no
`DELETE ... WHERE`, and the query API is read-only. So a bad afternoon, or
one sensor's history, cannot be removed by predicate the way an SQL
database would allow.

What the engine offers instead is retention, which is why `--retention`
sits on `labmon init`: it drops anything older than the period you set,
automatically and for ever after.

For anything narrower, the shape that works is to export what you want to
keep, reset, and write it back — [`export.md`](export.md) already selects
by measurement, sensor and time window. It is not atomic and it is not
quick, but it is honest about what it is doing.

</details>

## Two things in the logs that look wrong

<details>
<summary><b><code>labmon-init-state-dirs</code> exits immediately</b></summary>

<br>

Correct. It prepares `.influxdb3/data` and `.grafana/data` and stops.
Docker creates a missing bind-mount source as `root:root`, which would
leave InfluxDB (uid 1500) and Grafana (uid 472) unable to write to their
own data directories — on a fresh clone InfluxDB would not start at all.
The other services wait for it through `service_completed_successfully`.

</details>

<details>
<summary><b>InfluxDB's healthcheck opens a socket instead of calling <code>/health</code></b></summary>

<br>

Every influxdb3 endpoint answers 401 without a token, and the token is
issued by InfluxDB itself, so during first-time setup there is none to
send — a check that required one could never go healthy on a fresh install.
An HTTP probe therefore had to accept 401 as proof of life, and influxdb3
logged `the request was not authenticated` at ERROR for every one, once per
interval, forever. A TCP connect sends no request, so there is nothing to
reject and nothing to log. It proves the listener is bound rather than that
HTTP answers, which is all the 401 probe established either.

Worth knowing when reading logs: `the request was not authenticated` now
means a *real* auth failure. It used to be healthcheck noise that masked
genuine ones.

</details>
