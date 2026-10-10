#!/usr/bin/env python3
"""Verify the pinned build-6 guest before the full-system QEMU smoke."""

import argparse
import gzip
import hashlib
from pathlib import Path
import sys


EXPECTED = {
    "image": "db8a54a5c850afb3d1655a3e8062bbff77315f08cef1c1563611d676761d715d",
    "dtb": "1a9042d839ee9d1548062efacc5dde332789c6dc846f21bb2c7877045d49b358",
    "initrd": "93504ef5954b7cda5fdeb16577353277a89ac8f12bb321cff227f059c54c9fe6",
    "rootfs": "e76f7379c5684884da6469366b4e49103706a868ec3476d27d66530a2ef18af6",
}

REQUIRED_CONFIG = (
    "CONFIG_SERIAL_8250",
    "CONFIG_SERIAL_8250_DW",
    "CONFIG_INPUT_UINPUT",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def embedded_config(image: Path) -> dict[str, str]:
    contents = image.read_bytes()
    start_marker = b"IKCFG_ST"
    end_marker = b"IKCFG_ED"
    start = contents.find(start_marker)
    if start < 0:
        raise ValueError("kernel has no IKCONFIG start marker")
    start += len(start_marker)
    end = contents.find(end_marker, start)
    if end < 0:
        raise ValueError("kernel has no IKCONFIG end marker")
    text = gzip.decompress(contents[start:end]).decode("utf-8")
    config = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            name, value = line.split("=", 1)
            config[name] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            config[line[2:-11]] = "n"
    return config


def unavailable_drivers(config: dict[str, str]) -> list[str]:
    """Return drivers that are not guaranteed available at early boot."""
    # The shipped decoder starts before a module-presence check can be made;
    # built-ins keep READY fail-closed and self-contained.
    return [name for name in REQUIRED_CONFIG if config.get(name, "n") != "y"]


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in EXPECTED:
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()

    failed = False
    for name, expected in EXPECTED.items():
        path = getattr(args, name)
        actual = sha256(path)
        status = "ok" if actual == expected else "mismatch"
        print(f"ARTIFACT name={name} sha256={actual} status={status}")
        failed |= actual != expected

    try:
        config = embedded_config(args.image)
    except (OSError, ValueError, gzip.BadGzipFile) as error:
        print(f"PREFLIGHT result=BLOCKED reason=kernel-config-unreadable detail={error}")
        return 1

    missing = unavailable_drivers(config)
    for name in REQUIRED_CONFIG:
        value = config.get(name, "n")
        print(f"KCONFIG name={name} value={value}")

    if failed:
        print("PREFLIGHT result=BLOCKED reason=artifact-identity-mismatch")
        return 1
    if missing:
        print("PREFLIGHT result=BLOCKED reason=guest-mcu-drivers-unavailable "
              f"missing={','.join(missing)}")
        return 1
    print("PREFLIGHT result=READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
