#!/usr/bin/env python3
"""Bounded parallel boot loop for the A133 IOMMU readiness regression."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


READY = b"PF_TEST_AGENT_READY_V1"
PTW_TIMEOUT = b"PTW cache invalidation timed out!"
LOG_LIMIT = 256 * 1024


class EvidenceError(RuntimeError):
    """The launched process could not be bound to the verified payload."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_hash(name: str, path: Path, expected: str) -> str:
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"artifact-mismatch:{name}:{actual}")
    print(f"ARTIFACT name={name} sha256={actual} status=ok", flush=True)
    return actual


def require_process_payload(
    process: subprocess.Popen[bytes], evidence: Path, deadline: float
) -> str:
    """Bind a launched process to the independently hashed QEMU payload."""
    expected = evidence.resolve(strict=True)
    proc = Path("/proc") / str(process.pid)
    last = "process-not-inspectable"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise EvidenceError(
                f"qemu-evidence-unbound:process-exited:{process.returncode}"
            )
        try:
            executable = (proc / "exe").resolve(strict=True)
            if executable.samefile(expected):
                return str(executable)
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            decoded = [value.decode("utf-8", "surrogateescape")
                       for value in argv if value]
            # Published bundles use their private dynamic loader explicitly:
            # ld-linux --library-path DIR /bundle/bin/qemu-system-aarch64 ...
            if "--library-path" in decoded:
                option = decoded.index("--library-path")
                payload = option + 2
                if payload < len(decoded):
                    candidate = Path(decoded[payload]).resolve(strict=True)
                    if candidate.samefile(expected):
                        return str(candidate)
            last = f"exe={executable}"
        except (FileNotFoundError, OSError) as error:
            last = type(error).__name__
        time.sleep(0.01)
    raise EvidenceError(f"qemu-evidence-unbound:{last}")


def publish_verified_summary(
    summary_path: Path,
    summary: dict[str, object],
    artifacts: dict[str, tuple[Path, str]],
    result_line: str,
) -> None:
    """Publish a verdict only after every input still matches its identity."""
    for name, (path, expected) in artifacts.items():
        require_hash(f"{name}-after", path, expected)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(result_line, flush=True)


def bounded_append(log: bytearray, chunk: bytes) -> None:
    log.extend(chunk)
    if len(log) > LOG_LIMIT:
        del log[:-LOG_LIMIT]


