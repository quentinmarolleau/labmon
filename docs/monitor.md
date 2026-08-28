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
                           labmon · lab @ http://localhost:8181
╭─────────────┬──────────────┬─────────────────┬──────┬────────┬──────────┬─────────┬─────╮
│ measurement │ sensor       │           value │ unit │ age    │  average │       σ │   N │
├─────────────┼──────────────┼─────────────────┼──────┼────────┼──────────┼─────────┼─────┤
│ frequency   │ wavemeter-1  │ 2.765612936e+14 │ Hz   │ 5m ago │          │         │     │
├─────────────┼──────────────┼─────────────────┼──────┼────────┼──────────┼─────────┼─────┤
│ position    │ beam-x       │            10.7 │ µm   │ 1s ago │        6 │      10 │ 298 │
│             │ beam-y       │           12.94 │ µm   │ 1s ago │       -6 │      11 │ 298 │
├─────────────┼──────────────┼─────────────────┼──────┼────────┼──────────┼─────────┼─────┤
│ power       │ laser-1      │           85.63 │ mW   │ 1s ago │     95.1 │     8.8 │ 298 │
├─────────────┼──────────────┼─────────────────┼──────┼────────┼──────────┼─────────┼─────┤
│ pressure    │ chamber-1    │     1.35284e-07 │ mbar │ 4s ago │ 1.50e-07 │ 1.0e-08 │  60 │
│             │ pirani-1     │        1.59e-09 │ mbar │ 1s ago │  4.6e-08 │ 3.8e-08 │ 298 │
├─────────────┼──────────────┼─────────────────┼──────┼────────┼──────────┼─────────┼─────┤
│ temperature │ cryo-4k      │           3.865 │ K    │ 4s ago │    3.793 │   0.093 │  60 │
│             │ cryo-77k     │          77.533 │ K    │ 4s ago │    77.23 │    0.44 │  60 │
│             │ cryo-diode   │           34.48 │ K    │ 1s ago │       30 │      11 │ 298 │
│             │ room-1       │          21.075 │ °C   │ 4s ago │    20.70 │    0.24 │  60 │
│             │ room-2       │           22.07 │ °C   │ 5m ago │          │         │     │
├─────────────┼──────────────┼─────────────────┼──────┼────────┼──────────┼─────────┼─────┤
│ voltage     │ bias-monitor │           -2.79 │ V    │ 1s ago │      0.4 │     2.2 │ 298 │
╰─────────────┴──────────────┴─────────────────┴──────┴────────┴──────────┴─────────┴─────╯
                   12 sensors, 2 quiet · window 5m · every 2s · 08:29:01
```

The measurement is written once for the group of rows it covers, with a
rule between one group and the next. The rows are sorted by measurement,
so repeating it on every line spends the width of a column saying what
the line above has already said.

Every column is held at the widest it has had to be. Sized to whatever
one tick happens to hold, a column changes width as soon as a reading
gains or loses a digit — and every column right of it, and the centred
table itself, moves with it. Twice a second that is a table which
twitches, and a number never quite where it was last read. The widths
settle within a few ticks and never narrow again.

The title names the database being read and the host it lives on.
`labmon` alone cannot say which of two stacks on the same machine a panel
is watching, and a demo compose file running beside the real one shows
the same sensors either way.

| Key | Does |
|---|---|
| `q` | Quit |
| `r` | Change refresh rate |
| `s` | Take a screenshot |
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
changed. Each theme is worn as the selector passes over it, so the menu
is its own preview: the panel behind it is redrawn in the theme under the
cursor, in the terminal it will be read in and against the readings
actually on screen. `escape` puts back the theme that was in force.

The default is `nord`, chosen for being calm at a glance and legible on a
projector, which is where a panel beside an experiment tends to end up.
A different one every time is `theme` in the configuration file, or in a
layout passed with `--config`:

```toml
[monitor]
theme = "solarized-light"
```

An unknown name is refused before the panel starts, with the twenty-one
it does have in the message.

`s` writes a screenshot, as an SVG in the Downloads directory, and says
where it went. The command palette carries the same entry, a search box
and a selection away from the moment worth capturing.

Each character in it is placed on a coordinate of its own rather than a
run of text at a time. Rich holds a run to its width with `textLength`,
which browsers honour and librsvg — the renderer behind most desktop
image viewers, and behind ImageMagick — ignores, so a screenshot opened
outside a browser had its columns drifting off the borders they belong
to. The file is about three times the size and renders the same
everywhere.

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
in a file of its own passed with `--config`, or `-c`. The two have
different lifetimes: a machine-wide window and cadence are settings,
while "the five things worth watching during a bakeout" is a document
that belongs beside the procedure. `--config` **replaces** the `[monitor]` section
rather than merging with it, so a layout can say "just these tiles"
without first editing the file it was avoiding.

```toml
refresh = "2s"
window  = "15m"
theme   = "nord"

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
theme   = "nord"

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
