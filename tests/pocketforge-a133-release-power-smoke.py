#!/usr/bin/env python3
"""Prove the unmodified release stack owns A133 suspend and poweroff."""

import argparse
import hashlib
import json
from pathlib import Path
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time


QEMU_STACK_VERSION_PREFIX = "pocketforge-qemu-tsp-head="
OBSERVER = """#!/bin/sh
set -eu
exec 3>/dev/kmsg

fail() {
    printf '<3>PF_RELEASE_FAIL reason=%s\\n' "$1" >&3
    exit 1
}

timeout 120 sh -c '
    while ! systemctl is-active --quiet systemd-logind.service; do
        sleep 1
    done
' || fail logind-not-active
printf '<3>PF_RELEASE_STAGE logind=active\\n' >&3

launcher=$(timeout 120 sh -c '
    while :; do
        for unit in pf-shell-selected.service pocketforge-launcher.service; do
            if systemctl is-active --quiet "$unit"; then
                printf "%s\\n" "$unit"
                exit 0
            fi
        done
        sleep 1
    done
') || fail launcher-not-active
test -n "$launcher" || fail launcher-not-active

if systemctl is-enabled pfvd-powerdown.service >/dev/null 2>&1 ||
   systemctl is-active --quiet pfvd-powerdown.service; then
    fail substitute-power-listener-present
fi
if systemctl is-enabled systemd-logind.service 2>/dev/null | grep -qx masked; then
    fail logind-masked
fi
if systemctl is-enabled pf-shell-selected.service 2>/dev/null | grep -qx masked; then
    fail launcher-masked
fi

mem_sleep=$(cat /sys/power/mem_sleep) || fail mem-sleep-unreadable
test "$mem_sleep" = '[s2idle]' || fail mem-sleep-not-s2idle
printf '<3>PF_RELEASE_READY logind=active launcher=%s mem_sleep=%s\\n' \
    "$launcher" "$mem_sleep" >&3
"""

SLEEP_HOOK = """#!/bin/sh
set -eu
exec 3>/dev/kmsg
case "$1" in
    pre)
        printf '<3>PF_RELEASE_SUSPEND phase=pre mode=%s mem_sleep=%s\\n' \
            "$2" "$(cat /sys/power/mem_sleep)" >&3
        ;;
    post)
        printf '<3>PF_RELEASE_SUSPEND phase=post mode=%s mem_sleep=%s\\n' \
            "$2" "$(cat /sys/power/mem_sleep)" >&3
        ;;
esac
"""

