#!/bin/sh
# SPDX-License-Identifier: MIT

set -eu

validation_dir=/validation
renderer_source=/src/virglrenderer
harness_source=/src/virglrenderer-validation
harness_tests=$harness_source/tests/venus
host_build=/src/virgl-build
guest_source=/src/mesa-guest
guest_build=/src/mesa-guest-build
host_icd=/opt/venus/share/vulkan/icd.d/lvp_icd.x86_64.json
host_libdir=/opt/venus/lib/x86_64-linux-gnu:/opt/venus/lib
guest_icd=$guest_build/src/virtio/vulkan/virtio_devenv_icd.x86_64.json
log_root=/venus-host-validation-logs
runner=$harness_tests/run-venus-vtest-memory.sh

mkdir -p "$log_root"

renderer_vtest_tree=$(git -C "$renderer_source" rev-parse HEAD:vtest)
harness_vtest_tree=$(git -C "$harness_source" rev-parse HEAD:vtest)
if [ "$renderer_vtest_tree" != "$harness_vtest_tree" ]; then
   printf 'venus_vtest_identity=FAIL renderer_tree=%s harness_tree=%s\n' \
      "$renderer_vtest_tree" "$harness_vtest_tree"
   exit 1
fi
printf 'venus_vtest_identity=PASS renderer_tree=%s harness_tree=%s\n' \
   "$renderer_vtest_tree" "$harness_vtest_tree"
test -x "$runner"
test -f "$harness_tests/venus_vtest_memory.c"

timeout 2m cp -a -- /src/mesa "$guest_source"
timeout 30s git -C "$guest_source" apply \
   "$validation_dir/gpuum-vtest-import.patch"
timeout 5m meson setup "$guest_build" "$guest_source" \
   -Dvulkan-drivers=virtio \
   -Dgallium-drivers= \
   -Dplatforms= \
   -Dglx=disabled \
   -Degl=disabled \
   -Dgbm=disabled \
   -Dllvm=disabled \
   -Dshared-llvm=disabled \
   -Dbuild-tests=false \
   -Dlibunwind=disabled \
   -Dvalgrind=disabled
timeout 20m ninja -C "$guest_build" \
   src/virtio/vulkan/libvulkan_virtio.so \
   src/virtio/vulkan/virtio_devenv_icd.x86_64.json

# shellcheck disable=SC2046 # pkg-config output is the compiler argument list.
timeout 1m cc -std=c11 -Wall -Wextra -Werror \
   "$validation_dir/venus_dmabuf_alloc_red.c" \
   -o "$validation_dir/venus_dmabuf_alloc_red" \
   $(pkg-config --cflags --libs vulkan)
# shellcheck disable=SC2046 # pkg-config output is the compiler argument list.
timeout 1m cc -std=c11 -Wall -Wextra -Werror \
   "$validation_dir/opaque_fd_route_probe.c" \
   -o "$validation_dir/opaque_fd_route_probe" \
   $(pkg-config --cflags --libs vulkan)

failures=0
probe_failures=0
run_probe()
{
   name=$1
   binary=$2
   log=$log_root/$name.log
   set +e
   timeout 30s env \
      VK_DRIVER_FILES="$host_icd" \
      LD_LIBRARY_PATH="$host_libdir" \
      "$binary" >"$log" 2>&1
   status=$?
   set -e
   cat "$log"
   if [ "$status" -eq 0 ]; then
      result=PASS
   else
      result=FAIL
      failures=$((failures + 1))
      probe_failures=$((probe_failures + 1))
   fi
   printf 'venus_probe_summary probe=%s exit=%s result=%s\n' \
      "$name" "$status" "$result"
}

run_probe lavapipe-dmabuf-allocation-red \
   "$validation_dir/venus_dmabuf_alloc_red"
run_probe opaque-fd-route "$validation_dir/opaque_fd_route_probe"

