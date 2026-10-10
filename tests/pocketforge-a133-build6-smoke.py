#!/usr/bin/env python3
"""Boot build 6 and prove MCU-backed QMP input at the guest evdev node."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import select
import shutil
import socket
import subprocess
import sys
import time


EXPECTED = {
    "image": "db8a54a5c850afb3d1655a3e8062bbff77315f08cef1c1563611d676761d715d",
    "dtb": "1a9042d839ee9d1548062efacc5dde332789c6dc846f21bb2c7877045d49b358",
    "initrd": "93504ef5954b7cda5fdeb16577353277a89ac8f12bb321cff227f059c54c9fe6",
    "sd": "c2e684bda333be6784fa47839776ba6b805ecdd70585cfe841f268f3c9f117ae",
    "rootfs": "e76f7379c5684884da6469366b4e49103706a868ec3476d27d66530a2ef18af6",
}

QEMU_STACK_VERSION_PREFIX = "pocketforge-qemu-tsp-head="

ROOTFS_OFFSET = 127_926_272
ROOTFS_SIZE = 1_518_338_048
SMOKE_SCRIPT = """#!/bin/sh
set -eu
exec 3>/dev/kmsg
printf 'PF_SMOKE_START\\n' >&3
if timeout 5s systemctl is-active --quiet multi-user.target; then
    multi_user_active=true
    boot_gate=multi-user
else
    multi_user_active=false
    boot_gate=systemd-service-start
fi
printf 'PF_SMOKE_MULTI_USER active=%s gate=%s\\n' \
    "$multi_user_active" "$boot_gate" >&3
if timeout 5s systemctl is-active --quiet pf-input-decode.service; then
    decoder_start=auto
else
    decoder_start=explicit
    systemctl --no-block start pf-input-decode.service >/dev/null 2>&1 &
    printf 'PF_SMOKE_DECODER_START queued=true\\n' >&3
fi
printf 'PF_SMOKE_DECODER mode=%s\\n' "$decoder_start" >&3
broker_was_active=0
restore_broker() {
    if [ "$broker_was_active" -eq 1 ]; then
        timeout 5s systemctl start pf-input-broker.service
        broker_was_active=0
    fi
}
trap restore_broker EXIT
event_name=
printf 'PF_SMOKE_EVENT_SCAN begin\\n' >&3
tries=0
while [ ! -r /sys/class/input/event4/device/name ] &&
      [ "$tries" -lt 2000 ]; do
    tries=$((tries + 1))
done
if [ -r /sys/class/input/event4/device/name ]; then
    event_name=event4
fi
if [ -n "$event_name" ] &&
   [ "$(cat /sys/class/input/event4/device/name)" != "TRIMUI Player1" ]; then
    event_name=
fi
printf 'PF_SMOKE_EVENT_SCAN end device=%s\\n' "${event_name:-none}" >&3
if [ -z "$event_name" ]; then
    if timeout 10s strace -f -e trace=openat,ioctl,read \\
        /usr/bin/pf-input-decode --right /dev/ttyS3 \\
        --left /dev/ttyS4 >&3 2>&1; then
        :
    fi
    printf 'PF_SMOKE_FAIL reason=gamepad-not-found\\n' >&3
    exit 1
fi
if timeout 5s systemctl is-active --quiet pf-input-broker.service; then
    timeout 5s systemctl stop pf-input-broker.service
    broker_was_active=1
