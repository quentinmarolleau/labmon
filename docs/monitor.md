# Watching values in the terminal

`labmon monitor` opens a panel that redraws itself, showing where every
sensor stands and how its window has behaved. With a layout it is a
grid of tiles; without one, a table of everything the database has.

```bash
labmon monitor
labmon monitor --since 1h --refresh 5s
labmon monitor --measurement temperature
```

```
                                               labmon
╭─────────────┬───────────────┬─────────────────┬──────┬─────────┬─────────────────┬─────────┬──────╮
│ measurement │ sensor        │           value │ unit │ age     │         average │       σ │    N │
├─────────────┼───────────────┼─────────────────┼──────┼─────────┼─────────────────┼─────────┼──────┤
│ frequency   │ wavemeter-1   │ 2.765613016e+14 │ Hz   │ 1s ago  │ 2.765612997e+14 │ 1.6e+06 │  900 │
│ frequency   │ wavemeter-thz │     276.5613007 │ THz  │ 19h ago │                 │         │      │
│ position    │ beam-x        │            5.98 │ µm   │ 2s ago  │               0 │      19 │ 1797 │
│ position    │ beam-y        │           22.65 │ µm   │ 2s ago  │               0 │      16 │ 1797 │
│ power       │ laser-1       │           100.1 │ mW   │ 2s ago  │            95.0 │     8.8 │ 1797 │
│ pressure    │ chamber-1     │     1.80064e-07 │ mbar │ 2s ago  │        1.40e-07 │ 1.3e-08 │  360 │
│ pressure    │ dual-probe    │     6.84648e-07 │ mbar │ 21h ago │                 │         │      │
│ pressure    │ pirani-1      │        2.03e-08 │ mbar │ 2s ago  │         2.9e-08 │ 3.4e-08 │ 1797 │
│ temperature │ cryo-4k       │           4.301 │ K    │ 2s ago  │            4.08 │    0.31 │  360 │
│ temperature │ cryo-77k      │          74.807 │ K    │ 2s ago  │            77.6 │     1.3 │  360 │
│ temperature │ cryo-diode    │           43.39 │ K    │ 2s ago  │              28 │      11 │ 1797 │
│ temperature │ dual-probe    │         20.8026 │ K    │ 21h ago │                 │         │      │
│ temperature │ probe-158     │         21.0691 │ K    │ 23h ago │                 │         │      │
│ temperature │ room-1        │          20.996 │ °C   │ 2s ago  │           21.35 │    0.83 │  360 │
│ temperature │ room-2        │           21.66 │ °C   │ 2s ago  │           22.27 │    0.45 │  360 │
│ voltage     │ bias-monitor  │           2.847 │ V    │ 2s ago  │               0 │     2.2 │ 1797 │
╰─────────────┴───────────────┴─────────────────┴──────┴─────────┴─────────────────┴─────────┴──────╯
                       16 sensors, 4 quiet · window 30m · every 2s · 10:24:11
```

| Key | Does |
|---|---|
| `q` | Quit |
| `r` | Change refresh rate |
| `m` | Open the command palette |
| `?` | Show the keys, over the panel |

`r` opens a short menu — 1s, 2s, 5s, 10s, 30s, 60s, with the configured
interval spliced in — and opens it on the rate in force, so it says what
the panel is doing as well as what it could do. `escape` leaves it
alone. There is no key to force a refresh: the panel already refreshes
itself, so one early buys nothing.

```
╭─ refresh every ─╮
│        1s       │
│        2s       │
│        5s       │
│       10s       │
│       30s       │
│       60s       │
╰─────────────────╯
```

`m` opens Textual's command palette, where the colour theme can be
changed. The default is `nord`, chosen for being calm at a glance and
legible on a projector, which is where a panel beside an experiment tends
to end up.

`?` lists the keys above without leaving the panel, and closes on `?`,
`escape` or `q`.

Needs the `tui` extra, which brings in Textual:

```bash
pip install 'labmon[tui]'
```

Running it without that gives the install command rather than an import
traceback.

## Why not just leave Grafana open

Grafana is the right tool for a wall display and for digging through
history. It is the wrong one for the terminal that is already open next
to the experiment, and it is unavailable over a bare SSH session — which
is exactly the moment "is the cryostat still cold?" is worth asking.

## A layout of tiles

With no `[[monitor.panels]]` configured, `labmon monitor` shows the table
below and everything the database has. A layout replaces it with one
tile per sensor:

```bash
labmon monitor --config monitor.example.toml
```