case_count=0
case_failures=0
infrastructure_failures=0
for mode in sync async; do
   mode_log=$log_root/$mode
   mkdir -p "$mode_log"
   for case_name in \
      happy happy-unaligned implicit-unaligned foreign wrong-size \
      wrong-size-unaligned wrong-offset wrong-memory-type; do
      case_count=$((case_count + 1))
      set +e
      timeout 45s env \
         HOST_BUILD="$host_build" \
         GUEST_ICD="$guest_icd" \
         HOST_ICD="$host_icd" \
         HOST_LIBDIR="$host_libdir" \
         LOG_DIR="$mode_log" \
         VN_ALLOC_MODE="$mode" \
         "$runner" "$case_name"
      status=$?
      set -e

      prefix=$mode_log/venus-vtest-memory-$case_name
      summary=$prefix.summary.log
      wall_matches=$(grep -h -E \
         'minigbm_allocation is not enabled|VK_ERROR_OUT_OF_DEVICE_MEMORY' \
         "$prefix".*.log 2>/dev/null | awk 'END { print NR }')
      if [ "$status" -eq 0 ]; then
         result=PASS
         outcome=expected
      else
         result=FAIL
         failures=$((failures + 1))
         case_failures=$((case_failures + 1))
         if [ "$status" -eq 124 ]; then
            outcome=outer-timeout
            infrastructure_failures=$((infrastructure_failures + 1))
         elif [ "$status" -eq 125 ]; then
            outcome=server-not-ready
            infrastructure_failures=$((infrastructure_failures + 1))
         elif [ ! -f "$summary" ]; then
            outcome=harness-or-build
            infrastructure_failures=$((infrastructure_failures + 1))
         elif [ "$wall_matches" -gt 0 ]; then
            outcome=gbm-stub-wall
         else
            outcome=expectation-mismatch
         fi
      fi
      printf 'venus_vtest_summary case=%s mode=%s exit=%s result=%s outcome=%s wall_matches=%s\n' \
         "$case_name" "$mode" "$status" "$result" "$outcome" \
         "$wall_matches"
   done
done

if [ "$failures" -eq 0 ]; then
   verdict=PASS
else
   verdict=FAIL
fi

export case_count case_failures harness_vtest_tree infrastructure_failures
export probe_failures renderer_vtest_tree verdict
python3 - <<'PY'
import json
import os
from pathlib import Path

lock = json.loads(Path("/venus-host.lock.json").read_text(encoding="utf-8"))
receipt = {
    "cases": int(os.environ["case_count"]),
    "case_failures": int(os.environ["case_failures"]),
    "harness_commit": lock["virglrenderer_validation"]["commit"],
    "harness_repository": lock["virglrenderer_validation"]["repository"],
    "harness_vtest_tree": os.environ["harness_vtest_tree"],
    "infrastructure_failures": int(os.environ["infrastructure_failures"]),
    "probe_failures": int(os.environ["probe_failures"]),
    "renderer_vtest_tree": os.environ["renderer_vtest_tree"],
    "schema": "pocketforge.venus-host-validation/v1",
    "verdict": os.environ["verdict"],
    "virglrenderer_commit": lock["virglrenderer"]["commit"],
    "virglrenderer_repository": lock["virglrenderer"]["repository"],
}
Path("/venus-host-validation.json").write_text(
    json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
printf 'venus_validation_receipt='
cat /venus-host-validation.json

if [ "$failures" -ne 0 ]; then
   grep -R -h -E \
      'minigbm_allocation is not enabled|VK_ERROR_OUT_OF_DEVICE_MEMORY' \
      "$log_root" || true
   printf 'venus_host_validation=FAIL cases=%s case_failures=%s infrastructure_failures=%s probes=2 probe_failures=%s\n' \
      "$case_count" "$case_failures" "$infrastructure_failures" \
      "$probe_failures"
   exit 1
fi

printf 'venus_host_validation=PASS cases=%s case_failures=0 infrastructure_failures=0 probes=2 probe_failures=0\n' \
   "$case_count"