fi
mkfifo /tmp/pf-evtest.pipe
mkfifo /tmp/pf-evtest-ready.pipe
chmod 0666 /tmp/pf-evtest.pipe
# Keep one read/write descriptor open while the asynchronous reader and writer
# attach; otherwise dash can block on the background command's redirection.
exec 4<>/tmp/pf-evtest.pipe
(
    ready_sent=0
    key_press=0
    key_release=0
    hat_press=0
    hat_release=0
    abs_x=0
    abs_y=0
    abs_rx=0
    abs_ry=0
    abs_z_press=0
    abs_z_release=0
    abs_rz_press=0
    abs_rz_release=0
    while IFS= read -r line; do
        printf '%s\\n' "$line"
        case "$line" in
            Event:*) printf 'PF_EVTEST %s\\n' "$line" >&3 ;;
        esac
        case "$line" in
            'Testing ...'*)
                if [ "$ready_sent" -eq 0 ]; then
                    printf 'ready\\n' >/tmp/pf-evtest-ready.pipe
                    ready_sent=1
                fi
                ;;
        esac
        case "$line" in
            *'code 304 (BTN_SOUTH), value 1'*|*'code 304 (BTN_A), value 1'*)
                key_press=1 ;;
            *'code 304 (BTN_SOUTH), value 0'*|*'code 304 (BTN_A), value 0'*)
                key_release=1 ;;
            *'code 17 (ABS_HAT0Y), value -1'*) hat_press=1 ;;
            *'code 17 (ABS_HAT0Y), value 0'*) hat_release=1 ;;
            *'code 0 (ABS_X), value 0'*) abs_x=1 ;;
            *'code 1 (ABS_Y), value 4095'*) abs_y=1 ;;
            *'code 3 (ABS_RX), value 4095'*) abs_rx=1 ;;
            *'code 4 (ABS_RY), value 0'*) abs_ry=1 ;;
            *'code 2 (ABS_Z), value 255'*) abs_z_press=1 ;;
            *'code 2 (ABS_Z), value 0'*) abs_z_release=1 ;;
            *'code 5 (ABS_RZ), value 255'*) abs_rz_press=1 ;;
            *'code 5 (ABS_RZ), value 0'*) abs_rz_release=1 ;;
        esac
        if [ "$key_press" -eq 1 ] && [ "$key_release" -eq 1 ] &&
           [ "$hat_press" -eq 1 ] && [ "$hat_release" -eq 1 ] &&
           [ "$abs_x" -eq 1 ] && [ "$abs_y" -eq 1 ] &&
           [ "$abs_rx" -eq 1 ] && [ "$abs_ry" -eq 1 ] &&
           [ "$abs_z_press" -eq 1 ] && [ "$abs_z_release" -eq 1 ] &&
           [ "$abs_rz_press" -eq 1 ] && [ "$abs_rz_release" -eq 1 ]; then
            exit 0
        fi
    done
    exit 1
) </tmp/pf-evtest.pipe >/tmp/pf-evtest.log &
watcher_pid=$!
setpriv --reuid=1001 --regid=1001 --init-groups \
    stdbuf -oL evtest --grab "/dev/input/$event_name" \
    >/tmp/pf-evtest.pipe 2>&1 &
evtest_pid=$!
if ! IFS= read -r evtest_ready </tmp/pf-evtest-ready.pipe; then
    printf 'PF_SMOKE_FAIL reason=evtest-not-ready\\n' >&3
    exit 1
fi
printf 'PF_SMOKE_READY uid=1001 device=%s name=TRIMUI_Player1 broker=%s decoder=%s\\n' \
    "$event_name" "$broker_was_active" "$decoder_start" >&3
if ! wait "$watcher_pid"; then
    printf 'PF_SMOKE_FAIL reason=event-watch\\n' >&3
    exit 1
fi
exec 4>&-
if kill -0 "$evtest_pid" 2>/dev/null; then
    kill -KILL "$evtest_pid"
fi
if wait "$evtest_pid"; then
    evtest_status=0
else
    evtest_status=$?
fi
case "$evtest_status" in
    0|137|141) ;;
    *)
        printf 'PF_SMOKE_FAIL reason=evtest-exit status=%s\\n' \
            "$evtest_status" >&3
        exit 1
        ;;
esac
grep -Eq 'code 304 [(]BTN_(SOUTH|A)[)], value 1' /tmp/pf-evtest.log
grep -Eq 'code 304 [(]BTN_(SOUTH|A)[)], value 0' /tmp/pf-evtest.log
grep -Eq 'code 17 [(]ABS_HAT0Y[)], value -1' /tmp/pf-evtest.log
grep -Eq 'code 17 [(]ABS_HAT0Y[)], value 0' /tmp/pf-evtest.log
grep -Eq 'code 0 [(]ABS_X[)], value 0' /tmp/pf-evtest.log
grep -Eq 'code 1 [(]ABS_Y[)], value 4095' /tmp/pf-evtest.log
grep -Eq 'code 3 [(]ABS_RX[)], value 4095' /tmp/pf-evtest.log
grep -Eq 'code 4 [(]ABS_RY[)], value 0' /tmp/pf-evtest.log
grep -Eq 'code 2 [(]ABS_Z[)], value 255' /tmp/pf-evtest.log
grep -Eq 'code 2 [(]ABS_Z[)], value 0' /tmp/pf-evtest.log
grep -Eq 'code 5 [(]ABS_RZ[)], value 255' /tmp/pf-evtest.log
grep -Eq 'code 5 [(]ABS_RZ[)], value 0' /tmp/pf-evtest.log
grep -Eq 'Input device ID: bus 0x3 vendor 0x45e product 0x28e version 0x110' \
    /tmp/pf-evtest.log
