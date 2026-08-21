# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for a suspected vulnerability.

Use GitHub's private reporting instead: the **Report a vulnerability**
button on this repository's [Security tab][advisories]. It opens a
private thread visible only to the maintainer, and it is the preferred
route because the report, the discussion and the eventual advisory stay
in one place.

If that is inconvenient, email **q.marolleau-dev@pm.me** — the address
`CONTRIBUTING.md` already uses for Code of Conduct reports.

Either way, say what you found, how to reproduce it, and what an attacker
gains. A proof of concept helps and is not required.

Expect an acknowledgement within a week, a decision on whether the report
is accepted, and news while a fix is prepared. Say so if you would like
credit in the release notes, and say how you want to be named.

[advisories]: https://github.com/quentinmarolleau/labmon/security/advisories

## Supported versions

labmon is pre-1.0 and there are no released tags yet, so there is exactly
one supported version: **the latest commit on `main`**. Fixes land there.
There are no backports and no security support for a checkout held at an
older commit.

That changes when the [v0.2.0-beta milestone][milestone] ships, and this
section will name the supported tags then.

[milestone]: https://github.com/quentinmarolleau/labmon/milestone/1

## Threat model

Three assumptions are built into the design. Reporting that a documented
assumption holds is not a vulnerability; reporting that one can be
*broken* very much is.

**Encryption is available, not enforced.** By default the stack speaks
plain HTTP and gRPC on 8181 and 3000, so `INFLUXDB3_AUTH_TOKEN` crosses
the LAN in clear. The `tls` profile puts a reverse proxy with its own CA
in front of both services, and a deployment that turns it on and closes
the plain ports at the firewall is encrypted end to end — but nothing
forces that, and the plain ports stay open until an operator shuts them.
See [Encrypting client and viewer traffic][tls].

**Every client holds an admin token.** All clients share the same
`INFLUXDB3_AUTH_TOKEN`, and it grants full control of the database:
write, delete, reconfigure, mint further tokens. This is a platform
constraint rather than a choice — InfluxDB 3 Core issues admin tokens
only, and the per-client write-only tokens a deployment like this would
otherwise use are an Enterprise feature. The blast radius of one mislaid
client is therefore the whole historical record. Treat any machine
holding the token as trusted infrastructure, and keep 8181 off network
segments that do not need it. TLS does not change this: it protects the
token in transit, while every client still holds an admin credential at
rest. See [One token, and it is an admin token][token].

**A calibration file is code, not configuration.** Expressions in
`calibration.toml` are evaluated by [asteval][asteval], which imports no
modules and reaches no filesystem. That restriction makes typos safe; it
is not a security boundary, and asteval's own documentation says as much
— a crafted expression can still exhaust memory or crash the interpreter.
Whoever writes a calibration file already controls the process that reads
it, so for the intended use there is no boundary to cross. Do not accept
one from someone you would not give a shell to. See [A calibration file
is code, not configuration][calibration].

[tls]: docs/deployment.md#encrypting-client-and-viewer-traffic
[token]: docs/deployment.md#one-token-and-it-is-an-admin-token
[calibration]: docs/serial-sensor.md#a-calibration-file-is-code-not-configuration
[asteval]: https://github.com/lmfit/asteval

### In scope

A way to break one of those assumptions. For example: reading data, or
reaching the database, from a client that never held the token; defeating
certificate verification against a stack running the `tls` profile;
escaping asteval from an expression a *user* did not write; or anything
that lets a sensor's serial output — untrusted input, and the one place
bytes arrive from outside — affect the host beyond the reading it
encodes.

### Out of scope

The assumptions themselves, reported as findings: that the default is
unencrypted, that clients share an admin token, that a calibration file
can run code. Those are documented here and in the deployment guide. If
you think one of them is the wrong tradeoff, that is a design discussion
— open an issue.
