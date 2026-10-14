#!/usr/bin/env python3
"""Require PocketForge QMP commands to follow the machine's build condition."""

import argparse
from pathlib import Path
import re


COMMANDS = (
    "query-pocketforge-input",
    "pocketforge-input-send",
    "query-pocketforge-display",
)
CONDITION = "CONFIG_POCKETFORGE_A133"


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


def validate(schema: str) -> None:
    condition = re.compile(
        rf"'if'\s*:\s*'{re.escape(CONDITION)}'"
    )
    for name in COMMANDS:
        if not condition.search(command_definition(schema, name)):
            raise ValueError(f"command-condition-missing:{name}:{CONDITION}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("schema", type=Path)
    args = parser.parse_args()
    schema = args.schema.read_text()

    try:
        validate(schema)
    except ValueError as error:
        print(f"QAPI_CONFIG_GATING result=FAIL reason={error}")
        return 1

    negative = re.sub(
        rf",?\s*'if'\s*:\s*'{re.escape(CONDITION)}'",
        "",
        schema,
        count=1,
    )
    if negative == schema:
        print("QAPI_CONFIG_GATING result=FAIL reason=negative-control-not-mutated")
        return 1
    try:
        validate(negative)
    except ValueError as error:
        if str(error).startswith("command-condition-missing:"):
            print(
                "QAPI_CONFIG_GATING result=PASS "
                "positive=all-commands-conditional negative=missing-condition-rejected"
            )
            return 0
        print(f"QAPI_CONFIG_GATING result=FAIL reason=negative-control:{error}")
        return 1
    print("QAPI_CONFIG_GATING result=FAIL reason=negative-control-accepted")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
