#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
scratch=$(mktemp -d "${RUNNER_TEMP:-/tmp}/qemu-tsp-python-cache-test.XXXXXX")
trap 'find "$scratch" -mindepth 1 -delete; rmdir "$scratch"' EXIT

source_file="$scratch/probe.py"
printf "print('stale')\n" > "$source_file"
python3 - "$source_file" <<'PY'
import py_compile
import sys

py_compile.compile(
    sys.argv[1],
    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
)
PY

printf "print('fresh')\n" > "$source_file"
PYTHONPATH="$scratch" python3 -c 'import probe' > "$scratch/negative.out"
test "$(cat "$scratch/negative.out")" = stale

PYTHONPATH="$scratch" "$ROOT/scripts/run-python-fresh.sh" \
    -c 'import probe' > "$scratch/positive.out"
test "$(cat "$scratch/positive.out")" = fresh
printf 'PYTHON_FRESH_CACHE result=PASS negative=stale positive=fresh\n'
