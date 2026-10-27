#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2251 # Literal Dockerfile patterns and negated assertions.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dockerfile="$root/ci/venus-host.Dockerfile"
lockfile="$root/ci/venus-host.lock.json"
validation_script="$root/ci/venus-validation/run-validation.sh"

grep -Fqx 'ARG LLVM_PARALLEL_COMPILE_JOBS=16' "$dockerfile"
grep -Fq 'test "$LLVM_PARALLEL_COMPILE_JOBS" -le 16' "$dockerfile"
grep -Fq 'ninja -C /src/llvm-build -j"$LLVM_PARALLEL_COMPILE_JOBS" install-distribution' "$dockerfile"
! grep -Fq 'ninja -C /src/llvm-build -j4 install-distribution' "$dockerfile"

grep -Fqx 'FROM virglrenderer AS venus-host-validate' "$dockerfile"
grep -Fqx 'COPY --from=venus-host-validate /venus-host-validation.json /venus-host-validation.json' "$dockerfile"
grep -Fq 'repository=$(python3 -c '\''import json; print(json.load(open("/venus-host.lock.json"))["virglrenderer"]["repository"])'\'');' "$dockerfile"
grep -Fq 'git -C /src/virglrenderer remote add origin "$repository";' "$dockerfile"
grep -Fq 'git -C /src/virglrenderer fetch -q --depth 1 origin "$commit";' "$dockerfile"
grep -Fq 'test "$(git -C /src/virglrenderer rev-parse HEAD)" = "$commit";' "$dockerfile"
grep -Fq '["virglrenderer_validation"]["repository"]' "$dockerfile"
grep -Fq '["virglrenderer_validation"]["commit"]' "$dockerfile"
grep -Fq 'git -C /src/virglrenderer-validation fetch -q --depth 1 origin "$commit";' "$dockerfile"
grep -Fq 'test "$(git -C /src/virglrenderer-validation rev-parse HEAD)" = "$commit"' "$dockerfile"
! grep -Fq 'remote add origin https://gitlab.freedesktop.org/virgl/virglrenderer.git' "$dockerfile"
! grep -Fq 'COPY ci/venus-validation/venus_vtest_memory.c' "$dockerfile"
! grep -Fq 'COPY --chmod=0755 ci/venus-validation/run-venus-vtest-memory.sh' "$dockerfile"
test ! -e "$root/ci/venus-validation/venus_vtest_memory.c"
test ! -e "$root/ci/venus-validation/run-venus-vtest-memory.sh"

python3 - "$dockerfile" "$lockfile" <<'PY'
import json
import sys
from pathlib import Path

dockerfile = Path(sys.argv[1]).read_text(encoding="utf-8")
lock = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

production_flags = """    meson setup /src/virgl-build /src/virglrenderer \\
      --buildtype=release \\
      --prefix=/opt/venus \\
      --wrap-mode=nodownload \\
      -Dplatforms=[] \\
      -Dvenus=true \\
      -Dvulkan-dload=true \\
      -Ddrm-renderers=[] \\
      -Drender-server-worker=process \\
      -Dvideo=false \\
      -Dtests=false \\
      -Dfuzzer=false \\
      -Dtracing=none; \\
"""
assert dockerfile.count(production_flags) == 1, "production virglrenderer flags changed"
assert dockerfile.count("COPY ci/venus-host.lock.json /venus-host.lock.json") >= 5
assert "RUN /validation/run-validation.sh" in dockerfile

virgl = lock["virglrenderer"]
assert virgl["repository"] == "https://github.com/pocketforge-os/virglrenderer.git"
assert len(virgl["commit"]) == 40
assert set(virgl["commit"]) <= set("0123456789abcdef")
assert virgl["version"] == "1.3.0"
assert virgl["features"] == ["venus", "render-server"]

harness = lock["virglrenderer_validation"]
assert harness == {
    "repository": "https://github.com/pocketforge-os/virglrenderer.git",
    "commit": "7b9afc384c72a6f3f238ec20963620c1fa760bcb",
    "paths": ["tests/venus"],
}
PY

sh -n "$root/ci/venus-validation/run-validation.sh"
python3 - "$validation_script" <<'PY'
import re
import sys
from pathlib import Path

script = Path(sys.argv[1]).read_text(encoding="utf-8")
guest_build = 'timeout 20m ninja -C "$guest_build" -j4 \\'
assert guest_build in script, "guest Mesa ninja call must use -j4"
ninja_calls = [
    line.strip()
    for line in script.splitlines()
    if re.search(r"(?:^|\s)ninja(?:\s|$)", line)
]
assert ninja_calls, "validation script has no ninja invocation"
assert all(re.search(r"\s-j[1-9][0-9]*(?:\s|\\$)", line) for line in ninja_calls), (
    f"unbounded ninja call(s): {ninja_calls}"
)
PY
grep -Fq 'rev-parse HEAD:vtest' "$root/ci/venus-validation/run-validation.sh"
grep -Fq 'runner=$harness_tests/run-venus-vtest-memory.sh' "$root/ci/venus-validation/run-validation.sh"
grep -Fq 'venus_named_wall=VK_ERROR_OUT_OF_DEVICE_MEMORY' "$root/ci/venus-validation/run-validation.sh"
grep -Fq 'tail -n 40 -- "$client_log"' "$root/ci/venus-validation/run-validation.sh"
grep -Fq '^FAIL [^:]+: -2$' "$root/ci/venus-validation/run-validation.sh"

printf '%s\n' 'venus_host_dockerfile_test=PASS llvm_parallel_compile_jobs=16 bound=16 guest_ninja_jobs=4 validation_stage=present receipt_dependency=present exact_sha=verified production_flags=unchanged repository=lock harness=lock vtest_identity=checked'
