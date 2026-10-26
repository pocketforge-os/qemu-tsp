#!/usr/bin/env python3
"""Fail-closed contract test for the A133 LRADC aperture."""

import json
from pathlib import Path


BASE = 0x05070800
SIZE = 0x400


def overlaps_lradc(item: dict[str, str]) -> bool:
    start = int(item["system_address"], 16)
    end = start + int(item["size"], 16)
    return start < BASE + SIZE and BASE < end


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
    exact = [
        region
        for region in regions
        if int(region["base"], 16) == BASE
    ]
    assert exact == [
        {
            "base": "0x5070800",
            "size": "0x400",
            "name": "pocketforge-a133.lradc",
            "kind": "stub",
            "source": "release_guest",
        }
    ], f"missing or non-exact LRADC region: {exact!r}"

    # The contract is exact: neither adjacent space nor an oversized region
    # may be admitted as a substitute for the later-DT compatibility aperture.
    for region in regions:
        start = int(region["base"], 16)
        end = start + int(region["size"], 16)
        assert not (start < BASE < end), f"overlap before LRADC: {region!r}"
        assert not (start < BASE + SIZE < end), (
            f"overlap after LRADC: {region!r}"
        )
    assert not any(
        int(region["base"], 16) == BASE and int(region["size"], 16) != SIZE
        for region in regions
    )

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
    print("pocketforge-a133-lradc-coverage=PASS")


if __name__ == "__main__":
    main()
