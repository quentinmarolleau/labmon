# Watching values in the terminal

`labmon monitor` opens a panel that redraws itself, showing where every
sensor stands and how its window has behaved.

```bash
labmon monitor
labmon monitor --since 1h --refresh 5s
labmon monitor --measurement temperature
```

```
                                               labmon                                               
╭─────────────┬───────────────┬─────────────────┬──────┬────────┬─────────────────┬─────────┬──────╮
│ measurement │ sensor        │           value │ unit │ age    │            mean │      sd │    n │
├─────────────┼───────────────┼─────────────────┼──────┼────────┼─────────────────┼─────────┼──────┤
│ frequency   │ wavemeter-1   │ 2.765612976e+14 │ Hz   │ 2s ago │ 2.765613000e+14 │ 1.9e+06 │  899 │
│ frequency   │ wavemeter-thz │                 │ THz  │ 4h ago │                 │         │      │
│ position    │ beam-x        │               4 │ µm   │ 2s ago │               0 │      19 │ 1797 │
│ position    │ beam-y        │              22 │ µm   │ 2s ago │               0 │      16 │ 1797 │
│ power       │ laser-1       │            90.8 │ mW   │ 2s ago │            95.0 │     8.9 │ 1797 │
│ pressure    │ chamber-1     │        1.40e-07 │ mbar │ 3s ago │        1.34e-07 │ 2.1e-08 │  360 │
│ pressure    │ dual-probe    │                 │ mbar │ 6h ago │                 │         │      │
│ pressure    │ pirani-1      │           9e-09 │ mbar │ 2s ago │         2.9e-08 │ 3.4e-08 │ 1797 │
│ temperature │ cryo-4k       │            4.06 │ K    │ 3s ago │            4.26 │    0.24 │  360 │
│ temperature │ cryo-77k      │            78.6 │ K    │ 3s ago │            76.8 │     1.7 │  360 │
│ temperature │ cryo-diode    │              41 │ K    │ 2s ago │              28 │      11 │ 1797 │
│ temperature │ dual-probe    │                 │ K    │ 6h ago │                 │         │      │
│ temperature │ probe-158     │                 │ K    │ 8h ago │                 │         │      │
│ temperature │ room-1        │           20.69 │ °C   │ 3s ago │           21.18 │    0.60 │  360 │
│ temperature │ room-2        │           22.68 │ °C   │ 3s ago │           22.41 │    0.70 │  360 │
│ voltage     │ bias-monitor  │            -3.1 │ V    │ 2s ago │               0 │     2.2 │ 1797 │
╰─────────────┴───────────────┴─────────────────┴──────┴────────┴─────────────────┴─────────┴──────╯
                       16 sensors, 4 quiet · window 30m · every 2s · 19:17:06
```

`q` quits. `r` redraws immediately, for when waiting out the cadence is
one second too many. `p` opens the command palette, where the colour
theme can be changed — the default is `nord`, chosen for being calm at a
glance and legible on a projector, which is where a panel beside an
experiment tends to end up.

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

## Rows stay where you left them

Ordered by measurement, then by sensor, alphabetically. Nothing about a
reading moves a row.

This matters more here than anywhere else: a panel sorted by age
reorders itself on every tick, so following one number means finding it
again first. Staleness is carried by colour, which does not need a
position to say anything.

## Readings are rounded to their own noise

The panel shows each value at the precision its standard deviation over
the window justifies — `76.9 K` rather than `76.92300000000001`, `-7 µm`
for a beam whose spread is 16 µm.

It is a glance view, read from across a room, and nineteen digits of a
beam position crowd out the rest of the row while two of them carry
information. `labmon query latest` is the view that promises the reading
exactly as stored, and it still does.

A reading with no statistics to round against — one sample in the window
— is shown in full, because nothing says where its digits stop meaning
anything.

## What it shows, and what it shares

The panel shows exactly what `labmon query latest --stats` shows: the
same selection layer, the same renderer, the same notion of staleness.
Two implementations of "the latest value and how old it is" would drift,
and the one that drifted would be the one nobody was watching.

That means everything from
[`docs/export.md`](export.md#what-is-everything-reading-right-now)
applies here — the age colouring, the sensors remembered from the roster
when they have gone quiet, the mean rounded against its own deviation.

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
```

Both are checked when the file is read, not when they are first used. A
mistake that surfaced on the first tick would already have taken over
the terminal.
