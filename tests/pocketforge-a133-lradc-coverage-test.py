#!/usr/bin/env python3
"""Fail-closed contract test for the A133 LRADC aperture."""

import json
from pathlib import Path


BASE = 0x05070800
SIZE = 0x400


def main() -> None:
    manifest = json.loads(
        (Path(__file__).parents[1] / "pocketforge/a133-mmio-coverage.json")
        .read_text()
    )
    regions = manifest["qemu_regions"]
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
            "source": "dt",
        }
    ], f"missing or non-exact LRADC region: {exact!r}"

    # The contract is exact: neither adjacent space nor an oversized region
    # may be admitted as a substitute for the DT-declared aperture.
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
    print("pocketforge-a133-lradc-coverage=PASS")


if __name__ == "__main__":
    main()
