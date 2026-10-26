#!/usr/bin/env python3
"""Hermetic tests for the B45 LRADC smoke harness."""

import argparse
import importlib.util
from pathlib import Path
import unittest


SMOKE = Path(__file__).with_name("pocketforge-a133-release-lradc-smoke.py")
SPEC = importlib.util.spec_from_file_location("release_lradc_smoke", SMOKE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseLradcSmokeTest(unittest.TestCase):
    def test_seed_uses_argv_runcmd_and_exact_markers(self) -> None:
        config = MODULE.cloud_config()
        self.assertIn("- [systemctl, daemon-reload]", config)
        self.assertIn("PF_LRADC_IDLE events=0", config)
        self.assertIn("PF_LRADC_VREF supply=axp717-aldo4", config)
        self.assertNotIn("runcmd:\n  - systemctl ", config)

    def test_boot_is_volatile_snapshot_without_host_input_routes(self) -> None:
        args = argparse.Namespace(
            qemu=Path("qemu-system-aarch64"), image=Path("Image"),
            dtb=Path("dtb.bin"), initrd=Path("initrd.gz"))
        command = MODULE.qemu_command(
            args, Path("B45.img"), Path("qmp.sock"),
            Path("serial.sock"), Path("unimp.log"))
        rendered = " ".join(map(str, command))
        self.assertIn("pocketforge.root=volatile", rendered)
        self.assertIn("snapshot=on", rendered)
        for forbidden in ("input-linux", "evdev", "usb-host", "/dev/input",
                          "/dev/uinput"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
