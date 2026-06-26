# qemu-tsp

A thin PocketForge fork of **qemu-user** (the `aarch64-linux-user` target) that adds a
generic **evdev (`EVIOC*`) / uinput (`UI_*`) ioctl pass-through** so an *unmodified arm64
binary* run under qemu-user on an x86_64 host can probe a host-synthesized input/uinput
device **indistinguishably from real hardware**.

It is a **BUILD / SIM-HOST TOOL ONLY** — it is **never shipped in a PocketForge device
image**. It exists so the off-hardware device simulator (`pocketforge-os/sim`, epic E5 /
`infra-104`) can run the *identical* arm64 OCI app binary the device runs, against a
descriptor-synthesized `uinput` device, on a GPU-less x86 host. It keeps modelmaker / x86
as the simulator + CI host.

## Why this exists

Stock qemu-user translates **zero** evdev/uinput ioctls and does **not** pass unlisted
ioctls through to the host kernel: an arm64 binary gets `ENOTTY` (errno 25) on every
`EVIOCGID` / `EVIOCGNAME` / `EVIOCGBIT` / `EVIOCGABS` where a native x86_64 binary
succeeds (only the event `read()` stream + the byte-identical struct ABI pass). Since
SDL3/libevdev probe a device's capabilities at `open()`, a stock-qemu simulator is
trivially distinguishable from hardware. Empirically verified on qemu 8.2.2; the dispatch
returns `-TARGET_ENOTTY` from the table-miss branch of `do_ioctl()`
(`linux-user/syscall.c`), and `linux-user/ioctls.h` contains no `EVIOC*`/`UI_*` entries
in 8.2.2 or current master. Not version-fixable upstream (evdev lives only in qemu's
*system-mode* `ui/input-linux.c`).

## How the fork works

`pocketforge/0001-linux-user-evdev-uinput-ioctl-passthrough.patch` adds, at the
`do_ioctl()` table-miss branch, a check: if `_IOC_TYPE(cmd) ∈ {'E','U'}`, call a generic
`do_ioctl_pf_evdev_uinput()` that does a **raw buffer pass-through** driven by the
command's own `_IOC_DIR` / `_IOC_SIZE`:

- aarch64 and x86_64 share the asm-generic ioctl encoding, so the guest `cmd` equals the
  host `cmd` bit-for-bit and is handed to the host `ioctl()` unchanged.
- every relevant payload struct (`input_id`/`input_absinfo`/`input_event`/`uinput_setup`/
  `uinput_abs_setup`/…) is **byte-identical** between the two LP64 little-endian ABIs
  (proven by executing a layout dumper under qemu-aarch64: `input_event`=24,
  `input_absinfo`=24, `input_id`=8, `uinput_setup`=92, …), so a `_IOC_SIZE`-driven
  `memcpy` in the `_IOC_DIR` direction is a faithful translation.
- the heap buffer is sized to `_IOC_SIZE` (variable-size `EVIOCGNAME`/`EVIOCGBIT` are
  handled generically — no per-ioctl special case needed) and zeroed; the host ioctl's
  positive return value (e.g. the `EVIOCGNAME`/`EVIOCGBIT` byte count) flows back to the
  guest unchanged.

## Scope / honesty (what this does NOT do)

- It fixes the evdev/uinput **PROBE path** the simulator needs (`EVIOCG*` reads + the
  pointer-argument `UI_DEV_SETUP`/`UI_ABS_SETUP` writes).
- **Value-argument** WRITE ioctls that pass an `int` by value despite an `_IOW(...,int)`
  encoding (`UI_SET_EVBIT`/`KEYBIT`/`ABSBIT`, `EVIOCGRAB`, `EVIOCREVOKE`) are **out of
  scope** — a pointer copy can't represent them. In the simulator the virtual device is
  created **host-natively** by the VDB, so these never traverse qemu-tsp.
- **Force-feedback** uploads (`EVIOCSFF` / `UI_*_FF_UPLOAD`) embed a *guest pointer*
  (`ff_periodic_effect.custom_data`) the host kernel can't dereference; custom periodic FF
  is out of scope (`FF_RUMBLE` itself carries no pointer).
- It does **NOT** make guest **seccomp / enforcement** testable (qemu-user stubs
  `PR_SET_SECCOMP`→EINVAL). Isolation/confinement stays a hardware/substrate gate.

## Build

See [BUILD.md](BUILD.md). TL;DR on an x86_64 Linux host:

```sh
./build.sh           # clones the pinned upstream qemu, applies the patch, builds
```

Output: `build/qemu-tsp/qemu-aarch64` (static). Register via binfmt or invoke directly:
`qemu-aarch64 ./your-arm64-binary`.

## Verify (regression)

```sh
QEMU_TSP=$PWD/build/qemu-tsp/qemu-aarch64 regression/run-regression.sh
```

It synthesizes a host-native "TRIMUI Player1" (045e:028e) uinput gamepad, probes it
natively (x86) and under qemu-tsp (arm64), and asserts the two are **byte-identical**
(`regression/baseline/out.native.txt` ⇔ `regression/out.qemu-tsp.txt`). Needs `/dev/uinput`
and `sudo`.

## Provenance

Pinned upstream: see [UPSTREAM](UPSTREAM). Reproducibility today = pinned ref + patch.
Future hardening (tracked): mirror the upstream source tarball to the PocketForge S3/IPFS
artifact mirror so the build is independent of gitlab.com availability.
