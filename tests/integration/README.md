# Cross-platform integration test

End-to-end test for OpenScreen mDNS discovery: an `app-receiver` advertises
itself, an `app-sender` has to find it and complete the full handshake.

## Running it

```sh
python tests/integration/run.py
```

Requires Docker with Compose v2 (Docker Desktop on macOS/Windows, or
`docker-compose-plugin` on Linux). The runner uses only the Python standard
library, so there is nothing to `pip install`.

| Flag | |
|---|---|
| `--verbose` | Stream container output live instead of capturing it |
| `--keep` | Leave the containers up afterwards for debugging |
| `--self-test` | Check the runner's own logic and exit, without Docker |
| `--mdns-port` | mDNS port (default 5454) |
| `--psk` | Pre-shared key used by both agents |

Compose defaults for every value are baked into `docker-compose.yml`, so
`docker compose up` in this directory also works for poking at the stack by
hand.

## What it checks

The receiver name is generated fresh on every run
(`openscreen-ci-<random>`), and the test asserts that name appears:

1. in the sender's discovery output — proving mDNS discovery actually happened,
   rather than the sender falling back to a direct connection or picking up a
   stale receiver from a concurrent or previous run;
2. on the `Display Name:` line of the agent-info response — which is only
   reached after QUIC, TLS, SPAKE2 authentication and the application-level
   exchange have all succeeded;

plus that the sender exits 0 and the receiver reports authenticating a client.

On failure both container logs are printed before the stack is torn down.

## Design notes

**Why Docker.** mDNS discovery is unreliable on a GitHub Actions runner's host
network. Putting both agents on a user-defined bridge network gives them a
Linux bridge of their own, over which multicast behaves predictably.

**Why a non-standard mDNS port.** Both agents use 5454 rather than 5353 so the
test never contends with a host mDNS responder — macOS always has one
(Bonjour), and many Linux desktops run Avahi.

**Why the healthcheck.** `depends_on` alone only waits for the receiver's
*container* to start, not for it to be advertising. The receiver publishes its
mDNS service before it binds the QUIC socket, so a listening UDP socket on the
app port means both are ready; the sender waits for `service_healthy` and the
startup race disappears.

**Why `--exit-code-from sender`.** It implies `--abort-on-container-exit`, so
the run ends as soon as the sender finishes and the sender's exit status becomes
the test's exit status. The receiver otherwise runs forever.

## Platform support

The runner and the Compose stack behave identically on Linux, macOS and Windows
for local development.

In CI the end-to-end job runs on Linux only, because GitHub's hosted macOS
runners have no Docker daemon and its Windows runners cannot run Linux
containers. To keep the portability claim honest rather than assumed, the
`runner-self-test` job executes `run.py --self-test` on all three platforms.
