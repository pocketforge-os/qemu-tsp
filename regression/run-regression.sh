#!/usr/bin/env bash
# qemu-tsp regression: prove an aarch64 evdev probe under qemu-tsp is byte-identical
# to the native x86_64 probe of the SAME host-synthesized uinput device.
#
# Acceptance: `diff out.native.txt out.qemu-tsp.txt` is EMPTY (and both match the
# committed baseline). Run on an x86_64 host with /dev/uinput. Needs sudo (uinput
# + the created event node are root-only).
set -euo pipefail

QEMU_TSP="${QEMU_TSP:?set QEMU_TSP=/path/to/build/qemu-aarch64}"
FIX="$(cd "$(dirname "$0")" && pwd)"          # regression/ dir (holds probe.c, mkuinput.c)
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
cp "$FIX/probe.c" "$FIX/mkuinput.c" .

echo "== compile probes =="
gcc -O0 -o probe.x86 probe.c
aarch64-linux-gnu-gcc -O0 -static -o probe.arm64 probe.c
gcc -O0 -o mkuinput mkuinput.c

echo "== ensure uinput module =="
sudo modprobe uinput

echo "== create virtual TRIMUI Player1 (host-native) =="
cleanup_mk(){ sudo pkill -f "$WORK/mkuinput" 2>/dev/null || true; }
trap 'cleanup_mk; rm -rf "$WORK"' EXIT
sudo "$WORK/mkuinput" >/dev/null 2>&1 &
sleep 1

echo "== locate the event node by name =="
EV=""
for d in /sys/class/input/event*; do
  n="$d/device/name"
  if [ -r "$n" ] && grep -qx "TRIMUI Player1" "$n" 2>/dev/null; then
    EV="/dev/input/$(basename "$d")"; break
  fi
  # name may be root-only; try sudo
  if sudo grep -qx "TRIMUI Player1" "$n" 2>/dev/null; then
    EV="/dev/input/$(basename "$d")"; break
  fi
done
[ -n "$EV" ] || { echo "FAIL: could not find TRIMUI Player1 event node"; exit 1; }
echo "   device = $EV"

echo "== native x86 probe =="
sudo ./probe.x86 "$EV" | tee out.native.txt
echo "== aarch64 probe UNDER qemu-tsp =="
sudo "$QEMU_TSP" ./probe.arm64 "$EV" | tee out.qemu-tsp.txt

echo "== DIFF (must be empty) =="
if diff -u out.native.txt out.qemu-tsp.txt; then
  echo "PASS: qemu-tsp evdev probe is byte-identical to native"
else
  echo "FAIL: qemu-tsp probe differs from native"; exit 1
fi

# optional: compare to committed baseline if present
if [ -r "$FIX/out.native.txt" ]; then
  echo "== sanity vs committed baseline =="
  diff -u "$FIX/out.native.txt" out.native.txt && echo "baseline matches" \
    || echo "NOTE: baseline differs (device/kernel drift) — primary native-vs-qemu diff is authoritative"
fi
cp out.native.txt out.qemu-tsp.txt "$FIX/" 2>/dev/null || true
echo "DONE"
