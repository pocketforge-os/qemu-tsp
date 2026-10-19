#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dockerfile="$root/ci/venus-host.Dockerfile"

grep -Fqx 'ARG LLVM_PARALLEL_COMPILE_JOBS=16' "$dockerfile"
grep -Fq 'test "$LLVM_PARALLEL_COMPILE_JOBS" -le 16' "$dockerfile"
grep -Fq 'ninja -C /src/llvm-build -j"$LLVM_PARALLEL_COMPILE_JOBS" install-distribution' "$dockerfile"
! grep -Fq 'ninja -C /src/llvm-build -j4 install-distribution' "$dockerfile"
printf 'venus_host_dockerfile_test=PASS llvm_parallel_compile_jobs=16 bound=16\n'
