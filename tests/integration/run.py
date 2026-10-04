#!/usr/bin/env python3
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cross-platform integration test for OpenScreen mDNS discovery.

Brings up an ``app-receiver`` and an ``app-sender`` with Docker Compose and
checks that the sender discovers the receiver over mDNS and completes the
OpenScreen handshake.

Docker Compose does the orchestration, so the behaviour is identical on Linux,
macOS and Windows; this script only generates per-run configuration, asserts on
the result and cleans up. It uses the standard library only -- no ``pip
install`` step on any platform.

Usage::

    python tests/integration/run.py            # run the test
    python tests/integration/run.py --verbose  # stream container logs
    python tests/integration/run.py --keep     # leave containers up to debug
    python tests/integration/run.py --self-test  # check the runner, no Docker

Exit status is 0 if the test passed and 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yml"

# Marker strings printed by the two binaries. Kept together so that a change to
# either binary's output has one obvious place to be reflected.
SENDER_SUCCESS = "Test completed successfully"
SENDER_DISPLAY_NAME_FIELD = "Display Name:"
RECEIVER_AUTHENTICATED = "Client authenticated"

DEFAULT_TIMEOUT = 600
DEFAULT_BUILD_TIMEOUT = 1800


class TestFailure(Exception):
    """Raised when the integration test fails for an expected, reportable reason."""


def log(message: str) -> None:
    """Print progress, flushing so output interleaves correctly under CI."""
    print(message, flush=True)


def compose_base_command() -> list[str]:
    """Return the Compose CLI to use, preferring the v2 ``docker compose`` plugin.

    Raises:
        TestFailure: if neither Compose v2 nor the legacy v1 binary is available.
    """
    if shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    raise TestFailure(
        "Docker Compose not found. Install Docker Desktop (macOS/Windows) or "
        "the docker-compose-plugin package (Linux)."
    )


def build_environment(receiver_name: str, args: argparse.Namespace) -> dict[str, str]:
    """Build the environment Compose interpolates into the compose file.

    Starts from ``os.environ`` so Docker's own configuration (``DOCKER_HOST``,
    credential helpers, proxy settings) is preserved on every platform.
    """
    env = dict(os.environ)
    env.update(
        {
            "OPENSCREEN_RECEIVER_NAME": receiver_name,
            "OPENSCREEN_PSK": args.psk,
            "OPENSCREEN_APP_PORT": str(args.app_port),
            "OPENSCREEN_MDNS_PORT": str(args.mdns_port),
            "OPENSCREEN_DISCOVERY_TIMEOUT": str(args.discovery_timeout),
            "OPENSCREEN_RUST_LOG": args.rust_log,
        }
    )
    return env


def evaluate(sender_log: str, receiver_log: str, receiver_name: str) -> list[str]:
    """Check the container logs against the expected handshake.

    Pure, so it can be exercised by ``--self-test`` on a machine without Docker.

    Returns:
        A list of human-readable failure descriptions; empty means the test passed.
    """
    failures: list[str] = []

    # The receiver name is generated fresh per run, so finding it in the
    # sender's output is what actually proves mDNS discovery happened -- as
    # opposed to the sender having fallen back to a hard-coded direct
    # connection, or having discovered a stale receiver from an earlier run.
    if receiver_name not in sender_log:
        failures.append(
            f"sender never discovered this run's receiver ({receiver_name!r} "
            "absent from sender output)"
        )

    # The name also has to come back inside the agent-info response, which only
    # happens after QUIC, TLS, SPAKE2 and the application exchange all succeed.
    if not any(
        SENDER_DISPLAY_NAME_FIELD in line and receiver_name in line
        for line in sender_log.splitlines()
    ):
        failures.append(
            f"agent-info response did not report display name {receiver_name!r}"
        )

    if SENDER_SUCCESS not in sender_log:
        failures.append(f"sender did not report success ({SENDER_SUCCESS!r} absent)")

    if RECEIVER_AUTHENTICATED not in receiver_log:
        failures.append(
            f"receiver did not authenticate the sender ({RECEIVER_AUTHENTICATED!r} absent)"
        )

    return failures


