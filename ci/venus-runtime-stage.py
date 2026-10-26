#!/usr/bin/env python3
"""Stage a relocatable QEMU + Venus/Lavapipe runtime from a build root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


CHUNK = 1024 * 1024
LOCK_SCHEMA = "pocketforge.venus-host-closure/v1"
RUNTIME_SCHEMA = "pocketforge.venus-runtime/v1"


class Error(Exception):
    """A concise, stable failure reason."""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            value.update(block)
    return value.hexdigest()


def regular(path: Path, reason: str, executable: bool = False) -> Path:
    try:
        info = path.stat()
    except OSError as exc:
        raise Error(reason) from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise Error(reason)
    if executable and not os.access(path, os.X_OK):
        raise Error(reason)
    return path.resolve(strict=True)


def remove_created_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            path.rmdir()
    root.rmdir()


def ldd_dependencies(path: Path, search: list[Path]) -> tuple[list[tuple[str, Path]], str | None]:
    env = dict(os.environ, LC_ALL="C")
    env["LD_LIBRARY_PATH"] = ":".join(str(item) for item in search)
    result = subprocess.run(
        ["ldd", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        env=env,
    )
    static = result.returncode != 0 and any(
        marker in result.stdout
        for marker in ("not a dynamic executable", "statically linked")
    )
    if result.returncode != 0 and not static:
        raise Error("runtime_dependency_resolution_failed")
    if static:
        return [], None

    dependencies: list[tuple[str, Path]] = []
    loader: str | None = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("linux-vdso"):
            continue
        if "=> not found" in line:
            raise Error("runtime_dependency_missing")
        if "=>" in line:
            candidate = line.split("=>", 1)[1].strip().split(" ", 1)[0]
        else:
            candidate = line.split(" ", 1)[0]
        if not candidate.startswith("/"):
            raise Error("runtime_dependency_resolution_failed")
        source = regular(Path(candidate), "runtime_dependency_missing")
        name = Path(candidate).name
        dependencies.append((name, source))
        if "ld-linux" in name or name.startswith("ld-"):
            loader = name
    return dependencies, loader


def copy_real(source: Path, destination: Path, copied: dict[str, str]) -> None:
    source = source.resolve(strict=True)
    source_sha = digest(source)
    key = destination.relative_to(destination.parents[1]).as_posix()
    previous = copied.get(key)
    if previous is not None and previous != source_sha:
        raise Error("runtime_library_name_collision")
    if previous is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=True)
        copied[key] = source_sha


def stage(args: argparse.Namespace) -> None:
    sources = {
        "qemu": regular(args.qemu, "invalid_qemu", executable=True),
        "render_server": regular(
            args.render_server, "invalid_render_server", executable=True
        ),
        "virglrenderer": regular(args.virglrenderer, "invalid_virglrenderer"),
        "vulkan_loader": regular(args.vulkan_loader, "invalid_vulkan_loader"),
        "lavapipe": regular(args.lavapipe, "invalid_lavapipe"),
        "llvm": regular(args.llvm, "invalid_llvm"),
        "icd": regular(args.icd, "invalid_icd"),
        "lock": regular(args.lock, "invalid_lock"),
    }
    try:
        lock = json.loads(sources["lock"].read_text())
        icd = json.loads(sources["icd"].read_text())
    except (OSError, UnicodeError, ValueError) as exc:
        raise Error("invalid_metadata") from exc
    if not isinstance(lock, dict) or lock.get("schema") != LOCK_SCHEMA:
        raise Error("invalid_lock")
    required = {"llvm", "mesa", "vulkan_headers", "vulkan_loader", "virglrenderer"}
    if not required.issubset(lock):
        raise Error("invalid_lock")
    if not isinstance(icd, dict) or not isinstance(icd.get("ICD"), dict):
        raise Error("invalid_icd")

    output = args.output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise Error("output_exists")
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    committed = False
    try:
        copied: dict[str, str] = {}
        explicit = {
            "qemu": staging / "bin/qemu-system-aarch64",
            "render_server": staging / "libexec/virgl_render_server.bin",
            "virglrenderer": staging / "lib/libvirglrenderer.so.1",
            "vulkan_loader": staging / "lib/libvulkan.so.1",
            "lavapipe": staging / "lib/libvulkan_lvp.so",
            "llvm": staging / "lib/libLLVM.so.20.1",
        }
        for name, destination in explicit.items():
            copy_real(sources[name], destination, copied)
        render_binary = staging / "libexec/virgl_render_server.bin"
        render_binary.chmod(0o444)

        search = list(dict.fromkeys(source.parent for source in sources.values()))
        loader_names: set[str] = set()
        for name in ("qemu", "render_server", "virglrenderer", "vulkan_loader", "lavapipe", "llvm"):
            dependencies, loader = ldd_dependencies(sources[name], search)
            if loader:
                loader_names.add(loader)
            for dependency_name, source in dependencies:
                copy_real(source, staging / "lib" / dependency_name, copied)
        if len(loader_names) > 1:
            raise Error("runtime_loader_collision")
        loader_name = next(iter(loader_names), None)
        if loader_name is None:
            raise Error("runtime_loader_missing")

        icd["ICD"]["library_path"] = "../../../lib/libvulkan_lvp.so"
        icd_path = staging / "share/vulkan/icd.d/lvp_icd.x86_64.json"
        icd_path.parent.mkdir(parents=True)
        icd_path.write_text(json.dumps(icd, sort_keys=True, separators=(",", ":")) + "\n")
        pocketforge = staging / "share/pocketforge"
        pocketforge.mkdir(parents=True)
        shutil.copy2(sources["lock"], pocketforge / "venus-host.lock.json")
        metadata = {
            "lock_sha256": digest(sources["lock"]),
            "pins": {name: lock[name] for name in sorted(required)},
            "schema": RUNTIME_SCHEMA,
        }
        (pocketforge / "venus-runtime.json").write_text(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n"
        )

        command = (
            f'exec "$root/lib/{loader_name}" --library-path "$root/lib" '
            '"$root/bin/qemu-system-aarch64" "$@"'
        )
        launcher = staging / "qemu-system-aarch64"
        launcher.write_text(
            "#!/bin/sh\nset -eu\n"
            'root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'export RENDER_SERVER_EXEC_PATH="$root/libexec/virgl_render_server"\n'
            'export VK_DRIVER_FILES="$root/share/vulkan/icd.d/lvp_icd.x86_64.json"\n'
            + command
            + "\n"
        )
        launcher.chmod(0o755)
        server_launcher = staging / "libexec/virgl_render_server"
        server_launcher.write_text(
            "#!/bin/sh\nset -eu\n"
            'root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
            f'exec "$root/lib/{loader_name}" --library-path "$root/lib" '
            '--argv0 "$0" "$root/libexec/virgl_render_server.bin" "$@"\n'
        )
        server_launcher.chmod(0o755)
        if any(path.is_symlink() for path in staging.rglob("*")):
            raise Error("runtime_symlink_forbidden")
        probe = subprocess.run(
            [str(server_launcher)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if probe.returncode in (126, 127):
            raise Error("runtime_render_launcher_failed")
        check = subprocess.run(
            [str(launcher), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        if check.returncode:
            raise Error("runtime_self_test_failed")
        staging.rename(output)
        committed = True
    finally:
        if not committed and staging.exists():
            remove_created_tree(staging)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    for name in (
        "qemu", "render-server", "virglrenderer", "vulkan-loader",
        "lavapipe", "llvm", "icd", "lock", "output",
    ):
        result.add_argument(f"--{name}", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        stage(args)
        print(f"venus_runtime_stage_status=ok output={args.output.absolute()}")
        return 0
    except Error as exc:
        print(f"venus_runtime_stage_status=failed reason={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
