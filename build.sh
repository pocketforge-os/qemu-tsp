#!/usr/bin/env bash
# qemu-tsp build: clone the pinned upstream qemu, apply the PocketForge patches, and build:
#   - the static aarch64-linux-user target (evdev/uinput ioctl pass-through, patch 0001)
#   - the aarch64-softmmu target carrying the -M pocketforge-a133 machine (patch 0002)
# Both targets are built from the SAME pinned upstream checkout (./UPSTREAM) in separate
# out-of-tree build directories, so neither build's configure options interfere with the
# other's (the linux-user target wants --static --disable-system; the softmmu target is a
# normal dynamically-linked system emulator and cannot be built --static on most hosts).
#   Build deps (Ubuntu 24.04): git meson ninja-build pkg-config python3 gcc \
#                              libglib2.0-dev zlib1g-dev libpixman-1-dev flex bison
# Set QEMU_TSP_SKIP_LINUX_USER=1 to skip the linux-user leg (faster iteration on the
# softmmu machine only); it is NOT skipped by default so a plain ./build.sh still produces
# both targets.
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

echo "== apply PocketForge patch: linux-user evdev/uinput ioctl pass-through =="
git checkout -- linux-user/syscall.c 2>/dev/null || true
if ! grep -q do_ioctl_pf_evdev_uinput linux-user/syscall.c; then
  git apply "$ROOT/pocketforge/0001-linux-user-evdev-uinput-ioctl-passthrough.patch"
fi

echo "== apply PocketForge patch: -M pocketforge-a133 softmmu machine =="
if [ ! -f hw/arm/pocketforge_a133.c ]; then
  git apply "$ROOT/pocketforge/0002-hw-arm-pocketforge_a133-softmmu-machine.patch"
fi

echo "== apply PocketForge patch: -M pocketforge-a133 peripheral stubs =="
if [ ! -f hw/misc/pocketforge_a133_mmio_stub.c ]; then
  git apply "$ROOT/pocketforge/0003-hw-arm-pocketforge_a133-peripheral-stubs.patch"
fi

mkdir -p "$OUT"

if [ "${QEMU_TSP_SKIP_LINUX_USER:-0}" != "1" ]; then
  echo "== configure + build: aarch64-linux-user (static) =="
  rm -rf build
  ./configure --target-list=aarch64-linux-user --static --disable-system --without-default-features
  ninja -C build qemu-aarch64
  cp build/qemu-aarch64 "$OUT/qemu-aarch64"
  echo "== done: $OUT/qemu-aarch64 =="
  "$OUT/qemu-aarch64" --version | head -1
else
  echo "== QEMU_TSP_SKIP_LINUX_USER=1: skipping the linux-user leg =="
fi

echo "== configure + build: aarch64-softmmu (-M pocketforge-a133) =="
rm -rf build-softmmu
mkdir -p build-softmmu
(cd build-softmmu && ../configure --target-list=aarch64-softmmu --without-default-features)
ninja -C build-softmmu qemu-system-aarch64
cp build-softmmu/qemu-system-aarch64 "$OUT/qemu-system-aarch64"
echo "== done: $OUT/qemu-system-aarch64 =="
"$OUT/qemu-system-aarch64" --version | head -1
"$OUT/qemu-system-aarch64" -M help | grep pocketforge-a133
