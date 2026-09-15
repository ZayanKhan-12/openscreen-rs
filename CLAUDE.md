# CLAUDE.md

Guidance for Claude Code (claude.ai/code) and other agents working in this repository.

## What this is

A Rust implementation of the [Open Screen Protocol](https://www.w3.org/TR/openscreenprotocol/):
discovery, transport, authentication and the application-level messaging that
sits on top. The workspace is split so that transport, crypto and discovery are
each behind a trait with at least one swappable implementation.

## Workspace layout

| Crate | Role |
|---|---|
| `openscreen-common` | Shared types and encoding (varints, type keys) |
| `openscreen-network` | Transport abstraction |
| `openscreen-quinn` | QUIC transport via `quinn` — `QuinnClient`, `QuinnServer` |
| `openscreen-crypto` | Crypto traits (SPAKE2, certificates) |
| `openscreen-crypto-rustcrypto` | RustCrypto-backed implementation of those traits |
| `openscreen-discovery` | Discovery traits — `DiscoveryPublisher`, `DiscoveryBrowser` |
| `openscreen-discovery-mdns` | mDNS discovery via `mdns-sd` |
| `openscreen-discovery-mock` | In-memory discovery, for tests that must not touch the network |
| `openscreen-application` | Application protocol, plus the `app-receiver` / `app-sender` binaries |

The `-mock` and `-rustcrypto` crates exist so tests can avoid real networking
and real key generation. Prefer them over spinning up sockets in a unit test;
reach for the integration test when you genuinely need the whole stack.

## The two binaries

`openscreen-application/src/bin/app-receiver.rs` and `app-sender.rs` are the
end-to-end exercise of the stack and what the integration test drives.

The receiver's startup order is load-bearing: it publishes its mDNS service
**before** binding the QUIC socket. The integration test's healthcheck depends
on that — a listening UDP socket on the app port implies the service is already
advertised. If you reorder those steps, fix
`tests/integration/docker-compose.yml` too.

Both binaries print human-readable progress markers (`OK:`, `WAIT:`, `FAIL:`)
via `println!`, separately from `tracing`. The integration test asserts on some
of those strings; they are listed as constants at the top of
`tests/integration/run.py`, so changing binary output means updating one place.

## Building and testing

```sh
cargo build --workspace --all-features
cargo test --workspace --all-features
cargo clippy --workspace -- -D warnings
```

Clippy runs with `all` and `pedantic` enabled at the workspace level. A number
of pedantic lints are currently blanket-`allow`ed in the root `Cargo.toml` with
a TODO to revisit them individually — when you touch code covered by one of
those allows, prefer fixing it locally over leaving the blanket in place.

`Cargo.lock` is `.gitignore`d, so dependencies resolve fresh. Builds track
Rust **stable**, not a pinned version — the CI jobs use
`dtolnay/rust-toolchain@stable` and the integration Dockerfile uses
`rust:slim-bookworm` for the same reason. Don't pin either to an older release
without also committing a lockfile.

### Integration test

```sh
python tests/integration/run.py            # full end-to-end run (needs Docker)
python tests/integration/run.py --self-test  # runner logic only, no Docker
```

See `tests/integration/README.md` for the design rationale. In short: Docker
Compose puts both agents on a user-defined bridge network because mDNS
multicast is unreliable on a CI runner's host network, and the agents use mDNS
port 5454 so they never contend with the host's own responder (Bonjour on
macOS, Avahi on many Linux desktops).

The end-to-end job runs on Linux only — GitHub's macOS runners have no Docker
daemon and its Windows runners cannot run Linux containers. `--self-test` runs
on all three so the cross-platform claim is tested rather than assumed.

## Conventions

- **Pre-commit is the gate.** `.pre-commit-config.yaml` runs `cargo fmt`,
  `cargo check`, `clippy`, and hygiene hooks (trailing whitespace, LF endings,
  end-of-file newline, YAML/TOML/JSON validity). Run `pre-commit run
  --all-files` before pushing; CI runs the same hooks.
- **Apache 2.0 header** on every new source file, matching the existing ones.
  This includes YAML, Dockerfiles and Python, not just Rust.
- **Standard library only in the test runner.** `tests/integration/run.py` has
  no third-party imports on purpose, so it runs on a fresh Windows or macOS
  checkout without a `pip install` step. Keep it that way.
- **No `shell=True`** in the Python runner — argument lists only, so quoting
  behaves the same on Windows as on POSIX.

## Things worth knowing before you debug

- **mDNS on port 5353 will fight the host.** If discovery behaves strangely
  locally, check whether something is already bound to 5353. Both binaries take
  `--mdns-port`.
- **The sender sleeps, it does not poll.** `--discovery-timeout` is a flat
  sleep before it reads the discovered-services list, not a deadline it polls
  against. Raising it makes discovery more reliable but always costs the full
  duration.
- **The sender picks `services[0]`.** If more than one receiver is visible it
  takes the first, which is why the integration test generates a unique
  receiver name per run and asserts on it rather than merely checking that the
  sender exited 0.
- **`.openscreen/` holds generated certificates** and is gitignored. Deleting it
  forces regeneration, which changes the fingerprint.
