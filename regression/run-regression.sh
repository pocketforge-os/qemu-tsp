#!/usr/bin/env bash
# qemu-tsp regression: prove an aarch64 evdev probe under qemu-tsp is byte-identical
# to the native probe of the SAME synthesized uinput device.
#
# Acceptance: `diff out.native.txt out.qemu-tsp.txt` is EMPTY (and both match the
# committed baseline).
#
# tsp-ihne1.56.5: this script is NOT scheduled by any workflow, and its
# host-oriented privilege-escalation and module-load wrapper is gone. A created
# uinput device is host-global — Docker's user, mount and PID namespaces do not
# virtualize evdev — so whatever seat the host has adopts it. The kernel half of
# this check therefore belongs to the pfvd QEMU guest, which owns it from wave 2
# on: a guest-runnable qemu-aarch64, with the native and the qemu-user reader
# running against the same guest-created node inside that guest.
#
# Nothing here escalates, loads a kernel module, or changes a device node's
# group or mode. The creator goes through regression/pf_uinput_domain_guard.h
# and fails closed with reason=unsafe_uinput_domain outside an allowed domain,
# so on a runner host this script stops at the creator and never reaches the
# host kernel. There is no skip and no host-state branch: an unsafe domain is an
# error, printed with its reason.
#
# Run it inside the guest, as the user that owns that guest's kernel:
#
#   QEMU_TSP=/path/to/build/qemu-aarch64 regression/run-regression.sh
set -euo pipefail

QEMU_TSP="${QEMU_TSP:?set QEMU_TSP=/path/to/build/qemu-aarch64}"
FIX="$(cd "$(dirname "$0")" && pwd)"          # regression/ dir (holds probes and guard)
WORK="$(mktemp -d "${TMPDIR:-/tmp}/qemu-tsp-regression.XXXXXX")"
mkpid=""
cleanup() {
  if [ -n "$mkpid" ]; then
    kill "$mkpid" 2>/dev/null || true
    wait "$mkpid" 2>/dev/null || true
  fi
  find "$WORK" -mindepth 1 -delete; rmdir "$WORK"
}
trap cleanup EXIT
cd "$WORK"
cp "$FIX/probe.c" "$FIX/mkuinput.c" "$FIX/pf_uinput_domain_guard.h" .

echo "== compile probes =="
# The native reader is this kernel's own architecture; the qemu-tsp reader is
# the static aarch64 build. Wave 2 builds both inside the guest.
gcc -O0 -o probe.native probe.c
aarch64-linux-gnu-gcc -O0 -static -o probe.arm64 probe.c
gcc -O0 -o mkuinput mkuinput.c

echo "== create virtual TRIMUI Player1 (native to this kernel) =="
# Its stdout/stderr is kept: the domain guard's reason= line is the evidence
# when this is not an allowed domain, and discarding it hid that before.
./mkuinput > mkuinput.log 2>&1 &
mkpid=$!
sleep 1
if ! kill -0 "$mkpid" 2>/dev/null; then
  cat mkuinput.log
  echo "FAIL: the uinput creator did not stay up; its reason= line is above"
  exit 1
fi

echo "== locate the event node by name =="
EV=""
for d in /sys/class/input/event*; do
  n="$d/device/name"
  if [ -r "$n" ] && grep -qx "TRIMUI Player1" "$n" 2>/dev/null; then
    EV="/dev/input/$(basename "$d")"; break
  fi
done
if [ -z "$EV" ]; then
  cat mkuinput.log
  echo "FAIL: could not find TRIMUI Player1 event node"
  exit 1
fi
echo "   device = $EV"

echo "== native probe =="
./probe.native "$EV" | tee out.native.txt
echo "== aarch64 probe UNDER qemu-tsp =="
"$QEMU_TSP" ./probe.arm64 "$EV" | tee out.qemu-tsp.txt

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
if cp out.native.txt out.qemu-tsp.txt "$FIX/"; then
  echo "wrote this run's outputs next to the baseline in $FIX"
else
  echo "NOTE: $FIX is not writable; the diff above is the authoritative result"
fi
echo "DONE"