def run_step(
    command: list[str],
    env: dict[str, str],
    timeout: int,
    stream: bool,
) -> subprocess.CompletedProcess:
    """Run one Compose command, returning the completed process.

    Raises:
        TestFailure: if the command exceeds ``timeout``.
    """
    log(f"$ {' '.join(command)}")
    try:
        return subprocess.run(
            command,
            env=env,
            cwd=HERE,
            capture_output=not stream,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TestFailure(
            f"timed out after {timeout}s: {' '.join(command)}"
        ) from exc


def service_logs(compose: list[str], env: dict[str, str], service: str) -> str:
    """Return one service's logs with Compose's ``service |`` prefix stripped."""
    result = subprocess.run(
        [*compose, "logs", service],
        env=env,
        cwd=HERE,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    raw_logs = result.stdout + result.stderr
    stripped_lines = []
    for line in raw_logs.splitlines():
        if " | " in line:
            stripped_lines.append(line.split(" | ", 1)[1])
        else:
            stripped_lines.append(line)
    return "\n".join(stripped_lines)


def run_integration_test(args: argparse.Namespace) -> int:
    """Run the full test. Returns a process exit status."""
    if not COMPOSE_FILE.is_file():
        raise TestFailure(f"compose file not found: {COMPOSE_FILE}")

    compose = compose_base_command()

    # A fresh name per run keeps concurrent runs (and leftover state from a
    # previous one) from being mistaken for this run's receiver.
    receiver_name = f"openscreen-ci-{uuid.uuid4().hex[:12]}"
    env = build_environment(receiver_name, args)

    log("")
    log("=== OpenScreen cross-platform integration test ===")
    log(f"  receiver name : {receiver_name}")
    log(f"  mDNS port     : {args.mdns_port}")
    log(f"  app port      : {args.app_port}")
    log(f"  compose file  : {COMPOSE_FILE}")
    log("")

    try:
        log("--- Building images ---")
        build = run_step(
            [*compose, "build"], env, args.build_timeout, stream=True
        )
        if build.returncode != 0:
            raise TestFailure(f"image build failed (exit {build.returncode})")

        log("")
        log("--- Running receiver + sender ---")
        # --exit-code-from implies --abort-on-container-exit: the run ends as
        # soon as the sender finishes, and the sender's status becomes ours.
        up = run_step(
            [
                *compose,
                "up",
                "--exit-code-from",
                "sender",
                "--no-color",
            ],
            env,
            args.timeout,
            stream=args.verbose,
        )

        sender_log = service_logs(compose, env, "sender")
        receiver_log = service_logs(compose, env, "receiver")

        failures = evaluate(sender_log, receiver_log, receiver_name)
        if up.returncode != 0:
            failures.insert(0, f"sender exited with status {up.returncode}")

        if failures:
            log("")
            log("--- receiver log ---")
            log(receiver_log.rstrip() or "(empty)")
            log("")
            log("--- sender log ---")
            log(sender_log.rstrip() or "(empty)")
            log("")
            log("FAILED:")
            for failure in failures:
                log(f"  - {failure}")
            return 1

        log("")
        log("  ok  sender discovered the receiver over mDNS")
        log("  ok  QUIC + TLS + SPAKE2 handshake completed")
        log("  ok  agent-info exchange returned the expected display name")
        log("")
        log("INTEGRATION TEST PASSED")
        return 0

    finally:
        if args.keep:
            log("")
            log("--keep: leaving containers up. Tear down with:")
            log(f"  cd {HERE} && {' '.join(compose)} down -v")
        else:
            log("")
            log("--- Cleaning up ---")
            subprocess.run(
                [*compose, "down", "-v", "--remove-orphans"],
                env=env,
                cwd=HERE,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )


def self_test() -> int:
    """Exercise the runner's own logic without needing Docker.

    This is what makes the cross-platform claim testable: CI runs it on Linux,
    macOS and Windows, where a Linux-container Compose stack cannot run.
    """
    name = "openscreen-ci-deadbeef1234"

    good_sender = "\n".join(
        [
            "WAIT: Discovering OpenScreen receivers...",
            "OK: Found 1 receiver(s)",
            f"-> Selected: {name}",
            "WAIT: Connecting to receiver (QUIC + TLS + SPAKE2)... OK:",
            f"Display Name:        {name}",
            "OK: Test completed successfully!",
        ]
    )
    # Mirrors app-receiver's real output, including the two-space indent on the
    # authentication line (app-receiver.rs:186).
    good_receiver = "\n".join(
        [
            f"OK: mDNS service published: {name}._openscreen._udp.local.",
            "New connection from 172.20.0.3:54321",
            "  OK: Client authenticated!",
        ]
    )

    checks: list[tuple[str, bool]] = []

    checks.append(("clean run passes", evaluate(good_sender, good_receiver, name) == []))

    # A sender that found *a* receiver but not this run's one must fail: this is
    # the stale/concurrent-run case the unique name exists to catch.
    stale = good_sender.replace(name, "openscreen-ci-000000000000")
    checks.append(("stale receiver name fails", len(evaluate(stale, good_receiver, name)) > 0))

    # Discovery succeeded but the handshake did not.
    truncated = good_sender.replace("OK: Test completed successfully!", "")
    checks.append(("missing success marker fails", len(evaluate(truncated, good_receiver, name)) > 0))

    # Sender claims success but the receiver never authenticated anyone.
    checks.append(
        ("unauthenticated receiver fails", len(evaluate(good_sender, "", name)) > 0)
    )

    # The display name must appear on the agent-info line specifically, not just
    # anywhere in the log.
    no_agent_info = good_sender.replace(f"Display Name:        {name}", "Display Name:        other")
    checks.append(
        ("wrong agent-info display name fails", len(evaluate(no_agent_info, good_receiver, name)) > 0)
    )

    checks.append(("compose file present", COMPOSE_FILE.is_file()))
    checks.append(("dockerfile present", (HERE / "Dockerfile").is_file()))

    failed = 0
    for description, passed in checks:
        log(f"  {'ok  ' if passed else 'FAIL'} {description}")
        if not passed:
            failed += 1

    log("")
    if failed:
        log(f"SELF-TEST FAILED ({failed} of {len(checks)})")
        return 1
    log(f"SELF-TEST PASSED ({len(checks)} checks) on {sys.platform}, Python {sys.version.split()[0]}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-platform OpenScreen mDNS integration test.",
    )
    parser.add_argument(
        "--psk",
        default="integration-test-psk",
        help="Pre-shared key used by both agents (default: %(default)s)",
    )
    parser.add_argument(
        "--mdns-port",
        type=int,
        default=5454,
        help="mDNS port, kept off 5353 to avoid the host resolver (default: %(default)s)",
    )
    parser.add_argument(
        "--app-port",
        type=int,
        default=4433,
        help="QUIC port the receiver listens on (default: %(default)s)",
    )
    parser.add_argument(
        "--discovery-timeout",
        type=int,
        default=10,
        help="Seconds the sender browses for receivers (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Seconds to allow for the test run itself (default: %(default)s)",
    )
    parser.add_argument(
        "--build-timeout",
        type=int,
        default=DEFAULT_BUILD_TIMEOUT,
        help="Seconds to allow for the image build (default: %(default)s)",
    )
    parser.add_argument(
        "--rust-log",
        default="info",
        help="RUST_LOG for both containers (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Stream container output live instead of capturing it",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave containers running afterwards for debugging",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Check the runner's own logic and exit, without using Docker",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    try:
        return run_integration_test(args)
    except TestFailure as exc:
        log("")
        log(f"FAILED: {exc}")
        return 1
    except KeyboardInterrupt:
        log("")
        log("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