```
        ╭────────────────────────────╮  ╭────────────────────────────╮  ╭────────────────────────────╮
        │        Cold finger         │  │         4 K stage          │  │          Chamber           │
        │      ╶─┐╶─╮ ╭─╴╶╮ ╷ ╷      │  │       ╷ ╷ ╶─╮╭─╮╶╮         │  │ ╶╮  ╭─╮╷ ╷╶─╮╶─┐╭─╴    ╭─╮ │
        │        │ ─┤ ├─╮ │ ╰─┤      │  │       ╰─┤ ┌─┘│ │ │         │  │  │  ╰─┤╰─┤┌─┘  │╰─╮ ╶─╴│ │ │
        │        ╵╶─╯•╰─╯╶┴╴  ╵      │  │         ╵•╰─╴╰─╯╶┴╴        │  │ ╶┴╴•╶─╯  ╵╰─╴  ╵╶─╯e   ╰─╯ │
        │             K              │  │             K              │  │            mbar            │
        │    temperature · 3s ago    │  │    temperature · 3s ago    │  │     pressure · 3s ago      │
        ╰────────────────────────────╯  ╰────────────────────────────╯  ╰────────────────────────────╯

        ╭────────────────────────────╮  ╭────────────────────────────╮
        │           Laser            │  │       Probe pressure       │
        │         ╭─╮╭─╮ ╷ ╷         │  │ ╭─╴ ╭─╮╷ ╷╭─╴╷ ╷╭─╮    ╭─╮ │
        │         ├─┤╰─┤ ╰─┤         │  │ ├─╮ ├─┤╰─┤├─╮╰─┤├─┤ ╶─╴│ │ │
        │         ╰─╯╶─╯•  ╵         │  │ ╰─╯•╰─╯  ╵╰─╯  ╵╰─╯e   ╰─╯ │
        │             mW             │  │            mbar            │
        │       power · 1s ago       │  │     pressure · 21h ago     │
        ╰────────────────────────────╯  ╰────────────────────────────╯
```

The value is drawn with `Digits`, which is what makes it readable from
across a room — the whole reason a tile beats a table row.

A layout lives either in the user configuration, under `[monitor]`, or
in a file of its own passed with `--config`. The two have different
lifetimes: a machine-wide window and cadence are settings, while "the
five things worth watching during a bakeout" is a document that belongs
beside the procedure. `--config` **replaces** the `[monitor]` section
rather than merging with it, so a layout can say "just these tiles"
without first editing the file it was avoiding.

```toml
refresh = "2s"
window  = "15m"

[[panels]]
sensor_id = "cryo-77k"
title = "Cold finger"
precision = 3
warn_above = 80.0
```

[`monitor.example.toml`](../monitor.example.toml) works through every
key.

**Tiles keep the order they were written in.** Unlike the fallback
table, which is alphabetical, a layout says which tile to look at first
and sorting it would discard exactly that.

**A panel naming a sensor that is not reporting still gets a tile.**
Dropping it is the failure this whole view exists to prevent: the tile
somebody put in their layout is the one they are watching for, and an
empty space says nothing while a marked tile says the reading has not
arrived.

Such a tile shows **the last reading the roster saw**, dimmed, with a red
border and a caption saying how long ago that was. A tile showing `—`
answers none of the questions asked of an instrument that stopped; what
it was reading when it stopped answers most. A sensor labmon has never
seen a reading from shows `—`, because there is nothing to show.

Thresholds do not fire on a remembered reading. A threshold is a claim
about the experiment right now, and an hour-old number cannot support
one — the tile is already marked as not reporting, which is the accurate
alarm to raise.

### Thresholds

`warn_above` and `warn_below` colour the tile and replace the
measurement in its footer with `> 80` or `< 4` — too hot and too cold
are different problems and the colour is the same.

They stay a glanceable colour change and do not grow into notifications.
Grafana already does alerting, and a panel that could page somebody is a
panel that has to be running.

An alarm outranks staleness on a tile: a reading that is out of range is
a fact about the experiment, and one that is merely old is a fact about
the network.

A sensor that is not reporting raises no alarm. There is no reading to
be out of range, and "nothing is arriving" is a different thing to say
than "it is too hot".

### Precision

`precision` gives exactly that many decimal places, trailing zeros
included: somebody who wrote `precision = 3` is saying the instrument
resolves that far. `format` forces `plain` or `scientific` where the
automatic choice reads badly for one particular sensor. `plain` is
positional notation at every magnitude, so a gauge reading `5e-05` is
written `0.00005`; it shortens nothing, it only spells the number
differently.

With neither, a tile shows the reading exactly as stored, the same as
the fallback table.

A precision that belongs to the *instrument* rather than to one tile
goes in `[[monitor.sensors]]` instead, where it also reaches the
fallback table — see below. A tile that names its own wins over it.

### Naming a measurement

`measurement` is optional, because most sensors write to one table.
Naming it is how a probe reporting both a temperature and a pressure
says which of them a tile is for. **The tile always prints the
measurement it settled on**, so what is on screen is never ambiguous
even when the configuration was.

