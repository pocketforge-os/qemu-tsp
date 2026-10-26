#!/usr/bin/env python3
"""Prove QMP volume-rocker injection reaches the B45 LRADC evdev node."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


POWER_SMOKE = Path(__file__).with_name(
    "pocketforge-a133-release-power-smoke.py")
SPEC = importlib.util.spec_from_file_location("release_power_smoke", POWER_SMOKE)
assert SPEC is not None and SPEC.loader is not None
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)

GUEST_PROBE = r'''#!/usr/bin/python3
import os
import pathlib
import select
import struct
import time

EVENT = struct.Struct('llHHi')
EXPECTED = [
    (1, 115, 1, 'KEY_VOLUMEUP'),
    (1, 115, 0, 'KEY_VOLUMEUP'),
    (1, 114, 1, 'KEY_VOLUMEDOWN'),
    (1, 114, 0, 'KEY_VOLUMEDOWN'),
]

def emit(message):
    with open('/dev/kmsg', 'w') as kmsg:
        kmsg.write('<3>' + message + '\n')

def fail(reason):
    emit('PF_LRADC_FAIL reason=' + reason)
    raise SystemExit(1)

deadline = time.monotonic() + 180
node = None
while time.monotonic() < deadline:
    for candidate in sorted(pathlib.Path('/dev/input').glob('event*')):
        name = pathlib.Path('/sys/class/input') / candidate.name / 'device/name'
        try:
            if name.read_text().strip() == '5070800.lradc':
                node = candidate
                break
        except OSError:
            pass
    if node is not None:
        break
    time.sleep(0.25)
if node is None:
    fail('node-timeout')

supply_uv = None
for regulator in sorted(pathlib.Path('/sys/class/regulator').glob('regulator.*')):
    try:
        if (regulator / 'name').read_text().strip() == 'axp717-aldo4':
            supply_uv = int((regulator / 'microvolts').read_text().strip())
            break
    except (OSError, ValueError):
        pass
if supply_uv != 1800000:
    fail('vref-supply-{}'.format(supply_uv))
emit('PF_LRADC_VREF supply=axp717-aldo4 supply_uv={} internal_uv=1350000'.format(
    supply_uv))

fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
try:
    readable, _, _ = select.select([fd], [], [], 2.0)
    if readable:
        fail('idle-event')
    emit('PF_LRADC_IDLE events=0')
    emit('PF_LRADC_READY device=5070800.lradc node={}'.format(node))

    buffer = bytearray()
    pending = []
    seen = []
    deadline = time.monotonic() + 120
    while seen != EXPECTED:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fail('event-timeout')
        readable, _, _ = select.select([fd], [], [], min(1.0, remaining))
        if not readable:
            continue
        try:
            chunk = os.read(fd, EVENT.size * 16)
        except BlockingIOError:
            continue
        if not chunk:
            fail('event-eof')
        buffer.extend(chunk)
        while len(buffer) >= EVENT.size:
            record = bytes(buffer[:EVENT.size])
            del buffer[:EVENT.size]
            _, _, kind, code, value = EVENT.unpack(record)
            if kind == 0 and code == 0 and value == 0:
                if len(pending) != 1:
                    fail('report-size-{}'.format(len(pending)))
                actual = pending.pop()
                index = len(seen)
                if index >= len(EXPECTED) or actual[:3] != EXPECTED[index][:3]:
                    fail('event-order-{}-{}-{}'.format(*actual))
                name = EXPECTED[index][3]
                seen.append((*actual, name))
                emit('PF_LRADC_EVENT device=5070800.lradc type={} code={} name={} value={}'.format(
                    actual[0], actual[1], name, actual[2]))
            elif kind == 0:
                fail('invalid-syn-{}-{}'.format(code, value))
            else:
                pending.append((kind, code, value))
    emit('PF_LRADC_PASS events=4 idle=clean')
finally:
    os.close(fd)
'''

PROBE_UNIT = '''[Unit]
Description=PocketForge B45 LRADC smoke probe
After=systemd-udevd.service cloud-init.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/pf-lradc-probe.py
StandardOutput=journal+console
StandardError=journal+console
TimeoutStartSec=360

[Install]
WantedBy=multi-user.target
'''


def cloud_config() -> str:
    return (
        "#cloud-config\n"
        "write_files:\n"
        "  - path: /usr/local/sbin/pf-lradc-probe.py\n"
        "    owner: root:root\n"
        "    permissions: '0755'\n"
        "    content: |\n"
        f"{COMMON.yaml_block(GUEST_PROBE)}\n"
        "  - path: /etc/systemd/system/pf-lradc-probe.service\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        f"{COMMON.yaml_block(PROBE_UNIT)}\n"
        "runcmd:\n"
        "  - [systemctl, daemon-reload]\n"
        "  - [systemctl, enable, --now, pf-lradc-probe.service]\n"
    )


def prepare_run_disk(source: Path, work: Path) -> Path:
    run_disk = work / "B45-lradc-run.img"
    shutil.copyfile(source, run_disk)
    with run_disk.open("r+b") as stream:
        capacity = stream.seek(0, 2)
        if capacity > 2 * 1024 * 1024 * 1024:
            raise RuntimeError(f"sd-too-large:{capacity}")
        stream.truncate(2 * 1024 * 1024 * 1024)
    offset = COMMON.pocketforge_partition_offset(run_disk)
    seeds = {
        "user-data": cloud_config(),
        "meta-data": "instance-id: pf-release-lradc-smoke\n"
                     "local-hostname: pocketforge-lradc\n",
        "network-config": "version: 2\nethernets: {}\n",
    }
    for name, contents in seeds.items():
        local = work / f"seed-{name}"
        local.write_text(contents)
        try:
            subprocess.run(
                ["mcopy", "-o", "-i", COMMON.mtools_image(run_disk, offset),
                 str(local), f"::{name}"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            readback = subprocess.run(
                ["mtype", "-i", COMMON.mtools_image(run_disk, offset),
                 f"::{name}"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            ).stdout
            if readback != contents:
                raise RuntimeError(f"seed-readback-mismatch:{name}")
        finally:
            local.unlink(missing_ok=True)
    return run_disk


def kernel_command_line() -> str:
    return (
        "root=PARTLABEL=userdata rootwait rw console=ttyS0,115200 "
        "earlycon=uart8250,mmio32,0x05000000,115200n8 keep_bootcon "
        "loglevel=6 panic=-1 pocketforge.root=volatile "
        "ds=nocloud;s=file:///boot/ "
        "systemd.log_target=kmsg systemd.log_level=info"
    )


def require_volume_contract(qmp: COMMON.JsonLine) -> None:
    info = qmp.command("query-pocketforge-input").get("return", {})
    if info.get("abi_version") != {"major": 2, "minor": 4}:
        raise RuntimeError(f"input-abi:{info}")
    devices = {item.get("id"): item for item in info.get("devices", [])}
    if set(devices) != {"gamepad", "power-key", "lid-switch",
                        "volume-rocker"}:
        raise RuntimeError(f"input-devices:{devices}")
    volume = devices["volume-rocker"]
    expected = {
        "id": "volume-rocker", "name": "5070800.lradc", "bustype": 25,
        "vendor": 1, "product": 1, "version": 256, "ready": True,
        "codes": [
            {"type": 0, "code": 0, "name": "SYN_REPORT"},
            {"type": 1, "code": 114, "name": "KEY_VOLUMEDOWN"},
            {"type": 1, "code": 115, "name": "KEY_VOLUMEUP"},
        ],
    }
    if volume != expected:
        raise RuntimeError(f"volume-contract:{volume}")


def qemu_command(args: argparse.Namespace, run_disk: Path,
                 qmp_path: Path, serial_path: Path, qemu_log: Path) -> list[str]:
    return [
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

    COMMON.verify_qemu_provenance(
        args.qemu, args.source_root, args.expect_qemu)
    args.work.mkdir(parents=True, exist_ok=True)
    for name, path, expected in (
        ("image", args.image, args.expect_image),
        ("dtb", args.dtb, args.expect_dtb),
        ("initrd", args.initrd, args.expect_initrd),
        ("sd", args.sd, args.expect_sd),
    ):
        COMMON.require_hash(name, path, expected)

    qmp_path = args.work / "lradc-qmp.sock"
    serial_path = args.work / "lradc-serial.sock"
    serial_log = args.work / "lradc-serial.log"
    qemu_log = args.work / "lradc-unimp.log"
    for stale in (qmp_path, serial_path):
        stale.unlink(missing_ok=True)
    run_disk = prepare_run_disk(args.sd, args.work)
    process = subprocess.Popen(
        qemu_command(args, run_disk, qmp_path, serial_path, qemu_log),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "TMPDIR": str(args.work.resolve())})
    qmp = None
    console = None
    passed = False
    try:
        qmp = COMMON.JsonLine(COMMON.connect(qmp_path, 20))
        if "QMP" not in qmp.receive():
            raise RuntimeError("qmp-greeting")
        if "return" not in qmp.command("qmp_capabilities"):
            raise RuntimeError("qmp-capabilities")
        console = COMMON.Console(COMMON.connect(serial_path, 20), serial_log)
        console.until(b"PF_LRADC_READY ", 300)
        require_volume_contract(qmp)

        for code, value in ((115, 1), (115, 0), (114, 1), (114, 0)):
            COMMON.send_report(qmp, "volume-rocker",
                               COMMON.event(1, code, value))
        console.until(b"PF_LRADC_PASS events=4 idle=clean", 120)
        log = serial_log.read_text(errors="replace")
        required = (
            "PF_LRADC_VREF supply=axp717-aldo4 supply_uv=1800000 internal_uv=1350000",
            "PF_LRADC_IDLE events=0",
            "PF_LRADC_EVENT device=5070800.lradc type=1 code=115 name=KEY_VOLUMEUP value=1",
            "PF_LRADC_EVENT device=5070800.lradc type=1 code=115 name=KEY_VOLUMEUP value=0",
            "PF_LRADC_EVENT device=5070800.lradc type=1 code=114 name=KEY_VOLUMEDOWN value=1",
            "PF_LRADC_EVENT device=5070800.lradc type=1 code=114 name=KEY_VOLUMEDOWN value=0",
        )
        for marker in required:
            if marker not in log:
                raise RuntimeError(f"serial-marker-missing:{marker}")
        print("RELEASE_LRADC result=PASS events=4 idle=clean", flush=True)
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
            path.unlink(missing_ok=True)
        COMMON.require_hash("sd-after", args.sd, args.expect_sd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"SMOKE result=FAIL reason={error}", file=sys.stderr)
        sys.exit(1)
