#!/bin/bash
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
VERSION=v0.10.5
SRC=/opt/src/go-librespot

rm -rf "$SRC"
git clone -q --depth 1 --branch "$VERSION" https://github.com/devgianlu/go-librespot.git "$SRC"
git -C "$SRC" apply "$REPO/librespot/silent-playback.patch"
mkdir -p /opt/go-librespot
(cd "$SRC" && PATH=/opt/go/bin:$PATH go build -o /opt/go-librespot/go-librespot-silent.new ./cmd/daemon)
mv /opt/go-librespot/go-librespot-silent.new /opt/go-librespot/go-librespot-silent