## Rows stay where you left them

Ordered by measurement, then by sensor, alphabetically. Nothing about a
reading moves a row.

This matters more here than anywhere else: a panel sorted by age
reorders itself on every tick, so following one number means finding it
again first. Staleness is carried by colour, which does not need a
position to say anything.

## Only the average is rounded

A reading is shown **exactly as stored**. The sensor already rounded it
to the resolution it claims, and nothing here knows better.

The average and σ are rounded, and only against each other: σ is cut to
two significant figures and the average to the same decimal place, so
**the average is never printed to a finer resolution than σ**. That is a
statement about a computed quantity, whose shortest round-tripping form
runs to seventeen digits for readings that were only ever good to four.

σ deliberately does *not* touch the value beside it. It says how far a
quantity moved while nobody was looking, which is no statement at all
about how well the instrument measured it — a beam wandering 19 µm
across half an hour is at `-21.49 µm` right now, to far better than a µm.

## How many digits a sensor is worth

Some instruments write more digits than anybody wants to read at a
glance — anything behind a calibration, in particular, where a
conversion turns four honest digits into seventeen. `[[monitor.sensors]]`
fixes the display for one sensor, in the table and in any tile alike:

```toml
[[monitor.sensors]]
sensor_id = "beam-x"
precision = 2

[[monitor.sensors]]
sensor_id = "beam-y"
precision = 2
```

| Key | Means |
|---|---|
| `sensor_id` | Which sensor. Required. |
| `measurement` | Which of its tables, for a sensor that writes to two. Optional; a rule naming it wins over one that does not. |
| `precision` | Decimal places, trailing zeros included. |
| `format` | `auto`, `plain` or `scientific`. `plain` never uses an exponent, whatever the magnitude. |

These are display rules, not tiles: they carry no title and no
threshold, and they apply to sensors that have no tile at all. A rule
that names only a sensor changes nothing, which is what makes it a
sensible thing to write first and fill in after.

They belong to `[monitor]`, so `labmon query latest` is unaffected — it
promises the reading as recorded, and remains the place to go for every
digit.

## What it shows, and what it shares

The panel shows exactly what `labmon query latest --stats` shows: the
same selection layer, the same renderer, the same notion of staleness.
Two implementations of "the latest value and how old it is" would drift,
and the one that drifted would be the one nobody was watching.

That means everything from
[`docs/export.md`](export.md#what-is-everything-reading-right-now)
applies here — the age colouring, the sensors remembered from the roster
when they have gone quiet, the average rounded against its own deviation.

`n` deserves a second look on a live panel. Sensors here legitimately
run from 1 Hz down to once a minute, so a tile that has not changed in
thirty seconds might be perfectly healthy or might be dead. The reading
count over the window is what separates the two.

## How a refresh works

Each tick re-queries the whole window and replaces the result. It does
not append to what it already has.

That is what Grafana does with a SQL datasource, and the measurements
say why it is right here too. Pulling every reading from every sensor,
against the demo stack:

| window | rows | time |
|---|---|---|
| 5 minutes | 2 298 | 11 ms |
| 15 minutes | 6 918 | 14 ms |
| 1 hour | 27 694 | 19 ms |

Fourteen milliseconds against a two-second cadence is not worth
optimising. An incremental design would have to carry per-sensor
watermarks, handle late-arriving points and reconcile after a dropped
connection — real state, and real bugs, to save single-digit
milliseconds.

Re-querying is also **self-healing**. After a network blip the next tick
is simply correct, with no gap to detect and no backfill to run.

The query runs on a worker thread. It blocks for tens of milliseconds,
which is long enough to make a keypress feel sticky, and there is no
reason for `q` to wait for a database.

## When a refresh fails

The panel does not exit. The last good table stays on screen and the
status line says what happened:

```
cannot reach the database — showing the last good reading · window 15m · refreshing every 2s
```

A panel that tore down the terminal on one unreachable moment would be
worse than useless next to a running experiment. The window is
re-queried every tick anyway, so recovery needs no action.

## Settings

`--since` and `--refresh` override the configuration file for one run.
The file is where a decision worth keeping goes — see
[`docs/configuration.md`](configuration.md#the-user-configuration-file):

```toml
[monitor]
refresh = "2s"
window  = "15m"

[[monitor.sensors]]
sensor_id = "beam-x"
precision = 2

[[monitor.panels]]
sensor_id = "cryo-77k"
title = "Cold finger"
```

Everything is checked when the file is read, not when it is first used —
a mistake that surfaced on the first tick would already have taken over
the terminal — and a panel is named by its position when something is
wrong with it. "A panel is missing sensor_id" is unhelpful in a file
with nine of them, and a layout is exactly the kind of file that grows
to nine.
