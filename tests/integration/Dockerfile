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

# Build image for the integration test. Both app-receiver and app-sender ship in
# a single image; the compose file decides which binary a container runs.
#
# The build stage tracks Rust stable rather than pinning a version, to match
# `dtolnay/rust-toolchain@stable` in the other CI jobs. Cargo.lock is
# .gitignore'd, so dependencies resolve to their latest semver-compatible
# versions on every build and a pinned older toolchain would drift out of date.

FROM rust:slim-bookworm AS builder

WORKDIR /src

# Build dependencies for the crypto/QUIC stack (ring and friends need a C
# toolchain; quinn's TLS pulls in pkg-config).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# target/ and the cargo registry are cache mounts, so the binaries have to be
# copied out to a real layer before the mount goes away.
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/src/target,sharing=locked \
    cargo build --release --bin app-receiver --bin app-sender \
    && mkdir -p /out \
    && cp target/release/app-receiver target/release/app-sender /out/


FROM debian:bookworm-slim AS runtime

# iproute2 provides `ss`, used by the receiver's compose healthcheck to report
# readiness. ca-certificates is not needed for the PSK/self-signed-cert flow but
# costs little and avoids surprises if the apps ever make outbound TLS calls.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /out/app-receiver /usr/local/bin/app-receiver
COPY --from=builder /out/app-sender /usr/local/bin/app-sender

# Certificates are generated at startup into --cert-dir; give the unprivileged
# user somewhere writable to put them.
RUN useradd --create-home --shell /usr/sbin/nologin openscreen
USER openscreen
WORKDIR /home/openscreen

CMD ["app-receiver", "--help"]
