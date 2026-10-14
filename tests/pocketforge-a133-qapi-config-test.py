#!/usr/bin/env python3
"""Keep device config out of common QAPI code and in the ARM dispatcher."""

import argparse
from pathlib import Path
import re


COMMANDS = (
    "query-pocketforge-input",
    "pocketforge-input-send",
    "query-pocketforge-display",
)
CONDITION = "CONFIG_POCKETFORGE_A133"
DISPATCHER = "pocketforge_a133_qmp.c"


def command_definition(schema: str, name: str) -> str:
    marker = f"'command': '{name}'"
    marker_at = schema.find(marker)
    if marker_at < 0:
        raise ValueError(f"command-missing:{name}")
    start = schema.rfind("{", 0, marker_at)
    if start < 0:
        raise ValueError(f"definition-start-missing:{name}")

    depth = 0
    quoted = False
    escaped = False
    for offset in range(start, len(schema)):
        char = schema[offset]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                quoted = False
            continue
        if char == "'":
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return schema[start:offset + 1]
    raise ValueError(f"definition-end-missing:{name}")


def validate(schema: str, meson: str, dispatcher: str) -> None:
    for name in COMMANDS:
        if CONDITION in command_definition(schema, name):
            raise ValueError(f"common-qapi-uses-device-config:{name}:{CONDITION}")

    unconditional = re.compile(
        rf"arm_ss\.add\(files\(['\"]{re.escape(DISPATCHER)}['\"]\)\)"
    )
    if not unconditional.search(meson):
        raise ValueError(f"dispatcher-not-unconditionally-linked:{DISPATCHER}")
    if "hw_arch += {'arm': arm_ss}" not in meson:
        raise ValueError("dispatcher-not-in-target-specific-arm-source-set")
    if "#include CONFIG_DEVICES" not in dispatcher:
        raise ValueError("dispatcher-missing-config-devices")
    if not re.search(rf"#if(?:def|\s+defined\()[^\n]*{CONDITION}", dispatcher):
        raise ValueError(f"dispatcher-missing-condition:{CONDITION}")
    if dispatcher.count("ERROR_CLASS_DEVICE_NOT_FOUND") < len(COMMANDS):
        raise ValueError("dispatcher-missing-device-not-found-fallbacks")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args()
    root = args.source_root
    schema = (root / "qapi/misc-arm.json").read_text()
    meson = (root / "hw/arm/meson.build").read_text()
    dispatcher = (root / "hw/arm/pocketforge_a133_qmp.c").read_text()

    try:
        validate(schema, meson, dispatcher)
    except ValueError as error:
        print(f"QAPI_CONFIG_GATING result=FAIL reason={error}")
        return 1

    negative = dispatcher.replace("ERROR_CLASS_DEVICE_NOT_FOUND", "ERROR_CLASS_GENERIC_ERROR", 1)
    if negative == dispatcher:
        print("QAPI_CONFIG_GATING result=FAIL reason=negative-control-not-mutated")
        return 1
    try:
        validate(schema, meson, negative)
    except ValueError as error:
        if str(error) == "dispatcher-missing-device-not-found-fallbacks":
            print(
                "QAPI_CONFIG_GATING result=PASS "
                "positive=target-dispatcher-fallbacks "
                "negative=missing-fallback-rejected"
            )
            return 0
        print(f"QAPI_CONFIG_GATING result=FAIL reason=negative-control:{error}")
        return 1
    print("QAPI_CONFIG_GATING result=FAIL reason=negative-control-accepted")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
