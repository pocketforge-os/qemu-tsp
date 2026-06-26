#!/usr/bin/env bash
# qemu-tsp build: clone the pinned upstream qemu, apply the PocketForge patch, build the
# static aarch64-linux-user target. Reproducible from the pin in ./UPSTREAM.
#   Build deps (Ubuntu 24.04): git meson ninja-build pkg-config python3 gcc \
#                              libglib2.0-dev zlib1g-dev
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
REPO=$(sed -n 's/^repo *= *//p'   UPSTREAM)
TAG=$(sed -n  's/^tag *= *//p'    UPSTREAM)
COMMIT=$(sed -n 's/^commit *= *//p' UPSTREAM)
SRC="${QEMU_TSP_SRC:-$ROOT/build/qemu-src}"
OUT="$ROOT/build/qemu-tsp"

mkdir -p "$ROOT/build"
if [ ! -d "$SRC/.git" ]; then
  echo "== clone $REPO @ $TAG =="
  git clone --depth 1 --branch "$TAG" "$REPO" "$SRC"
fi
cd "$SRC"
got=$(git rev-parse HEAD)
[ "$got" = "$COMMIT" ] || { echo "FATAL: upstream HEAD $got != pinned $COMMIT"; exit 1; }
echo "== apply PocketForge patch =="
git checkout -- linux-user/syscall.c 2>/dev/null || true
if ! grep -q do_ioctl_pf_evdev_uinput linux-user/syscall.c; then
  git apply "$ROOT/pocketforge/0001-linux-user-evdev-uinput-ioctl-passthrough.patch"
fi
echo "== configure (aarch64-linux-user, static) =="
rm -rf build
./configure --target-list=aarch64-linux-user --static --disable-system --without-default-features
echo "== build =="
ninja -C build qemu-aarch64
mkdir -p "$OUT"
cp build/qemu-aarch64 "$OUT/qemu-aarch64"
echo "== done: $OUT/qemu-aarch64 =="
"$OUT/qemu-aarch64" --version | head -1