def connect_serial(path: Path, deadline: float) -> socket.socket:
    last = "socket_unavailable"
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.1)
        try:
            client.connect(str(path))
            return client
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout,
                OSError) as error:
            last = type(error).__name__
            client.close()
            time.sleep(0.02)
    raise TimeoutError(f"socket-connect-timeout:{last}")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def boot_once(index: int, args: argparse.Namespace) -> dict[str, object]:
    boot_dir = args.output / f"boot-{index:03d}"
    boot_dir.mkdir()
    serial_path = boot_dir / "serial.log"
    stderr_path = boot_dir / "qemu-stderr.log"
    socket_path = boot_dir / "uart0.sock"
    command = [
        str(args.qemu),
        "-M", "pocketforge-a133", "-smp", "4", "-m", "1024",
        "-kernel", str(args.kernel), "-dtb", str(args.dtb),
        "-initrd", str(args.initrd),
        "-append", "console=ttyS0,115200n8 rdinit=/init pf_test_agent=uart0",
        "-display", "none", "-monitor", "none", "-no-reboot",
        "-serial", f"unix:{socket_path},server=on,wait=off",
    ]
    started = time.monotonic()
    serial = bytearray()
    ready = False
    reason = "boot-timeout"
    integrity_failure = ""
    qemu_payload = "unbound"
    process: subprocess.Popen[bytes] | None = None
    with stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
            )
            deadline = started + args.timeout
            qemu_payload = require_process_payload(
                process, args.qemu_evidence,
                min(deadline, time.monotonic() + 1),
            )
            client = connect_serial(socket_path, deadline)
            with client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        reason = f"qemu-exit:{process.returncode}"
                        break
                    client.settimeout(min(0.1, max(0.01, deadline - time.monotonic())))
                    try:
                        chunk = client.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        reason = "serial-eof"
                        break
                    bounded_append(serial, chunk)
                    if READY in serial:
                        ready = True
                        reason = "ready"
                        observe_until = min(deadline, time.monotonic() + args.observe_after_ready)
                        while time.monotonic() < observe_until:
                            client.settimeout(min(0.1, observe_until - time.monotonic()))
                            try:
                                chunk = client.recv(65536)
                            except socket.timeout:
                                continue
                            if not chunk:
                                break
                            bounded_append(serial, chunk)
                        break
        except EvidenceError as error:
            integrity_failure = str(error)
            reason = integrity_failure.replace(" ", "_")
        except (OSError, TimeoutError) as error:
            reason = str(error).replace(" ", "_")
        finally:
            if process is not None:
                stop_process(process)
    serial_path.write_bytes(serial)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    ptw_lines = serial.count(PTW_TIMEOUT)
    result = {
        "boot": index,
        "ready": ready,
        "reason": reason,
        "elapsed_ms": elapsed_ms,
        "ptw_timeouts": ptw_lines,
        "integrity_failure": integrity_failure,
        "qemu_payload": qemu_payload,
    }
    (boot_dir / "result.json").write_text(
        json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--qemu-evidence", type=Path, required=True,
                        help="ELF payload whose digest identifies the QEMU build")
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--expect-qemu", required=True)
    parser.add_argument("--expect-qemu-evidence", required=True)
    parser.add_argument("--expect-kernel", required=True)
    parser.add_argument("--expect-dtb", required=True)
    parser.add_argument("--expect-initrd", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boots", type=positive_int, default=50)
    parser.add_argument("--jobs", type=positive_int, default=8)
    parser.add_argument("--timeout", type=positive_int, default=60)
    parser.add_argument("--observe-after-ready", type=float, default=0.5)
    parser.add_argument("--max-readiness-failures", type=nonnegative_int,
                        default=0)
    parser.add_argument("--max-ptw-timeouts", type=nonnegative_int, default=0)
    args = parser.parse_args()

    if args.output.exists() and any(args.output.iterdir()):
        raise RuntimeError(f"output-not-empty:{args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "qemu": (args.qemu, args.expect_qemu),
        "qemu-evidence": (args.qemu_evidence, args.expect_qemu_evidence),
        "kernel": (args.kernel, args.expect_kernel),
        "dtb": (args.dtb, args.expect_dtb),
        "initrd": (args.initrd, args.expect_initrd),
    }
    initial_hashes = {
        name: require_hash(name, path, expected)
        for name, (path, expected) in artifacts.items()
    }
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda index: boot_once(index, args),
                                range(1, args.boots + 1)))
    results.sort(key=lambda result: int(result["boot"]))
    ready = sum(bool(result["ready"]) for result in results)
    failures = args.boots - ready
    integrity_failures = sum(bool(result["integrity_failure"])
                             for result in results)
    ptw_timeouts = sum(int(result["ptw_timeouts"]) for result in results)
    boots_with_ptw = sum(int(result["ptw_timeouts"]) > 0 for result in results)
    wall_seconds = round(time.monotonic() - started, 3)
    summary = {
        "boots": args.boots,
        "jobs": args.jobs,
        "ready": ready,
        "readiness_failures": failures,
        "integrity_failures": integrity_failures,
        "ptw_timeouts": ptw_timeouts,
        "boots_with_ptw": boots_with_ptw,
        "wall_seconds": wall_seconds,
        **{f"{name.replace('-', '_')}_sha256": digest
           for name, digest in initial_hashes.items()},
        "results": results,
    }
    result = "PASS" if (integrity_failures == 0 and
                         failures <= args.max_readiness_failures and
                         ptw_timeouts <= args.max_ptw_timeouts) else "FAIL"
    result_line = (
        f"IOMMU_BOOT_LOOP result={result} boots={args.boots} jobs={args.jobs} "
        f"ready={ready} readiness_failures={failures} "
        f"integrity_failures={integrity_failures} "
        f"ptw_timeouts={ptw_timeouts} boots_with_ptw={boots_with_ptw} "
        f"wall_seconds={wall_seconds} "
        f"qemu_sha256={initial_hashes['qemu']}"
    )
    publish_verified_summary(
        args.output / "summary.json", summary, artifacts, result_line
    )
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
