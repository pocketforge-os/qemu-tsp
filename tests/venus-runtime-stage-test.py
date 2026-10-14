#!/usr/bin/env python3
"""Hermetic positive and negative tests for the Venus runtime stager."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "ci/venus-runtime-stage.py"
LOCK = ROOT / "ci/venus-host.lock.json"


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(STAGER), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"expected rc={expect}, got {result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def arguments(root: Path, output: Path) -> list[str]:
    return [
        "--qemu", str(root / "qemu"),
        "--render-server", str(root / "render-server"),
        "--virglrenderer", str(root / "libvirglrenderer.so"),
        "--vulkan-loader", str(root / "libvulkan.so"),
        "--lavapipe", str(root / "libvulkan_lvp.so"),
        "--llvm", str(root / "libLLVM.so"),
        "--icd", str(root / "lvp_icd.json"),
        "--lock", str(LOCK),
        "--output", str(output),
    ]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="venus-runtime-stage-test.") as td:
        root = Path(td)
        fixture = Path("/bin/true").resolve(strict=True)
        for name in (
            "qemu", "render-server", "libvirglrenderer.so",
            "libvulkan.so", "libvulkan_lvp.so", "libLLVM.so",
        ):
            shutil.copy2(fixture, root / name)
        (root / "lvp_icd.json").write_text(json.dumps({
            "file_format_version": "1.0.1",
            "ICD": {
                "api_version": "1.4.328",
                "library_path": "/opt/venus/lib/libvulkan_lvp.so",
            },
        }) + "\n")

        output = root / "runtime"
        result = run(*arguments(root, output))
        assert "venus_runtime_stage_status=ok" in result.stdout
        assert subprocess.run([str(output / "qemu-system-aarch64"), "--version"]).returncode == 0
        assert not any(path.is_symlink() for path in output.rglob("*"))
        assert all(path.is_file() for path in output.rglob("*") if not path.is_dir())

        launcher = (output / "qemu-system-aarch64").read_text()
        assert 'LD_LIBRARY_PATH="$root/lib"' in launcher
        assert 'RENDER_SERVER_EXEC_PATH="$root/libexec/virgl_render_server"' in launcher
        assert 'VK_DRIVER_FILES="$root/share/vulkan/icd.d/lvp_icd.x86_64.json"' in launcher
        icd = json.loads((output / "share/vulkan/icd.d/lvp_icd.x86_64.json").read_text())
        assert icd["ICD"]["library_path"] == "../../../lib/libvulkan_lvp.so"
        metadata = json.loads((output / "share/pocketforge/venus-runtime.json").read_text())
        assert metadata["schema"] == "pocketforge.venus-runtime/v1"
        assert metadata["lock_sha256"] == hashlib.sha256(LOCK.read_bytes()).hexdigest()

        missing = root / "missing-lavapipe"
        bad_args = arguments(root, root / "bad-runtime")
        bad_args[bad_args.index("--lavapipe") + 1] = str(missing)
        negative = run(*bad_args, expect=1)
        assert "reason=invalid_lavapipe" in negative.stderr
        assert not (root / "bad-runtime").exists()

        collision = run(*arguments(root, output), expect=1)
        assert "reason=output_exists" in collision.stderr

    print("PASS venus_runtime_stage positive missing_lavapipe output_collision")


if __name__ == "__main__":
    main()
