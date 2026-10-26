#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2251 # Literal Dockerfile patterns and negated assertions.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dockerfile="$root/ci/venus-host.Dockerfile"
lockfile="$root/ci/venus-host.lock.json"

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
grep -Fq 'rev-parse HEAD:vtest' "$root/ci/venus-validation/run-validation.sh"
grep -Fq 'runner=$harness_tests/run-venus-vtest-memory.sh' "$root/ci/venus-validation/run-validation.sh"

printf '%s\n' 'venus_host_dockerfile_test=PASS llvm_parallel_compile_jobs=16 bound=16 validation_stage=present receipt_dependency=present exact_sha=verified production_flags=unchanged repository=lock harness=lock vtest_identity=checked'
