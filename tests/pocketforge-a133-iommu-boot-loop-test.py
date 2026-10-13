#!/usr/bin/env python3
"""Hermetic integrity tests for the A133 IOMMU boot-loop evidence."""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).with_name("pocketforge-a133-iommu-boot-loop.py")
SPEC = importlib.util.spec_from_file_location("iommu_boot_loop", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BOOT_LOOP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT_LOOP)


class EvidenceIntegrityTest(unittest.TestCase):
    def test_launched_process_is_bound_to_hashed_payload(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            actual = BOOT_LOOP.require_process_payload(
                process, Path(sys.executable), time.monotonic() + 1
            )
            self.assertTrue(Path(actual).samefile(sys.executable))
            with self.assertRaisesRegex(RuntimeError, "qemu-evidence-unbound"):
                BOOT_LOOP.require_process_payload(
                    process, Path("/bin/true"), time.monotonic() + 0.05
                )
        finally:
            process.terminate()
            process.wait(timeout=2)

    def test_stale_artifact_cannot_publish_pass_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "qemu"
            summary_path = root / "summary.json"
            artifact.write_bytes(b"verified")
            expected = BOOT_LOOP.sha256(artifact)
            artifacts = {"qemu": (artifact, expected)}
            summary = {"ready": 1, "readiness_failures": 0}

            artifact.write_bytes(b"stale")
            with self.assertRaisesRegex(RuntimeError, "artifact-mismatch:qemu"):
                BOOT_LOOP.publish_verified_summary(
                    summary_path, summary, artifacts, "IOMMU_BOOT_LOOP result=PASS"
                )
            self.assertFalse(summary_path.exists())

            artifact.write_bytes(b"verified")
            with contextlib.redirect_stdout(io.StringIO()):
                BOOT_LOOP.publish_verified_summary(
                    summary_path, summary, artifacts, "IOMMU_BOOT_LOOP result=PASS"
                )
            self.assertTrue(summary_path.is_file())


if __name__ == "__main__":
    unittest.main()
