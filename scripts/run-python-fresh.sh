#!/usr/bin/env bash
set -euo pipefail

pycache=$(mktemp -d "${RUNNER_TEMP:-/tmp}/qemu-tsp-pycache.XXXXXX")
cleanup() {
  find "$pycache" -mindepth 1 -delete
  rmdir "$pycache"
}
trap cleanup EXIT

PYTHONPYCACHEPREFIX="$pycache" python3 "$@"