OBSERVER_UNIT = """[Unit]
Description=PocketForge release ownership smoke observer
After=systemd-logind.service cloud-init.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/pf-release-observer
StandardOutput=journal+console

[Install]
WantedBy=multi-user.target
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_out_head(source_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def verify_qemu_provenance(qemu: Path, source_root: Path,
                           expected_qemu: str) -> None:
    head = checked_out_head(source_root)
    for command in (
        ["git", "-C", str(source_root), "diff", "--quiet", "HEAD", "--"],
        ["git", "-C", str(source_root), "diff", "--cached", "--quiet",
         "HEAD", "--"],
    ):
        if subprocess.run(command, check=False).returncode != 0:
            raise RuntimeError("source-tree-not-clean")
    require_hash("qemu", qemu, expected_qemu)
    version = subprocess.run(
        [str(qemu), "--version"], check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    ).stdout.splitlines()[0]
    marker = f"{QEMU_STACK_VERSION_PREFIX}{head}"
    if marker not in version:
        raise RuntimeError(f"qemu-provenance:{marker}:{version}")
    print(f"QEMU_PROVENANCE head={head} sha256={expected_qemu} status=ok",
          flush=True)


def require_hash(name: str, path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"artifact-mismatch:{name}:{actual}")
    print(f"ARTIFACT name={name} sha256={actual} status=ok", flush=True)


def yaml_block(text: str, spaces: int = 6) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line for line in text.rstrip().splitlines())


def cloud_config() -> str:
    return (
        "#cloud-config\n"
        "write_files:\n"
        "  - path: /usr/local/sbin/pf-release-observer\n"
        "    owner: root:root\n"
        "    permissions: '0755'\n"
        "    content: |\n"
        f"{yaml_block(OBSERVER)}\n"
        "  - path: /usr/lib/systemd/system-sleep/pf-release-smoke\n"
        "    owner: root:root\n"
        "    permissions: '0755'\n"
        "    content: |\n"
        f"{yaml_block(SLEEP_HOOK)}\n"
        "  - path: /etc/systemd/system/pf-release-observer.service\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        f"{yaml_block(OBSERVER_UNIT)}\n"
        "runcmd:\n"
        "  - [systemctl, daemon-reload]\n"
        "  - [systemctl, enable, --now, pf-release-observer.service]\n"
    )


def gpt_partition_offsets(image: Path) -> list[int]:
    sector_size = 512
    with image.open("rb") as stream:
        stream.seek(sector_size)
        header = stream.read(92)
        if header[:8] != b"EFI PART":
            raise RuntimeError("gpt-header-missing")
        header_size = struct.unpack_from("<I", header, 12)[0]
        entries_lba = struct.unpack_from("<Q", header, 72)[0]
        entry_count = struct.unpack_from("<I", header, 80)[0]
        entry_size = struct.unpack_from("<I", header, 84)[0]
        if not 92 <= header_size <= sector_size:
            raise RuntimeError(f"gpt-header-size:{header_size}")
        if not 128 <= entry_size <= 4096 or entry_count > 4096:
            raise RuntimeError(
                f"gpt-entry-layout:{entry_count}:{entry_size}")
        stream.seek(entries_lba * sector_size)
        entries = stream.read(entry_count * entry_size)
        if len(entries) != entry_count * entry_size:
            raise RuntimeError("gpt-entry-table-short-read")

    offsets = []
    for index in range(entry_count):
        entry = entries[index * entry_size:(index + 1) * entry_size]
        if entry[:16] == bytes(16):
            continue
        first_lba = struct.unpack_from("<Q", entry, 32)[0]
        last_lba = struct.unpack_from("<Q", entry, 40)[0]
        if first_lba == 0 or last_lba < first_lba:
            raise RuntimeError(f"gpt-entry-range:{index}")
        offsets.append(first_lba * sector_size)
    return offsets


def pocketforge_partition_offset(image: Path) -> int:
    matches = []
    for offset in gpt_partition_offsets(image):
        label = subprocess.run(
            ["mlabel", "-i", mtools_image(image, offset), "-s", "::"],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
        if label.returncode == 0 and label.stdout.strip().endswith(
                "POCKETFORGE"):
            matches.append(offset)
    if len(matches) != 1:
        raise RuntimeError(f"pocketforge-partition-count:{len(matches)}")
    return matches[0]


def mtools_image(image: Path, offset: int) -> str:
    return f"{image}@@{offset}"


def prepare_run_disk(source: Path, work: Path) -> Path:
    run_disk = work / "Image.release-smoke.img"
    shutil.copyfile(source, run_disk)
    with run_disk.open("r+b") as stream:
        capacity = stream.seek(0, 2)
        if capacity > 2 * 1024 * 1024 * 1024:
            raise RuntimeError(f"sd-too-large:{capacity}")
        stream.truncate(2 * 1024 * 1024 * 1024)

    offset = pocketforge_partition_offset(run_disk)
    seed_files = {
        "user-data": cloud_config(),
        "meta-data": "instance-id: pf-release-power-smoke\n"
                     "local-hostname: pocketforge-smoke\n",
        "network-config": (
            "version: 2\n"
            "wifis:\n"
            "  wlan0:\n"
            "    dhcp4: true\n"
            "    optional: true\n"
            "    access-points:\n"
            "      pf-qemu-no-radio:\n"
            "        password: qemu-ci-placeholder\n"
        ),
    }
    for name, contents in seed_files.items():
        local = work / f"seed-{name}"
        local.write_text(contents)
        try:
            subprocess.run(
                ["mcopy", "-o", "-i", mtools_image(run_disk, offset),
                 str(local), f"::{name}"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            readback = subprocess.run(
                ["mtype", "-i", mtools_image(run_disk, offset), f"::{name}"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            ).stdout
            if readback != contents:
                raise RuntimeError(f"seed-readback-mismatch:{name}")
        finally:
            if local.exists():
                local.unlink()
    return run_disk


def connect(path: Path, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(path))
            return client
        except OSError:
            client.close()
            time.sleep(0.05)
    raise RuntimeError(f"socket-timeout:{path.name}")


class JsonLine:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.stream = sock.makefile("rwb", buffering=0)

    def receive(self) -> dict:
        line = self.stream.readline()
        if not line:
            raise RuntimeError("qmp-eof")
        return json.loads(line)

    def command(self, execute: str, arguments: dict | None = None) -> dict:
        message = {"execute": execute}
        if arguments is not None:
            message["arguments"] = arguments
        self.stream.write(json.dumps(message).encode() + b"\n")
        while True:
            response = self.receive()
            if "event" not in response:
                return response

    def close(self) -> None:
        self.stream.close()
        self.sock.close()


class Console:
    def __init__(self, sock: socket.socket, transcript: Path):
        self.sock = sock
        self.output = transcript.open("wb")
        self.buffer = bytearray()
        self.failure: Exception | None = None
        self.condition = threading.Condition()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        try:
            while True:
                ready, _, _ = select.select([self.sock], [], [], 1)
                if not ready:
                    continue
                chunk = self.sock.recv(4096)
                if not chunk:
                    return
                self.output.write(chunk)
                self.output.flush()
                with self.condition:
                    self.buffer.extend(chunk)
                    self.condition.notify_all()
        except Exception as error:
            with self.condition:
                self.failure = error
                self.condition.notify_all()

    def until(self, needle: bytes, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        with self.condition:
            while time.monotonic() < deadline:
                failure = self.buffer.find(b"PF_RELEASE_FAIL ")
                if failure >= 0:
                    line_end = self.buffer.find(b"\n", failure)
                    if line_end >= 0:
                        line = self.buffer[failure:line_end].decode(
                            errors="replace")
                        raise RuntimeError(f"guest-failure:{line}")
                if needle in self.buffer:
                    found = bytes(self.buffer)
                    self.buffer.clear()
                    return found.decode(errors="replace")
                if self.failure is not None:
                    raise self.failure
                self.condition.wait(max(0, deadline - time.monotonic()))
            tail = self.buffer[-1000:].decode(errors="replace")
            raise RuntimeError(f"serial-timeout:{needle!r}:{tail}")

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        self.reader.join(timeout=5)
        self.output.close()


def event(event_type: int, code: int, value: int) -> dict:
    return {"type": event_type, "code": code, "value": value}


def send_report(qmp: JsonLine, device: str, *events: dict) -> None:
    response = qmp.command(
        "pocketforge-input-send",
        {"device": device, "events": [*events, event(0, 0, 0)]},
    )
    if "return" not in response:
        raise RuntimeError(f"input-send:{device}:{response}")


def kernel_command_line() -> str:
    return (
        "root=PARTLABEL=userdata rootwait rw console=ttyS0,115200 "
        "earlycon=uart8250,mmio32,0x05000000,115200n8 keep_bootcon "
        "loglevel=6 panic=-1 pocketforge.root=volatile "
        "ds=nocloud;s=file:///boot/ "
        "systemd.log_target=kmsg systemd.log_level=info"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--expect-qemu", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--sd", type=Path, required=True)
    parser.add_argument("--expect-image", required=True)
    parser.add_argument("--expect-dtb", required=True)
    parser.add_argument("--expect-initrd", required=True)
    parser.add_argument("--expect-sd", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--source-root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    verify_qemu_provenance(args.qemu, args.source_root, args.expect_qemu)
    args.work.mkdir(parents=True, exist_ok=True)
    artifacts = (
        ("image", args.image, args.expect_image),
        ("dtb", args.dtb, args.expect_dtb),
        ("initrd", args.initrd, args.expect_initrd),
        ("sd", args.sd, args.expect_sd),
    )
    for name, path, expected in artifacts:
        require_hash(name, path, expected)

    qmp_path = args.work / "release-smoke-qmp.sock"
    serial_path = args.work / "release-smoke-serial.sock"
    serial_log = args.work / "release-smoke-serial.log"
    qemu_log = args.work / "release-smoke-unimp.log"
    for stale in (qmp_path, serial_path):
        if stale.exists():
            stale.unlink()
    run_disk = prepare_run_disk(args.sd, args.work)
    command = [
        str(args.qemu), "-accel", "tcg", "-M", "pocketforge-a133",
        "-cpu", "cortex-a53", "-smp", "4", "-m", "1024", "-nodefaults",
        "-kernel", str(args.image), "-dtb", str(args.dtb),
        "-initrd", str(args.initrd), "-append", kernel_command_line(),
        "-drive", f"if=sd,format=raw,file={run_disk},snapshot=on",
        "-display", "none", "-monitor", "none", "-no-reboot",
        "-d", "unimp", "-D", str(qemu_log),
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-serial", f"unix:{serial_path},server=on,wait=off",
        "-serial", "null", "-serial", "null", "-serial", "null",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    qmp = None
    console = None
    passed = False
    try:
        qmp = JsonLine(connect(qmp_path, 20))
        if "QMP" not in qmp.receive():
            raise RuntimeError("qmp-greeting")
        if "return" not in qmp.command("qmp_capabilities"):
            raise RuntimeError("qmp-capabilities")
        console = Console(connect(serial_path, 20), serial_log)
        console.until(b"PF_RELEASE_READY ", 240)

        info = qmp.command("query-pocketforge-input").get("return", {})
        devices = {item.get("id"): item for item in info.get("devices", [])}
        if info.get("abi_version") != {"major": 2, "minor": 4}:
            raise RuntimeError(f"input-abi:{info}")
        if set(devices) != {"gamepad", "power-key", "lid-switch",
                            "volume-rocker"}:
            raise RuntimeError(f"input-devices:{devices}")
        if not all(devices[name].get("ready")
                   for name in ("power-key", "lid-switch", "volume-rocker")):
            raise RuntimeError(f"input-not-ready:{devices}")

        send_report(qmp, "lid-switch", event(5, 0, 1))
        console.until(b"PF_RELEASE_SUSPEND phase=pre mode=suspend", 60)
        send_report(qmp, "lid-switch", event(5, 0, 0))
        console.until(b"PF_RELEASE_SUSPEND phase=post mode=suspend", 60)
        print("GUEST_SUSPEND owner=systemd-logind mode=s2idle resumed=true",
              flush=True)

        send_report(qmp, "power-key", event(1, 116, 1), event(1, 116, 0))
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("guest-poweroff-did-not-exit-qemu") from error
        if process.returncode != 0:
            raise RuntimeError(f"qemu-exit-status:{process.returncode}")
        console.until(b"Powering off", 5)
        log = serial_log.read_text(errors="replace")
        for marker in (
            "PF_RELEASE_READY logind=active launcher=",
            "PF_RELEASE_SUSPEND phase=pre mode=suspend mem_sleep=[s2idle]",
            "PF_RELEASE_SUSPEND phase=post mode=suspend mem_sleep=[s2idle]",
            "Powering off",
        ):
            if marker not in log:
                raise RuntimeError(f"serial-marker-missing:{marker}")
        print("RELEASE_POWER result=PASS owner=systemd-logind "
              "launcher=running qemu_exit=0 qmp_quit=false", flush=True)
        passed = True
        return 0
    finally:
        if qmp is not None:
            qmp.close()
        if console is not None:
            console.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if process.stderr is not None:
            stderr = process.stderr.read()
            if not passed and stderr:
                print(stderr[-2000:], file=sys.stderr)
        for path in (qmp_path, serial_path, run_disk):
            if path.exists():
                path.unlink()
        require_hash("sd-after", args.sd, args.expect_sd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"SMOKE result=FAIL reason={error}", file=sys.stderr)
        sys.exit(1)
