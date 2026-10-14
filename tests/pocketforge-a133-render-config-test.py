#!/usr/bin/env python3
"""Verify A133 render-device selection follows the registered renderer type."""

import argparse
from pathlib import Path
import re


DEVICE_BRANCH = re.compile(
    r"#ifdef CONFIG_(?P<feature>[A-Z0-9_]+)\s+"
    r"gpu = qdev_new\(TYPE_VIRTIO_GPU_GL\);.*?"
    r"#else\s+gpu = qdev_new\(TYPE_VIRTIO_GPU\);\s+#endif",
    re.DOTALL,
)


def validate(source: str, meson: str) -> None:
    match = DEVICE_BRANCH.search(source)
    if not match:
        raise ValueError("render-device-branch-missing")
    if match.group("feature") != "VIRGL":
        raise ValueError(
            f"render-device-guard-is-{match.group('feature')}-not-VIRGL"
        )

    if "config_host_data.set('CONFIG_VIRGL', virgl.found())" not in meson:
        raise ValueError("virgl-build-availability-not-exported")

    # Reachable build matrix: VIRGL implies OpenGL, but OpenGL does not imply
    # VIRGL.  The latter must select the registered 2D placeholder.
    matrix = (
        (False, False, "virtio-gpu-device"),
        (True, False, "virtio-gpu-device"),
        (True, True, "virtio-gpu-gl-device"),
    )
    for opengl, virgl, expected in matrix:
        selected = "virtio-gpu-gl-device" if virgl else "virtio-gpu-device"
        if selected != expected:
            raise ValueError(
                f"matrix-mismatch:opengl={int(opengl)}:virgl={int(virgl)}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args()
    source = (args.source_root / "hw/arm/pocketforge_a133.c").read_text()
    meson = (args.source_root / "meson.build").read_text()

    try:
        validate(source, meson)
    except ValueError as error:
        print(f"A133_RENDER_CONFIG result=FAIL reason={error}")
        return 1

    negative_meson = meson.replace(
        "config_host_data.set('CONFIG_VIRGL', virgl.found())", "", 1
    )
    if negative_meson == meson:
        print("A133_RENDER_CONFIG result=FAIL reason=meson-negative-not-mutated")
        return 1
    try:
        validate(source, negative_meson)
    except ValueError as error:
        if str(error) != "virgl-build-availability-not-exported":
            print(f"A133_RENDER_CONFIG result=FAIL reason=meson-negative:{error}")
            return 1
    else:
        print("A133_RENDER_CONFIG result=FAIL reason=meson-negative-accepted")
        return 1

    negative = source.replace(
        "#ifdef CONFIG_VIRGL", "#ifdef CONFIG_OPENGL", 1
    )
    if negative == source:
        print("A133_RENDER_CONFIG result=FAIL reason=negative-control-not-mutated")
        return 1
    try:
        validate(negative, meson)
    except ValueError as error:
        if str(error) == "render-device-guard-is-OPENGL-not-VIRGL":
            print(
                "A133_RENDER_CONFIG result=PASS "
                "positive=opengl-without-virgl-fallback "
                "negative=missing-export-and-opengl-guard-rejected"
            )
            return 0
        print(f"A133_RENDER_CONFIG result=FAIL reason=negative-control:{error}")
        return 1

    print("A133_RENDER_CONFIG result=FAIL reason=negative-control-accepted")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
