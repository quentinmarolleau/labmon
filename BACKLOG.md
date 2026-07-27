# Backlog

Forward-looking ideas, unordered by nature — this file tracks them along
with a coarse effort estimate and a suggested priority order. Not a
commitment or a roadmap with dates, just a reference so ideas aren't
lost.

Effort scale: **S** (a PR or two, low uncertainty) / **M** (several PRs,
some real design decisions) / **L** (substantial new subsystem, real
hardware/external-library uncertainty) / **XL** (open-ended, R&D-flavored).

## BL-1 — Data acquisition + conversion script for client machines

**What**: The first real (non-mock) sensor script — reads an actual
microcontroller (e.g. an Arduino Due over serial), applies a calibration
curve to convert a raw signal (e.g. volts) into a physical quantity, and
writes it through the existing pipeline. This is what `mock-sensor` has
stood in for since the client/server networking work landed.

**Affects**: a new module (e.g. under `src/labmon/sensors/`), a new
`pyserial`-style dependency, a calibration-curve config format,
`docker-compose.client.yml` / `deploy/labmon-sensor.service` (swap the
command from `mock-sensor` to this script), `docs/client-setup.md`.
Reuses `labmon.writer.PointWriter` and `labmon.influx.get_client`
unchanged — both are already generic over "a `Point` from somewhere."

**Effort**: L — real hardware I/O that can't be fully verified without
physical hardware in hand (tests will mostly mock the serial layer);
calibration-curve handling is a genuine design surface on its own.

## BL-2 — Dimensioned quantities via Pint

**What**: Replace today's "unit is just a free-text tag, values are bare
floats" approach with real dimensional analysis (via
[Pint](https://github.com/hgrecco/pint)), so unit conversions (e.g. a
calibration curve producing volts → kelvin) are handled safely rather
than by convention.

**Affects**: whatever does raw → physical-unit conversion (i.e. BL-1's
acquisition script) is where Pint actually earns its keep; `mock_sensor.py`
has no real conversion to do (it simulates already-in-physical-units
readings), so retrofitting Pint there would be unit-theater with no
payoff. `pyproject.toml` (new dependency), tests.

**Effort**: M on its own, but tightly coupled to BL-1 — best done *as
part of* BL-1's implementation, not as a standalone refactor.
Still worth its own line since Pint's scope (pass `pint.Quantity` objects
through `PointWriter`, or convert to a canonical unit + plain float right
before constructing a `Point`?) is a real design decision worth deciding
deliberately, not as a side effect of BL-1.

## BL-3 — TLS for inter-service networking

**What**: Encrypt InfluxDB/Grafana traffic between server and clients
(currently explicit plain HTTP by design, documented in
`docs/deployment.md`, protected only by the bearer auth token).

**Affects**: `docker-compose.yml` (InfluxDB TLS cert config, or a reverse
proxy like Caddy in front of it), `grafana/provisioning/datasources/influxdb3.yaml`
(`insecureGrpc` flips, trust config), `docs/deployment.md` (revises the
explicit "plain HTTP by design" section), a cert generation/distribution
story for clients.

**Effort**: M/L — the TLS setup itself is well-trodden, but distributing
trust to every client (or accepting cert-validation-disabled, which
defeats the point) adds real per-client operational overhead for a small
lab.

## BL-4 — Optional mDNS/Avahi server discovery

**What**: Let clients find the server by a `.local` name instead of a
static IP/hostname in their `.env` (the static-IP approach was an
explicit, deliberate choice in the networking plan, to keep things
simple).

**Affects**: `docker-compose.yml` (an `avahi-daemon` sidecar, likely
needing `network_mode: host` since Docker's default bridge network
doesn't relay multicast well — a real wrinkle against the existing
port-mapping model), `docs/deployment.md`.

**Effort**: M — not much code, but Docker+Avahi networking is fiddly, and
genuinely verifying multicast discovery needs a real second physical
machine on the LAN (can't be fully proven without one).

## BL-5 — Analog gauge digitization (webcam + digitization)

**What**: For lab equipment with no electrical output (an LCD readout, a
dial/pointer gauge), periodically photograph it with a webcam and
digitize the reading (OCR for LCD digits; CV/angle-detection for pointer
gauges) before pushing it through the same pipeline as any other client
sensor.

**Affects**: an entirely new component — camera capture, an OCR/CV
dependency (e.g. `pytesseract` for digits; pointer-gauge reading is a
harder, more bespoke CV problem), a calibration step mapping pixels/angle
to a physical value, scheduling. Downstream of the camera it reuses the
same `Point`/`PointWriter` pipeline as BL-1.

**Effort**: XL — the most open-ended item. LCD OCR is fairly tractable;
analog pointer-gauge reading robust enough for unattended use (varying
lighting, angle, glare) is a genuinely hard, somewhat research-y problem,
and best iterated against the *actual* gauge hardware/lighting rather
than designed speculatively.

## BL-6 — Proper documentation site (Read the Docs)

**What**: Move beyond scattered `docs/*.md` files rendered ad hoc by
GitHub, to a real documentation site (Sphinx or MkDocs, hosted on
readthedocs.org) — searchable, versioned, with generated API reference
for `src/labmon` alongside the hand-written usage docs.

**Affects**: a new Sphinx/MkDocs project structure under `docs/`
(`conf.py`/`mkdocs.yml`, an index + toctree/nav), a `.readthedocs.yaml` at
repo root, `pyproject.toml` (new `docs` dependency group), migrating
today's `docs/*.md` content into the new structure, README (link to the
hosted site). Requires connecting a Read the Docs account/project too —
an external-service step, similar to how Codecov needed a token wired
into CI.

**Effort**: M — the tooling itself is well-trodden and mostly mechanical
to wire up; the real work is migrating/reorganizing existing content and
deciding a navigation structure that still makes sense once BL-1/BL-2/
BL-3 each add their own docs.

**Note**: at 5 short markdown files today, this could be seen as ahead of
actual need — but prioritized second regardless: the case for doing it
early is that BL-1's hardware docs, BL-2's unit-handling docs, and
BL-3's TLS/cert setup docs then get authored directly into the new
structure as they land, rather than written into `docs/*.md` first and
migrated/reorganized later.

## Suggested priority order

1. **BL-1 (+ BL-2 folded in)** — the direct next step after the
   networking work: it's what makes labmon monitor anything real instead
   of a simulation, and everything built so far (server, client
   packaging, resilience, dashboards) exists to support this. Do Pint
   adoption as part of this work, scoped to the acquisition script's
   conversion logic — not a standalone refactor of the mock/demo path,
   which has no real unit-safety problem to solve.
2. **BL-6 (Read the Docs)** — moved up from a later slot: getting the new
   structure in place now means BL-1/BL-2/BL-3's own docs get authored
   directly into it as they land, instead of written into `docs/*.md` and
   migrated later.
3. **BL-3 (TLS)** — worth doing if/when the deployment model extends past
   a single trusted lab LAN (e.g. a client reachable only over a VPN or
   less-trusted network). Not urgent for a genuinely local, physically
   secured lab network — revisit if that assumption changes.
4. **BL-4 (mDNS/Avahi)** — pure convenience on top of an already-working
   discovery mechanism (static IP/hostname). Nice polish, doesn't unblock
   or de-risk anything else.
5. **BL-5 (digitization)** — highest effort, most uncertain payoff, and
   the least dependent on/blocking of the other items. Best tackled once
   there's a concrete gauge in hand to iterate against, rather than
   designed in the abstract.
