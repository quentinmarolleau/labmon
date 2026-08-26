# Watching values in the terminal

`labmon monitor` opens a panel that redraws itself, showing where every
sensor stands and how its window has behaved.

```bash
labmon monitor
labmon monitor --since 1h --refresh 5s
labmon monitor --measurement temperature
```

```
sensor_id      measurement  value                  unit  age     mean             sd       n
-------------  -----------  ---------------------  ----  ------  ---------------  -------  -----
wavemeter-1    frequency    2.765613014e+14        Hz    0s ago  2.765612999e+14  2.0e+06  11153
cryo-77k       temperature  76.704                 K     2s ago  77.0             1.5      4462
cryo-4k        temperature  4.103                  K     2s ago  4.11             0.25     4462
room-1         temperature  21.125                 °C    2s ago  20.93            0.78     4462
wavemeter-thz  frequency    276.5613007            THz   3h ago  276.56130115     3.1e-07  8
probe-158      temperature  21.0691                K     7h ago  21.069           0.056    3

16 sensors
18:12:09 · window 24h · refreshing every 2s
```

`q` quits. `r` redraws immediately, for when waiting out the cadence is
one second too many.

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
