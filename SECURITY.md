# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for a suspected vulnerability.

Report it privately by email to **q.marolleau-dev@pm.me** — the address
`CONTRIBUTING.md` already uses for Code of Conduct reports. Include what you
found, how to reproduce it, and what you think an attacker gains; a proof of
concept helps but is not required.

Expect an acknowledgement within a week. You will be told whether the report is
accepted, and kept informed while a fix is prepared. If you would like credit in
the release notes, say so — and say how you want to be named.

## Supported versions

labmon is pre-1.0 and there are no released tags yet, so there is only one
supported version: **the latest commit on `main`**. Fixes land there. There are
no backports, and no security support for a checkout you are holding at an older
commit.

That changes when the [v0.2.0-beta milestone][milestone] ships; this section
will be updated to name the supported tags then.

[milestone]: https://github.com/quentinmarolleau/labmon/milestone/1

## Threat model

Three assumptions are built into the design. A report that a documented
assumption holds is not a vulnerability; a report that one can be *broken* very
much is.

**The lab LAN is trusted.** The stack speaks plain HTTP and gRPC with no TLS, so
`INFLUXDB3_AUTH_TOKEN` travels unencrypted between clients and the server. That
is a deliberate tradeoff for a small trusted network rather than an oversight —
see [Security: plain HTTP, by design][http] in the deployment guide. TLS through
a reverse proxy is a planned upgrade, not a shipped feature.

**Every client holds an admin token.** All clients share the same
`INFLUXDB3_AUTH_TOKEN`, and it grants full control of the database: write,
delete, reconfigure, mint further tokens. This is a platform constraint —
InfluxDB 3 Core issues admin tokens only, and the per-client write-only tokens a
deployment like this would otherwise use are an Enterprise feature. So the blast
radius of one mislaid client is the whole historical record: treat any machine
holding the token as trusted infrastructure, and keep port 8181 off network
segments that do not need it. See [One token, and it is an admin token][token],
and rotate using the drill there on any suspicion.

**A calibration file is code, not configuration.** Expressions in
`calibration.toml` are evaluated by [asteval][asteval], which does not import
modules or reach the filesystem. That restriction makes typos safe; it is not a
security boundary, and asteval's own documentation says so — a crafted
expression can still exhaust memory or crash the interpreter. Whoever writes a
calibration file already controls the process that reads it, so for the intended
use there is no boundary to cross. Do not accept one from someone you would not
give a shell to. See [A calibration file is code, not configuration][calib].

[http]: docs/deployment.md#security-plain-http-by-design-for-now
[token]: docs/deployment.md#one-token-and-it-is-an-admin-token
[calib]: docs/serial-sensor.md#a-calibration-file-is-code-not-configuration
[asteval]: https://github.com/lmfit/asteval

### What is in scope

A way to break one of those assumptions. For example: reaching data or the
database without the token from a client that never held it; escaping the
asteval sandbox from an expression a *user* did not write; anything that lets a
sensor's serial output — which is untrusted input, and the one place bytes
arrive from outside — affect the host beyond the reading it encodes.

### What is out of scope

The assumptions themselves, reported as findings: that the LAN is unencrypted,
that clients share an admin token, that a calibration file can run code. Those
are documented above and in the deployment guide. If you think one of them is
the wrong tradeoff, that is a design discussion — open an issue.
