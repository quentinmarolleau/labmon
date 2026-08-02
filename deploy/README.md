# Running a sensor without Docker

Example systemd units for machines where the containers are not an option.
Nothing here is needed for the normal path — a sensor in a container is
supervised by Compose, and these files have no part in it.

## Prefer the container

A container pins the sensor's dependencies next to the code that uses
them, needs no Python or virtualenv on the host, takes the same commands
on every machine, and has its output collected without further setup. Use
it unless something stops you.

What actually stops people, in practice:

- **Policy.** A managed or locked-down control PC where Docker will not be
  installed. This is the common one, and no technical argument moves it.
- **A vendor SDK that will not containerise** — a licence dongle, a kernel
  module, a hard requirement on a particular interpreter or distribution.
- **A machine too small to want the daemon**, where installing Docker to
  run one Python script is more setup than the script.

Worth knowing: choosing systemd is not escaping it. A container's
`restart: unless-stopped` is enforced by the Docker daemon, which is
itself a systemd unit. The choice is how many supervisors you deal with,
not whether systemd is involved.

## The units

| Unit | Runs | Guide |
| --- | --- | --- |
| `labmon-sensor.service` | `mock-sensor`, for proving a client can reach the server | [client-setup](../docs/client-setup.md) |
| `labmon-serial-sensor.service` | `serial-sensor`, reading a board over serial | [serial-sensor](../docs/serial-sensor.md) |
| `labmon-custom-sensor.service` | A continuous script built from [`templates/custom-sensor/`](../templates/custom-sensor/) | [custom-sensor](../docs/custom-sensor.md) |
| `labmon-custom-sensor-triggered.service` | The same, reading once per run | [custom-sensor](../docs/custom-sensor.md) |
| `labmon-custom-sensor-triggered.timer` | Schedules the unit above | [custom-sensor](../docs/custom-sensor.md) |

Each file carries its own install instructions in a comment at the top.
All of them use placeholder paths and a placeholder user, so they need
editing before they will start.

## Two things that catch people out

**Enable the timer, not the service it starts.** A `Type=oneshot` service
enabled on its own runs once at boot and never again:

```bash
sudo systemctl enable --now labmon-custom-sensor-triggered.timer   # correct
sudo systemctl enable --now labmon-custom-sensor-triggered.service # runs once, ever
```

**A triggered unit has no `Restart=` on purpose.** A failed reading waits
for the next scheduled run rather than retrying immediately — retrying a
rate-limited API straight away is how a temporary refusal becomes a
lasting one. The continuous units do the opposite, with
`Restart=on-failure`, because there is nothing scheduling a second attempt.

## Logs

A unit's output goes to the journal:

```bash
journalctl -u labmon-custom-sensor.service -f
```

That matters beyond reading it by hand: the journal is where logs are
collected from on a machine with no Docker, so a sensor run this way ends
up in the same place as one run in a container.
