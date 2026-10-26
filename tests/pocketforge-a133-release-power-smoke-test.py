#!/usr/bin/env python3
"""Hermetic tests for release power-smoke provenance checks."""

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SMOKE = Path(__file__).with_name("pocketforge-a133-release-power-smoke.py")
SPEC = importlib.util.spec_from_file_location("release_power_smoke", SMOKE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeQmp:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls = 0

    def command(self, name: str) -> dict:
        if name != "query-pocketforge-input":
            raise AssertionError(name)
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


class ProvenanceTest(unittest.TestCase):
    def test_release_boot_keeps_native_display_enabled(self) -> None:
        command_line = MODULE.kernel_command_line()
        self.assertNotIn("video=DSI-1:d", command_line)
        self.assertNotIn("systemd.mask=", command_line)

    def test_expected_qemu_hash_positive_and_negative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pf-release-provenance-") as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@pocketforge.local"],
                cwd=source, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "PocketForge Test"],
                cwd=source, check=True,
            )
            (source / "tracked").write_text("clean\n")
            subprocess.run(["git", "add", "tracked"], cwd=source, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fixture"], cwd=source,
                check=True,
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source, check=True,
                stdout=subprocess.PIPE, text=True,
            ).stdout.strip()

            qemu = root / "qemu-system-aarch64"
            qemu.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' 'QEMU emulator version 8.2.2 "
                f"(pocketforge-qemu-tsp-head={head})'\n"
            )
            qemu.chmod(0o755)
            expected = hashlib.sha256(qemu.read_bytes()).hexdigest()

            with self.assertRaisesRegex(RuntimeError,
                                        "^artifact-mismatch:qemu:"):
                MODULE.verify_qemu_provenance(qemu, source, "0" * 64)
            MODULE.verify_qemu_provenance(qemu, source, expected)

    def test_volume_ready_poll_waits_for_guest_open(self) -> None:
        qmp = FakeQmp([
            {"return": {"devices": [
                {"id": "volume-rocker", "ready": False},
            ]}},
            {"return": {"devices": [
                {"id": "volume-rocker", "ready": True},
            ]}},
        ])

        info = MODULE.wait_device_ready(
            qmp, "volume-rocker", timeout=1.0, poll_interval=0.0)

        self.assertTrue(info["devices"][0]["ready"])
        self.assertEqual(qmp.calls, 2)

    def test_volume_ready_poll_has_clear_timeout(self) -> None:
        qmp = FakeQmp([{"return": {"devices": [
            {"id": "volume-rocker", "ready": False},
        ]}}])
        with (
            mock.patch.object(MODULE.time, "monotonic",
                              side_effect=[0.0, 0.0, 1.0]),
            mock.patch.object(MODULE.time, "sleep"),
            self.assertRaisesRegex(
                RuntimeError,
                r"^input-not-ready:volume-rocker:.*'ready': False",
            ),
        ):
            MODULE.wait_device_ready(
                qmp, "volume-rocker", timeout=0.5, poll_interval=0.1)


if __name__ == "__main__":
    unittest.main()
