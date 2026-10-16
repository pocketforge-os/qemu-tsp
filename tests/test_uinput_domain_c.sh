#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
expected=538fce952f5de6cb901c46c1919aedc44dcd436e753f7786c1a6e5fe21ad2fb9
actual="$(sha256sum "$root/tests/fixtures/uinput-domain-v1.tsv" | awk '{print $1}')"
[[ "$actual" == "$expected" ]]
tmp="$(mktemp -d "${RUNNER_TEMP:-/tmp}/uinput-domain-c.XXXXXX")"
trap 'find "$tmp" -mindepth 1 -delete; rmdir "$tmp"' EXIT
cc -std=c11 -Wall -Wextra -Werror -I"$root" "$root/tests/uinput_domain_c_test.c" -o "$tmp/test"
"$tmp/test" "$root/tests/fixtures/uinput-domain-v1.tsv"

# Prove the documented regression entry point stages every quoted include and
# that its creator compiles; never execute the creator in this device-free test.
grep -Fq 'cp "$FIX/probe.c" "$FIX/mkuinput.c" "$FIX/pf_uinput_domain_guard.h" .' \
  "$root/regression/run-regression.sh"
cp "$root/regression/mkuinput.c" "$root/regression/pf_uinput_domain_guard.h" "$tmp/"
cc -O0 -Wall -Wextra -Werror -o "$tmp/mkuinput" "$tmp/mkuinput.c"

# tsp-ihne1.56.5: the regression entry point is unscheduled and the pfvd QEMU
# guest owns its kernel half from wave 2 on, so it must not carry the
# host-oriented escalation wrapper it used to. Source-only: the banned spellings
# are matched, never executed, and the two planted mutants below prove each
# assertion bites in the same invocation as the green one.
banned=(sudo modprobe chgrp chmod --device --privileged GRANT_IMAGE)

no_host_route() {
  local script="$1" token
  for token in "${banned[@]}"; do
    if grep -Fq -- "$token" "$script"; then
      echo "FAIL: ${script##*/} carries a host route: $token" >&2
      return 1
    fi
  done
}

regression="$root/regression/run-regression.sh"
no_host_route "$regression"
# Positive control: the creator is still staged, still run, and still guarded;
# a refusal is reported, never skipped.
grep -Fq './mkuinput > mkuinput.log 2>&1 &' "$regression"
grep -Fq 'FAIL: the uinput creator did not stay up' "$regression"
grep -Fq 'pf_uinput_guarded_open' "$root/regression/mkuinput.c"

# Negative controls: replant each removed host route and require a refusal.
cp "$regression" "$tmp/mutant-escalate.sh"
printf 'sudo modprobe uinput\n' >> "$tmp/mutant-escalate.sh"
if no_host_route "$tmp/mutant-escalate.sh" 2>/dev/null; then
  echo "FAIL: a replanted sudo modprobe was accepted" >&2
  exit 1
fi
cp "$regression" "$tmp/mutant-grant.sh"
printf 'chgrp "$(id -g)" "$devnode" && chmod g+rw "$devnode"\n' >> "$tmp/mutant-grant.sh"
if no_host_route "$tmp/mutant-grant.sh" 2>/dev/null; then
  echo "FAIL: a replanted device chgrp/chmod grant was accepted" >&2
  exit 1
fi

# tsp-ihne1.56.5 review round 1: each run must bind its evidence to the node
# this invocation created. The creator reports its own UI_GET_SYSNAME and the
# script derives /dev/input/event* from that identity, so a pre-existing or a
# concurrent same-name device cannot be probed in its place and reported as
# this run's result. Source-only, like every assertion here.
reports_own_identity() {
  local src="$1"
  if ! grep -Fq 'UI_GET_SYSNAME' "$src"; then return 1; fi
  if ! grep -Fq 'sysname=' "$src"; then return 1; fi
  return 0
}

binds_own_device() {
  local script="$1"
  if ! grep -Fq 'sysname=' "$script"; then return 1; fi
  if ! grep -Fq '/sys/class/input/$sysname' "$script"; then return 1; fi
  if grep -Fq '/sys/class/input/event*' "$script"; then return 1; fi
  return 0
}

mkuinput_src="$root/regression/mkuinput.c"
if ! reports_own_identity "$mkuinput_src"; then
  echo "FAIL: regression/mkuinput.c does not report the device it created" >&2
  exit 1
fi
if ! binds_own_device "$regression"; then
  echo "FAIL: run-regression.sh does not bind its evidence to this invocation's device" >&2
  exit 1
fi

# Negative controls: strip the identity report from the creator, and replant the
# global first-name-match discovery; each must be refused.
grep -v 'UI_GET_SYSNAME' "$mkuinput_src" > "$tmp/mutant-mkuinput.c"
if reports_own_identity "$tmp/mutant-mkuinput.c" 2>/dev/null; then
  echo "FAIL: a creator that does not report its own sysname was accepted" >&2
  exit 1
fi
cat > "$tmp/mutant-discovery.sh" <<'MUTANT'
EV=""
for d in /sys/class/input/event*; do
  n="$d/device/name"
  if [ -r "$n" ] && grep -qx "TRIMUI Player1" "$n" 2>/dev/null; then
    EV="/dev/input/$(basename "$d")"; break
  fi
done
MUTANT
if binds_own_device "$tmp/mutant-discovery.sh" 2>/dev/null; then
  echo "FAIL: a first-global-name-match discovery was accepted" >&2
  exit 1
fi
echo "regression-entry-point: PASS host_route=absent escalate_mutant=red grant_mutant=red guarded_creator=present own_device=bound identity_mutant=red discovery_mutant=red"
