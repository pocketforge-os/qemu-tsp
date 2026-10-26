#!/usr/bin/env python3
"""Fail-closed contract test for the A133 LRADC aperture."""

import json
from pathlib import Path


BASE = 0x05070800
SIZE = 0x400
EXPECTED_REGION = {
    "base": "0x5070800",
    "size": "0x400",
    "name": "pocketforge-a133.lradc",
    "kind": "stub",
    "source": "release_guest",
}


def overlaps_lradc(item: dict[str, str]) -> bool:
    start = int(item["system_address"], 16)
    end = start + int(item["size"], 16)
    return start < BASE + SIZE and BASE < end


def validate_qemu_regions(regions: list[dict[str, str]]) -> None:
    overlapping = [
        region
        for region in regions
        if (
            int(region["base"], 16) < BASE + SIZE
            and int(region["base"], 16) + int(region["size"], 16) > BASE
        )
    ]
    assert overlapping == [EXPECTED_REGION], (
        f"LRADC overlap must be the single exact stub region: {overlapping!r}"
    )


def main() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "pocketforge/a133-mmio-coverage.json").read_text())
    regions = manifest["qemu_regions"]
    producer_enabled = [
        item
        for item in manifest["reg_inventory"]
        if item["kind"] == "address"
    ]
    release_enabled = [
        item
        for item in manifest["pins"]["release_guest_acceptance"]["reg_inventory"]
        if item["kind"] == "address"
    ]
    # Reject any enabled producer-DT tuple that intersects the release-only aperture,
    # including a tuple that covers only part of it. Half-open interval
    # comparisons keep exact adjacency outside the aperture valid.
    assert overlaps_lradc({"system_address": hex(BASE), "size": "0x100"})
    assert overlaps_lradc(
        {"system_address": hex(BASE - 0x100), "size": "0x200"}
    )
    assert not overlaps_lradc(
        {"system_address": hex(BASE - 0x100), "size": "0x100"}
    )
    assert not overlaps_lradc(
        {"system_address": hex(BASE + SIZE), "size": "0x100"}
    )
    assert not any(overlaps_lradc(item) for item in producer_enabled), (
        "pinned DT inventory unexpectedly overlaps the later LRADC aperture"
    )
    assert [item for item in release_enabled if overlaps_lradc(item)] == [
        {
            "path": "/soc/lradc@5070800",
            "index": 0,
            "kind": "address",
            "address": "0x5070800",
            "size": "0x400",
            "system_address": "0x5070800",
        }
    ], "release-guest inventory must contain exactly the LRADC aperture"
    validate_qemu_regions(regions)
    overlap_mutations = [
        {"base": "0x5070900", "size": "0x300"},
        {"base": "0x5070800", "size": "0x200"},
        {"base": "0x5070a00", "size": "0x100"},
        {"base": "0x5070700", "size": "0x200"},
        {"base": "0x5070b00", "size": "0x200"},
        EXPECTED_REGION,
    ]
    for mutation in overlap_mutations:
        try:
            validate_qemu_regions([*regions, mutation])
        except AssertionError:
            pass
        else:
            raise AssertionError(f"LRADC overlap mutation accepted: {mutation!r}")

    # A patch-stack entry is inert unless build.sh applies it to the pinned
    # upstream tree. Keep the producer-side application contract alongside
    # the manifest contract so a new aperture cannot silently disappear from
    # the built machine.
    build = (root / "build.sh").read_text()
    patch_name = "0063-hw-arm-add-A133-LRADC-aperture-stub.patch"
    assert patch_name in build, f"build.sh does not apply {patch_name}"
    assert f'git apply "$ROOT/pocketforge/{patch_name}"' in build, (
        f"build.sh does not apply the LRADC patch path for {patch_name}"
    )
    red_patch = "0068-tests-require-A133-LRADC-volume-rocker.patch"
    assert red_patch in build, f"build.sh does not apply {red_patch}"
    assert f'git apply "$ROOT/pocketforge/{red_patch}"' in build, (
        f"build.sh does not apply the LRADC red-test path for {red_patch}"
    )
    print("pocketforge-a133-lradc-coverage=PASS")


if __name__ == "__main__":
    main()
