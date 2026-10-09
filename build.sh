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
LINUX_USER_BUILD_DIR="${QEMU_TSP_LINUX_USER_BUILD_DIR:-build}"
SYSTEM_BUILD_DIR="${QEMU_TSP_SYSTEM_BUILD_DIR:-build-softmmu}"

prepare_owned_build_dir() {
  local dir=$1

  case "$dir" in
    build|build-softmmu)
      [ ! -L "$dir" ] || { echo "FATAL: refusing to clean symlink: $dir"; exit 1; }
      if [ -d "$dir" ]; then
        find "$dir" -mindepth 1 -delete
        rmdir "$dir"
      elif [ -e "$dir" ]; then
        echo "FATAL: build path exists and is not a directory: $dir"
        exit 1
      fi
      ;;
    build-linux-user-*|build-softmmu-rtc-*|build-softmmu-mmio-*)
      [ ! -e "$dir" ] || {
        echo "FATAL: refusing collision with unique build path: $dir"
        exit 1
      }
      ;;
    *) echo "FATAL: refusing to clean unexpected build directory: $dir"; exit 1 ;;
  esac
  mkdir "$dir"
}

mkdir -p "$ROOT/build"
if [ ! -d "$SRC/.git" ]; then
  echo "== clone $REPO @ $TAG =="
  git clone --depth 1 --branch "$TAG" "$REPO" "$SRC"
fi
cd "$SRC"
got=$(git rev-parse HEAD)
[ "$got" = "$COMMIT" ] || { echo "FATAL: upstream HEAD $got != pinned $COMMIT"; exit 1; }

echo "== apply PocketForge patch: linux-user evdev/uinput ioctl pass-through =="
git checkout -- linux-user/syscall.c
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

echo "== apply PocketForge patch: -M pocketforge-a133 Phase C stubs + virtio-gpu graft =="
if ! grep -q pocketforge_a133_create_virtio_mmio hw/arm/pocketforge_a133.c; then
  git apply "$ROOT/pocketforge/0004-hw-arm-pocketforge_a133-phase-c-stubs-and-virtio-gpu.patch"
fi

echo "== apply PocketForge patch: exact A100 RTC clock-control model =="
if [ ! -f hw/rtc/pocketforge_a100_rtc.c ]; then
  git apply "$ROOT/pocketforge/0005-hw-rtc-add-PocketForge-A100-RTC-model.patch"
fi

echo "== apply PocketForge patch: pinned-DTB MMIO coverage =="
if [ ! -f tests/qtest/pocketforge-a133-mmio-map-test.c ]; then
  git apply "$ROOT/pocketforge/0006-hw-arm-pocketforge-a133-complete-mmio-coverage.patch"
fi

echo "== apply PocketForge patch: deterministic A133 input controls =="
if [ ! -f hw/input/pocketforge_a133_input.c ]; then
  git apply "$ROOT/pocketforge/0007-hw-input-add-PocketForge-A133-controls.patch"
fi

echo "== apply PocketForge patch: atomic virtio-input reports =="
if ! grep -q 'without consuming guest buffers' hw/input/virtio-input.c; then
  git apply "$ROOT/pocketforge/0008-hw-input-make-virtio-input-reports-queue-atomic.patch"
fi

echo "== apply PocketForge patch: A133 R-I2C0 controller =="
if [ ! -f tests/qtest/pocketforge-a133-r-i2c-test.c ]; then
  git apply "$ROOT/pocketforge/0009-hw-arm-model-A133-R-I2C0-controller.patch"
fi

echo "== apply PocketForge patch: bounded AXP717 PMIC register bank =="
if [ ! -f tests/qtest/pocketforge-a133-axp717-test.c ]; then
  git apply "$ROOT/pocketforge/0010-hw-misc-add-bounded-AXP717-register-bank.patch"
fi

echo "== apply PocketForge patch: precise R-I2C0 coverage classification =="
if ! grep -q 'r_i2c0.reserved' hw/arm/pocketforge_a133.c; then
  git apply "$ROOT/pocketforge/0011-hw-arm-classify-R-I2C0-aperture-precisely.patch"
fi

echo "== apply PocketForge patch: build-6 A133 probe apertures =="
if ! grep -q 'pocketforge-a133.g2d_clk' hw/arm/pocketforge_a133.c; then
  git apply "$ROOT/pocketforge/0012-hw-arm-cover-build-6-A133-probe-apertures.patch"
fi

echo "== apply PocketForge patch: A100 SD host variant =="
if ! grep -q 'TYPE_AW_SDHOST_SUN50I_A100' include/hw/sd/allwinner-sdhost.h; then
  git apply "$ROOT/pocketforge/0013-hw-sd-add-A100-SD-host-variant.patch"
fi

echo "== check applied PocketForge source whitespace =="
git diff --check "$COMMIT" --

mkdir -p "$OUT"

if [ "${QEMU_TSP_SKIP_LINUX_USER:-0}" != "1" ]; then
  echo "== configure + build: aarch64-linux-user (static) =="
  prepare_owned_build_dir "$LINUX_USER_BUILD_DIR"
  (cd "$LINUX_USER_BUILD_DIR" && ../configure --target-list=aarch64-linux-user --static --disable-system --without-default-features)
  ninja -C "$LINUX_USER_BUILD_DIR" qemu-aarch64
  cp "$LINUX_USER_BUILD_DIR/qemu-aarch64" "$OUT/qemu-aarch64"
  echo "== done: $OUT/qemu-aarch64 =="
  "$OUT/qemu-aarch64" --version | head -1
else
  echo "== QEMU_TSP_SKIP_LINUX_USER=1: skipping the linux-user leg =="
fi

echo "== configure + build: aarch64-softmmu (-M pocketforge-a133) =="
prepare_owned_build_dir "$SYSTEM_BUILD_DIR"
# -Dpixman=enabled: needed for the QMP `screendump` command (used by the Phase C UI
# harness, scripts/qemu-pocketforge-a133-ui.sh, to capture render evidence) -- without it
# --without-default-features strips pixman along with every other UI backend and
# `screendump` fails at runtime with QMP error CommandNotFound (no build-time signal).
(cd "$SYSTEM_BUILD_DIR" && ../configure --target-list=aarch64-softmmu --without-default-features -Dpixman=enabled)
ninja -C "$SYSTEM_BUILD_DIR" qemu-system-aarch64 \
  tests/qtest/pocketforge-a100-rtc-test \
  tests/qtest/pocketforge-a133-input-test \
  tests/qtest/pocketforge-a133-mmio-map-test \
  tests/qtest/pocketforge-a133-r-i2c-test \
  tests/qtest/pocketforge-a133-axp717-test
meson test -C "$SYSTEM_BUILD_DIR" --print-errorlogs \
  qtest-aarch64/pocketforge-a100-rtc-test \
  qtest-aarch64/pocketforge-a133-input-test \
  qtest-aarch64/pocketforge-a133-mmio-map-test \
  qtest-aarch64/pocketforge-a133-r-i2c-test \
  qtest-aarch64/pocketforge-a133-axp717-test
cp "$SYSTEM_BUILD_DIR/qemu-system-aarch64" "$OUT/qemu-system-aarch64"
echo "== done: $OUT/qemu-system-aarch64 =="
"$OUT/qemu-system-aarch64" --version | head -1
"$OUT/qemu-system-aarch64" -M help | grep pocketforge-a133
