#!/usr/bin/env python3
"""Hermetic positive and negative controls for build-6 smoke log gates."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


SMOKE_PATH = Path(__file__).with_name("pocketforge-a133-build6-smoke.py")
SPEC = importlib.util.spec_from_file_location("pocketforge_build6_smoke", SMOKE_PATH)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class DisplayTimeoutTest(unittest.TestCase):
    def test_listener_digest_positive_and_negative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pf-dbus-listener-") as tmp:
            listener = Path(tmp) / "listener"
            listener.write_bytes(b"reviewed-listener\n")
            expected = hashlib.sha256(listener.read_bytes()).hexdigest()

            with self.assertRaisesRegex(RuntimeError,
                                        "^artifact-mismatch:dbus-listener:"):
                SMOKE.require_hash("dbus-listener", listener, "0" * 64)
            SMOKE.require_hash("dbus-listener", listener, expected)

    def test_clean_display_log_is_accepted(self) -> None:
        log = """\
[drm] Initialized sun4i-drm 1.0.0 for display-engine on minor 0
sun4i-drm display-engine: bound 6100000.mixer
sun4i-drm display-engine: bound 6510000.tcon-top
sun4i-drm display-engine: bound 6511000.lcd-controller
sun4i-drm display-engine: bound 6504000.dsi
sun6i-mipi-dsi 6504000.dsi: Attached device otm1289a
"""
        self.assertIsNone(SMOKE.find_display_timeout(log))

    def test_each_display_timeout_form_is_rejected(self) -> None:
        failures = (
            "sun4i-drm display-engine: timeout waiting for atomic commit",
            "6100000.mixer: blender update timed out",
            "timeout waiting for 6510000.tcon-top gate",
            "6511000.lcd-controller: fifo timeout",
            "6504000.dsi: transfer timed out",
            "timeout: 6505000.phy calibration incomplete",
            "panel-orisetech-otm1289a 6504000.dsi.0: enable timeout",
            "[drm] atomic commit timed out",
        )
        for line in failures:
            with self.subTest(line=line):
                self.assertEqual(SMOKE.find_display_timeout(line), line)


if __name__ == "__main__":
    unittest.main()
