#!/usr/bin/env python3
"""Hermetic tests for release power-smoke provenance checks."""

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SMOKE = Path(__file__).with_name("pocketforge-a133-release-power-smoke.py")
SPEC = importlib.util.spec_from_file_location("release_power_smoke", SMOKE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProvenanceTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