grep -Fq 'Input device name: "TRIMUI Player1"' /tmp/pf-evtest.log
check_abs_range() {
    awk -v code="$1" -v want_max="$2" '
        /Event code [0-9]+ [(]/ {
            active = index($0, "(" code ")") != 0
            if (active) {
                seen = 1
                minimum = ""
                maximum = ""
            }
            next
        }
        active && /Min[[:space:]]+-?[0-9]+/ { minimum = $NF }
        active && /Max[[:space:]]+-?[0-9]+/ { maximum = $NF }
        END { exit !(seen && minimum == 0 && maximum == want_max) }
    ' /tmp/pf-evtest.log
}
check_abs_range ABS_X 4095
check_abs_range ABS_Y 4095
check_abs_range ABS_RX 4095
check_abs_range ABS_RY 4095
check_abs_range ABS_Z 255
check_abs_range ABS_RZ 255
if grep -Eq 'type 5 [(]EV_SW[)]|SW_LID' /tmp/pf-evtest.log; then
    printf 'PF_SMOKE_FAIL reason=negative-event-delivered\\n' >&3
    exit 1
fi
restore_broker
printf 'PF_SMOKE_PASS uid=1001 key=BTN_SOUTH:1,0 hat=ABS_HAT0Y:-1,0 sticks=ABS_X:0,ABS_Y:4095,ABS_RX:4095,ABS_RY:0 triggers=ABS_Z:255,0,ABS_RZ:255,0 ranges=sticks:0..4095,triggers:0..255 negative_ev_sw=absent broker_restored=true decoder=%s\\n' "$decoder_start" >&3
systemctl poweroff
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_out_head(source_root: Path) -> str:
    head = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    for command in (
        ["git", "-C", str(source_root), "diff", "--quiet", "HEAD", "--"],
        ["git", "-C", str(source_root), "diff", "--cached", "--quiet", "HEAD", "--"],
    ):
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError("source-tree-not-clean")
    return head


def verify_qemu_provenance(qemu: Path, source_root: Path) -> None:
    head = checked_out_head(source_root)
    version = subprocess.run(
        [str(qemu), "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()[0]
    marker = f"{QEMU_STACK_VERSION_PREFIX}{head}"
    if marker not in version:
        raise RuntimeError(f"qemu-provenance:{marker}:{version}")
    print(
        f"QEMU_PROVENANCE head={head} sha256={sha256(qemu)} status=ok",
        flush=True,
    )


def copy_range(source: Path, destination: Path, offset: int, size: int) -> None:
    remaining = size
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        input_file.seek(offset)
        while remaining:
            block = input_file.read(min(1024 * 1024, remaining))
            if not block:
                raise RuntimeError("rootfs-short-read")
            output_file.write(block)
            remaining -= len(block)


def write_range(source: Path, destination: Path, offset: int, size: int) -> None:
    remaining = size
    with source.open("rb") as input_file, destination.open("r+b") as output_file:
        output_file.seek(offset)
        while remaining:
            block = input_file.read(min(1024 * 1024, remaining))
            if not block:
                raise RuntimeError("rootfs-short-write-source")
            output_file.write(block)
            remaining -= len(block)


def connect(path: Path, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(path))
            return client
        except OSError:
            client.close()
            time.sleep(0.1)
    raise RuntimeError(f"socket-timeout:{path.name}")


class JsonLine:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = b""

    def receive(self, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if line.strip():
                    return json.loads(line)
            readable, _, _ = select.select(
                [self.sock], [], [], max(0, deadline - time.monotonic())
            )
            if readable:
                data = self.sock.recv(65536)
                if not data:
                    raise RuntimeError("qmp-eof")
                self.buffer += data
        raise RuntimeError("qmp-timeout")

    def command(self, execute: str, arguments: dict | None = None) -> dict:
        request = {"execute": execute}
        if arguments is not None:
            request["arguments"] = arguments
        self.sock.sendall(json.dumps(request).encode() + b"\n")
        while True:
            response = self.receive()
            if "event" not in response:
                return response


class Console:
    def __init__(self, sock: socket.socket, transcript: Path):
        self.sock = sock
        self.buffer = b""
        self.transcript = transcript.open("wb")

    def close(self) -> None:
        self.transcript.close()

    def until(self, needle: bytes, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            failure = self.buffer.find(b"PF_SMOKE_FAIL ")
            if failure >= 0:
                line_end = self.buffer.find(b"\n", failure)
                if line_end >= 0:
                    line = self.buffer[failure:line_end].decode(
                        errors="replace"
                    )
                    raise RuntimeError(f"guest-failure:{line}")
            if needle in self.buffer:
                end = self.buffer.index(needle) + len(needle)
                found = self.buffer[:end]
                self.buffer = self.buffer[end:]
                return found.decode(errors="replace")
            readable, _, _ = select.select(
                [self.sock], [], [], max(0, deadline - time.monotonic())
            )
            if readable:
                data = self.sock.recv(65536)
                if not data:
                    raise RuntimeError("serial-eof")
                self.transcript.write(data)
                self.transcript.flush()
                self.buffer += data
        tail = self.buffer[-1000:].decode(errors="replace")
        raise RuntimeError(f"serial-timeout:{needle!r}:{tail}")


def prepare_smoke_disk(sd: Path, work: Path) -> Path:
    smoke_sd = work / "Image.smoke.img"
    shutil.copyfile(sd, smoke_sd)
    with smoke_sd.open("r+b") as image_file:
        image_file.truncate(2 * 1024 * 1024 * 1024)
    seed = work / "seed"
    seed.mkdir(exist_ok=True)
    for stale in seed.iterdir():
        if not stale.is_file() and not stale.is_symlink():
            raise RuntimeError(f"unexpected-seed-entry:{stale.name}")
        stale.unlink()
    smoke_script = seed / "pf-qdbus-smoke"
    smoke_script.write_text(SMOKE_SCRIPT)
    smoke_unit = seed / "pf-qdbus-smoke.service"
    smoke_unit.write_text(
        "[Unit]\n"
        "Description=PocketForge build-6 QEMU input smoke\n"
        "After=local-fs.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=/usr/local/sbin/pf-qdbus-smoke\n"
        "StandardOutput=journal+console\n"
    )
    rootfs = work / "rootfs.smoke.ext4"
    copy_range(sd, rootfs, ROOTFS_OFFSET, ROOTFS_SIZE)
    try:
        actual = sha256(rootfs)
        if actual != EXPECTED["rootfs"]:
            raise RuntimeError(f"artifact-mismatch:rootfs:{actual}")
        print(f"ARTIFACT name=rootfs sha256={actual} status=ok", flush=True)
        commands = (
            f"write {smoke_script} /usr/local/sbin/pf-qdbus-smoke",
            "set_inode_field /usr/local/sbin/pf-qdbus-smoke mode 0100755",
            f"write {smoke_unit} /etc/systemd/system/pf-qdbus-smoke.service",
            "symlink /etc/systemd/system/multi-user.target.wants/"
            "pf-qdbus-smoke.service /etc/systemd/system/"
            "pf-qdbus-smoke.service",
        )
        for command in commands:
            subprocess.run(
                ["debugfs", "-w", "-R", command, str(rootfs)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        link = subprocess.run(
            [
                "debugfs", "-R",
                "stat /etc/systemd/system/multi-user.target.wants/"
                "pf-qdbus-smoke.service",
                str(rootfs),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        if "Fast link dest: \"/etc/systemd/system/pf-qdbus-smoke.service\"" not in link:
            raise RuntimeError("smoke-service-link-missing")
        write_range(rootfs, smoke_sd, ROOTFS_OFFSET, ROOTFS_SIZE)
    finally:
        if rootfs.exists():
            rootfs.unlink()
    for path in (smoke_script, smoke_unit):
        if path.exists():
            path.unlink()
    try:
        seed.rmdir()
    except OSError as error:
        raise RuntimeError(f"seed-cleanup:{error}") from error
    if sha256(sd) != EXPECTED["sd"]:
        raise RuntimeError("source-sd-mutated")
    return smoke_sd


def event(event_type: int, code: int, value: int) -> dict:
    return {"type": event_type, "code": code, "value": value}


def send_report(qmp: JsonLine, *events: dict) -> dict:
    return qmp.command(
        "pocketforge-input-send",
        {"device": "gamepad", "events": [*events, event(0, 0, 0)]},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--sd", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()

    verify_qemu_provenance(args.qemu, args.source_root)
    args.work.mkdir(parents=True, exist_ok=True)
    for stale in ("qmp.sock", "serial.sock"):
        path = args.work / stale
        if path.exists():
            path.unlink()
    for name, expected in EXPECTED.items():
        if name == "rootfs":
            continue
        path = getattr(args, name)
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"artifact-mismatch:{name}:{actual}")
        print(f"ARTIFACT name={name} sha256={actual} status=ok", flush=True)

    smoke_sd = prepare_smoke_disk(args.sd, args.work)
    qmp_path = args.work / "qmp.sock"
    serial_path = args.work / "serial.sock"
    serial_log = args.work / "serial.log"
    qemu_log = args.work / "qemu-unimp.log"
    command = [
        str(args.qemu), "-accel", "tcg", "-M", "pocketforge-a133",
        "-cpu", "cortex-a53",
        "-smp", "4", "-m", "1024", "-nodefaults",
        "-kernel", str(args.image), "-dtb", str(args.dtb),
        "-initrd", str(args.initrd),
        "-append",
        "root=PARTLABEL=userdata rootwait rw console=ttyS0,115200 "
        "earlycon=uart8250,mmio32,0x05000000,115200n8 keep_bootcon loglevel=5 "
        "panic=-1 pocketforge.root=volatile video=DSI-1:d "
        "systemd.log_target=kmsg systemd.log_level=info "
        "systemd.mask=pocketforge-boot-animator.service "
        "systemd.mask=pf-shell-selected.service "
        "systemd.mask=pf-open-gpu-gate.service "
        "systemd.mask=sleep.target systemd.mask=suspend.target "
        "systemd.mask=systemd-suspend.service",
        "-drive", f"if=sd,format=raw,file={smoke_sd},snapshot=on",
        "-display", "none", "-monitor", "none", "-no-reboot",
        "-d", "unimp", "-D", str(qemu_log),
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-serial", f"unix:{serial_path},server=on,wait=off",
        "-serial", "null", "-serial", "null", "-serial", "null",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    console = None
    try:
        qmp = JsonLine(connect(qmp_path, 20.0))
        greeting = qmp.receive()
        if "QMP" not in greeting or "return" not in qmp.command("qmp_capabilities"):
            raise RuntimeError("qmp-handshake")
        display = qmp.command("query-pocketforge-display").get("return", {})
        expected_display = {
            "profile": "hardware",
            "native_width": 720,
            "native_height": 1280,
            "preferred_fourcc": "XR24",
            "presentation_rotation": 90,
        }
        for key, expected in expected_display.items():
            if display.get(key) != expected:
                raise RuntimeError(f"display-metadata:{key}:{display.get(key)}")
        print(
            "QMP_DISPLAY width=720 height=1280 fourcc=XR24 "
            "presentation_rotation=90",
            flush=True,
        )

        console = Console(connect(serial_path, 20.0), serial_log)
        console.until(b"PF_SMOKE_MULTI_USER ", 180.0)
        console.until(b"PF_SMOKE_READY ", 120.0)

        deadline = time.monotonic() + 30.0
        info = {}
        while time.monotonic() < deadline:
            info = qmp.command("query-pocketforge-input").get("return", {})
            devices = info.get("devices", [])
            if devices and devices[0].get("ready"):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"input-not-ready:{info}")

        if info.get("abi_version") != {"major": 2, "minor": 1}:
            raise RuntimeError(f"input-abi:{info}")
        devices = info.get("devices", [])
        if (len(devices) != 1 or devices[0].get("id") != "gamepad" or
                devices[0].get("name") != "TRIMUI Player1"):
            raise RuntimeError(f"input-devices:{devices}")
        if not devices[0].get("ready"):
            raise RuntimeError(f"input-not-ready:{devices[0]}")
        expected_axes = {
            0: ("ABS_X", 0, 4095), 1: ("ABS_Y", 0, 4095),
            3: ("ABS_RX", 0, 4095), 4: ("ABS_RY", 0, 4095),
            2: ("ABS_Z", 0, 255), 5: ("ABS_RZ", 0, 255),
        }
        advertised_axes = {}
        for cap in devices[0].get("caps", []):
            if cap.get("type") == 3:
                advertised_axes.update({axis["code"]: axis for axis in cap["abs"]})
        for code, (name, minimum, maximum) in expected_axes.items():
            axis = advertised_axes.get(code)
            if axis != {
                "code": code, "name": name, "min": minimum, "max": maximum,
                "fuzz": 0, "flat": 0,
            }:
                raise RuntimeError(f"input-axis:{code}:{axis}")
        print("QMP_INPUT abi=2.1 device=gamepad ready=true", flush=True)

        negative = send_report(qmp, event(5, 0, 1))
        description = negative.get("error", {}).get("desc", "")
        if not description.startswith(
            "pocketforge-input: reason=invalid-parameter index=0:"
        ):
            raise RuntimeError(f"negative-control:{negative}")
        print("QMP_NEGATIVE reason=invalid-parameter delivered=false", flush=True)

        for triple in (
            event(1, 304, 1), event(1, 304, 0),
            event(3, 17, -1), event(3, 17, 0),
            event(3, 0, 0), event(3, 1, 4095),
            event(3, 3, 4095), event(3, 4, 0),
            event(3, 2, 255), event(3, 2, 0),
            event(3, 5, 255), event(3, 5, 0),
        ):
            response = send_report(qmp, triple)
            if "return" not in response:
                raise RuntimeError(f"positive-injection:{response}")

        console.until(b"PF_SMOKE_PASS ", 60.0)
        console.until(b"\n", 5.0)
        log_text = serial_log.read_text(errors="replace")
        patterns = (
            r"code 304 \(BTN_(SOUTH|A)\), value 1",
            r"code 304 \(BTN_(SOUTH|A)\), value 0",
            r"code 17 \(ABS_HAT0Y\), value -1",
            r"code 17 \(ABS_HAT0Y\), value 0",
            r"code 0 \(ABS_X\), value 0",
            r"code 1 \(ABS_Y\), value 4095",
            r"code 3 \(ABS_RX\), value 4095",
            r"code 4 \(ABS_RY\), value 0",
            r"code 2 \(ABS_Z\), value 255",
            r"code 2 \(ABS_Z\), value 0",
            r"code 5 \(ABS_RZ\), value 255",
            r"code 5 \(ABS_RZ\), value 0",
        )
        for pattern in patterns:
            if not re.search(pattern, log_text):
                raise RuntimeError(f"guest-event-missing:{pattern}")
        if re.search(r"type 5 \(EV_SW\)|SW_LID", log_text):
            raise RuntimeError("negative-event-delivered")
        if "broker_restored=true" not in log_text:
            raise RuntimeError("broker-not-restored")
        decoder = re.search(r"PF_SMOKE_DECODER mode=(auto|explicit)", log_text)
        if decoder is None:
            raise RuntimeError("decoder-start-mode-missing")
        boot_gate = re.search(
            r"PF_SMOKE_MULTI_USER active=(true|false) "
            r"gate=(multi-user|systemd-service-start)",
            log_text,
        )
        if boot_gate is None:
            raise RuntimeError("boot-gate-missing")
        print(
            "GUEST_INPUT uid=1001 device=TRIMUI_Player1 "
            "key=BTN_SOUTH:press,release hat=ABS_HAT0Y:-1,0 "
            "sticks=ABS_X:0,ABS_Y:4095,ABS_RX:4095,ABS_RY:0 "
            "triggers=ABS_Z:255,0,ABS_RZ:255,0 "
            "ranges=sticks:0..4095,triggers:0..255 "
            "negative_ev_sw=absent broker_restored=true "
            f"decoder={decoder.group(1)} "
            f"multi_user_active={boot_gate.group(1)} "
            f"boot_gate={boot_gate.group(2)}",
            flush=True,
        )
        return 0
    finally:
        if console is not None:
            console.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        if process.stderr:
            stderr = process.stderr.read()
            if process.returncode not in (0, -15):
                print(stderr[-2000:], file=sys.stderr)
        for path in (qmp_path, serial_path):
            if path.exists():
                path.unlink()
        if smoke_sd.exists():
            smoke_sd.unlink()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"SMOKE result=FAIL reason={error}", file=sys.stderr)
        sys.exit(1)
