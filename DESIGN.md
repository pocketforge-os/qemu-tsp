# PocketForge A133 D-Bus display and versioned input ABI

Status: approved Phase 1 design for `tsp-mc9m.41.984.54.12`, including gpu-14's
four amendments. This document incorporates the four-message pfvd/QEMU thread
and `pfvd-abi-requirements.txt`; Phase 2 follows this approved contract.

Input transport update (2026-10-10): the unmodified release kernel has no
virtio support, so the former virtio-input transport and ABI 1 input semantics
are superseded by the real UART3/UART4 gamepad-MCU model and input ABI 2.0 in
`docs/pocketforge-a133-input.md`. The display design below remains current.

## 1. Scope and fixed decisions

The v1 pfvd backend will use `-M pocketforge-a133` with:

- the non-GL QEMU D-Bus display and its copied `Scanout`/`Update` byte arrays;
- a raw, versioned QMP evdev injection ABI, with the existing control-name
  strings retained as convenience syntax;
- a machine-owned virtio-gpu configured for the real display's native mode;
- the real build-6 kernel, DTB, initrd and SD image for the headless smoke.

The physical display contract is deliberately not `1280x720`. Its native mode
and scanout are 720x1280; the primary plane rotation is 0; formats observed on
the primary plane are XR24, XB24 and AR24; and the panel is rotated 90 degrees
clockwise for its presented landscape orientation. D-Bus bytes remain in native
scanout order. pfvd applies the reported presentation rotation.

Here `presentation_rotation=90` means exactly: rotate the native-order buffer
90 degrees clockwise on the host to show the image upright. This direction is
not inferred from the dimensions. The owned build-6 kernel at commit
`c22dbc0226242cd1e582073eac5d82d36953c0d8`,
`arch/arm64/boot/dts/allwinner/sun50i-a133-pocketforge-tsp.dts:289-294`, says
that 90 produces upright landscape, that 270 is upside down, and sets the panel
`rotation` property to 90. This agrees with the owned gamescope work tracked as
`tsp-b25264113c4580010832`, “Vulkan final-output portrait rotation with native
720x1280 KMS scanout.”

DMA-BUF, audio, snapshots and a guest agent are not part of v1. D-Bus
Keyboard/Mouse injection is also out of scope: QMP is the one input path.

The single stable reference for every machine option and both QMP ABIs will be
the existing `docs/pocketforge-a133-input.md`. Phase 2 will broaden its title and
contents rather than create a second normative document or move the existing
path.

## 2. Build configuration and measured cost

### 2.1 Non-GL D-Bus build

The current softmmu configure line is:

```text
--target-list=aarch64-softmmu --without-default-features -Dpixman=enabled
```

With `--without-default-features`, enabling only `dbus_display` fails: Meson
reports that GIO was disabled. The required delta is therefore exactly:

```text
-Dgio=enabled -Ddbus_display=enabled
```

`build.sh` will add both flags to the softmmu configure invocation. The CI image
already installs `libglib2.0-dev`; on its pinned Ubuntu 24.04 base this provides
GIO and depends on `libglib2.0-dev-bin`, which provides
`/usr/bin/gdbus-codegen`. The measured versions were GIO 2.80.0, and
`dpkg-query -S /usr/bin/gdbus-codegen` resolved to `libglib2.0-dev-bin`.
Consequently v1 needs no Dockerfile package addition. At runtime, the executable
adds direct dependencies on `libgio-2.0.so.0` and `libgobject-2.0.so.0`; those
are already in the same Ubuntu GLib runtime closure used by the baseline.

The following are clean out-of-tree builds of the same pinned and patched QEMU
8.2.2 source in the same container. `/usr/bin/time` covered configure plus
`ninja qemu-system-aarch64`. Times are single samples and are recorded as local
cost evidence, not as a statistically stable speed claim.

| Configuration | Wall time | Executable bytes | Stripped bytes | Allocated sections (`size`) |
| --- | ---: | ---: | ---: | ---: |
| baseline: pixman | 126.62 s | 95,325,648 | 27,573,920 | 27,706,766 |
| v1: pixman + GIO + D-Bus | 183.21 s | 96,525,024 | 27,850,400 | 27,985,814 |
| optional: v1 + OpenGL/GBM | 134.62 s | 96,645,672 | 27,880,000 | 28,013,085 |

The non-GL D-Bus delta is 1,199,376 bytes unstripped (1.258%) or 276,480
bytes stripped (1.003%). The observed cold wall-time delta against the baseline
sample was 56.59 s (44.7%). The OpenGL row ran later and benefited from host
filesystem cache, so its absolute time is reported but its apparent speed-up is
not attributed to OpenGL.

### 2.2 Copy-path frame cost

The measurement payload was exactly `720 * 1280 * 4 = 3,686,400` bytes
(3.5156 MiB), matching one packed 32-bit native frame. A small measurement
harness used the CI image's GIO implementation, an authenticated peer-to-peer
Unix socket, a D-Bus method with one `ay` argument, synchronous delivery,
server-side byte-count validation, and an empty acknowledgement. Each run used
10 warm-ups followed by 100 measured frames. On an AMD Ryzen 7 7735HS:

| Run | Median | p95 | Mean | Min / max |
| --- | ---: | ---: | ---: | ---: |
| 1 | 2.844 ms | 4.049 ms | 3.030 ms | 2.025 / 5.267 ms |
| 2 | 2.558 ms | 3.824 ms | 2.718 ms | 1.920 / 5.562 ms |
| 3 | 2.887 ms | 5.064 ms | 3.150 ms | 2.263 / 5.978 ms |

This is the measured local GIO serialization/socket/deserialization/return cost
for the copied frame payload. It deliberately does not claim to include guest
rendering, QEMU's damage production, scheduling between processes, or pfvd's
texture upload. Phase 2's D-Bus qtest will exercise QEMU's actual generated
listener call and validate its bytes; this measurement establishes the v1 copy
budget without estimating it.

### 2.3 Separate GL/GBM option

DMA-BUF remains a later opt-in build step. Starting from v1, its configure delta
is `-Dopengl=enabled`; with `libepoxy-dev` and `libgbm-dev` installed, QEMU 8.2.2
detected Epoxy 1.5.10, GBM 25.2.8 and compiled `CONFIG_GBM`, making the D-Bus
`ScanoutDMABUF`/`UpdateDMABUF` methods available. It does not require
virglrenderer merely to compile those listener methods.

The measured executable increment over non-GL D-Bus was 120,648 bytes
unstripped and 29,600 bytes stripped. The two dev packages' current apt closure
added 233.13 MiB by `Installed-Size`; the measured container image grew from
493,404,132 to 736,244,275 bytes (231.59 MiB). Most of that closure was Mesa,
LLVM and graphics-loader libraries, not QEMU itself. Actual zero-copy also needs
a GL guest scanout and usable render-node/GBM plumbing. It will not be enabled,
tested with privileged containers, or included in v1.

## 3. Display machine contract

### 3.1 Machine option and ownership

The stable option will be:

```text
-M pocketforge-a133,pocketforge-display=hardware
```

`pocketforge-display` is an enum with `hardware` and `off`; its default is
`hardware`. The `hardware` profile makes the machine instantiate
`virtio-gpu-device` on virtio-mmio transport 0 with `xres=720`, `yres=1280` and
EDID enabled. The launcher must not add a second virtio GPU. `off` leaves the
transport empty and exists for negative tests and deliberately headless users.
The option is immutable after machine realization.

The guest receives 720x1280 through virtio-gpu `GET_DISPLAY_INFO` and as the
preferred EDID mode. The guest chooses and submits the virtio-gpu resource
format; QEMU's active console surface therefore remains the authority for the
actual fourcc. There is no rotation field in the virtio-gpu 1.0 display-info or
EDID protocols. The guest renders the native portrait framebuffer with plane
rotation 0, while the host-side pfvd client receives the 90-degree presentation
rotation through QMP. This explicitly avoids adding a DT property the Linux
virtio-gpu driver would ignore or pretending rotation reached the guest.

### 3.2 Display query

`query-pocketforge-display` returns the immutable profile and an optional live
scanout:

```json
{
  "return": {
    "abi_version": {"major": 1, "minor": 0},
    "profile": "hardware",
    "native_width": 720,
    "native_height": 1280,
    "plane_rotation": 0,
    "presentation_rotation": 90,
    "preferred_fourcc": "XR24",
    "supported_fourcc": ["XR24", "XB24", "AR24"],
    "active_scanout": {
      "width": 720,
      "height": 1280,
      "stride": 2880,
      "fourcc": "XR24"
    }
  }
}
```

`active_scanout` is absent before a surface exists and when the profile is
`off`. Its dimensions, stride and format are read from the active QEMU display
surface, not copied from the profile constants. The conversion table is
explicit (`PIXMAN_x8r8g8b8` to XR24, `PIXMAN_x8b8g8r8` to XB24, and
`PIXMAN_a8r8g8b8` to AR24); an unrepresentable active format produces a typed
query error rather than a guessed fourcc.

D-Bus v1 remains upstream-compatible: `Scanout` supplies width, height, stride,
the numeric pixman format and `ay` bytes, while `Update` supplies the damaged
rectangle and its copied bytes. No private D-Bus interface is added. pfvd calls
the QMP query for the native/presentation metadata and cross-checks the live
scanout against D-Bus. A guest-selected mode can differ from the preferred
native mode; in that case `native_*` continues to describe the panel while
`active_scanout` and D-Bus both report the actual surface.

## 4. Versioned QMP input ABI

### 4.1 Capability query and versioning

`query-pocketforge-input` has no arguments and returns:

```json
{
  "return": {
    "abi_version": {"major": 1, "minor": 0},
    "max_batch_events": 256,
    "devices": [{
      "id": "gamepad",
      "name": "TRIMUI Player1",
      "bustype": 3,
      "vendor": 1118,
      "product": 654,
      "version": 272,
      "ready": true,
      "caps": [
        {"type": 0, "codes": [{"code": 0, "name": "SYN_REPORT"}]},
        {"type": 1, "codes": [
          {"code": 304, "name": "BTN_SOUTH"},
          {"code": 305, "name": "BTN_EAST"},
          {"code": 307, "name": "BTN_NORTH"},
          {"code": 308, "name": "BTN_WEST"},
          {"code": 310, "name": "BTN_TL"},
          {"code": 311, "name": "BTN_TR"},
          {"code": 314, "name": "BTN_SELECT"},
          {"code": 315, "name": "BTN_START"},
          {"code": 316, "name": "BTN_MODE"}
        ]},
        {"type": 3, "abs": [
          {"code": 0, "name": "ABS_X", "min": 0, "max": 4095},
          {"code": 1, "name": "ABS_Y", "min": 0, "max": 4095},
          {"code": 2, "name": "ABS_Z", "min": 0, "max": 255},
          {"code": 3, "name": "ABS_RX", "min": 0, "max": 4095},
          {"code": 4, "name": "ABS_RY", "min": 0, "max": 4095},
          {"code": 5, "name": "ABS_RZ", "min": 0, "max": 255},
          {"code": 16, "name": "ABS_HAT0X", "min": -1, "max": 1},
          {"code": 17, "name": "ABS_HAT0Y", "min": -1, "max": 1}
        ]}
      ]
    }]
  }
}
```

The numeric identity is the already guest-visible `BUS_USB`, `045e:028e:0110`
identity. Symbolic names are diagnostic; raw numeric type/code/value fields are
the stable injection representation. The query and virtio-input configuration
will be generated from the same static capability table so they cannot drift.
If `pocketforge-input-device=off`, `devices` is empty rather than containing a
phantom device.

Major 1 fixes command names, required fields, numeric event meaning, framing,
validation, ordering and atomicity. A major increment is required to remove or
reinterpret any of those. A minor increment may add optional response fields,
new independently identified devices, or newly advertised capabilities. A
client accepts the major it implements and may ignore fields/capabilities added
by a newer minor; it must reject an unknown major. Removing a capability from an
existing device is not treated as a harmless minor change.

### 4.2 Raw batch grammar

The primary command is:

```json
{
  "execute": "pocketforge-input-send",
  "arguments": {
    "device": "gamepad",
    "events": [
      {"type": 1, "code": 304, "value": 1},
      {"type": 0, "code": 0, "value": 0}
    ]
  }
}
```

The grammar and behavior are:

1. `events` contains 1 through 256 raw evdev triples and ends in explicit
   `EV_SYN/SYN_REPORT/0`. Multiple non-empty reports may be batched; every report
   has its own terminating SYN. No other EV_SYN code or SYN value is accepted.
2. The implementation validates the entire batch before touching device state or
   guest queues. Every type and code must be present in the selected device's
   query result. EV_ABS values must be within that code's advertised inclusive
   range. EV_KEY accepts only 0 and 1, retains press/release state for all nine
   keys, and rejects duplicate presses and release-before-press. No release is
   synthesized. Hat zeroing is likewise explicit; arbitrary in-range stick,
   trigger and hat absolute values are otherwise valid.
3. Validation simulates state changes on a temporary copy. Thus an invalid final
   triple cannot leak a valid earlier report from the same batch. After complete
   validation, queue capacity for the complete batch is preflighted without
   consuming descriptors. Insufficient capacity delivers nothing.
4. Delivery preserves array order. Each sequence through a SYN_REPORT is queued
   and committed as one virtio-input report; no guest notification can expose a
   half-report. Since QMP commands execute on the QEMU main loop, no second
   producer can interleave between reports in one accepted batch.
5. Only after every report is committed is the temporary key/hat state installed
   as live state. Reset/unrealize clears it. The existing read-only `ready` and
   `held` properties remain; `held` covers pressed keys and non-zero hats, not
   centered/non-centered analog axes.

The real modern virtio-mmio queue reports 1024 entries to the qtest guest
driver. The ABI maximum is therefore `min(256, 1024) = 256`; it is bounded
below the capacity that can succeed at steady state rather than admitting a
permanently impossible request. The post-merge smoke found that the pinned
build-6 kernel has virtio core, virtio-mmio, virtio-input and virtio-gpu
disabled, so that artifact cannot negotiate the queue. See
`docs/pocketforge-a133-build6-smoke.txt` for the exact hashes and prerequisite.

The raw integer grammar is intentionally device-neutral. A future machine can
advertise EV_SW or touch/ABS_MT codes in another queried device and accept the
same command without an ABI change. This A133 machine advertises neither touch
nor EV_SW through this injection ABI, so attempts to inject either fail.

### 4.3 Typed failures

No new core QAPI `ErrorClass` values are added. Upstream treats that enum as a
legacy surface. Existing classes are used where they fit; all other failures
remain `GenericError` and begin `desc` with an ABI-stable token:

| QMP class / stable `desc` token | Conditions | Mutation/delivery |
| --- | --- | --- |
| `DeviceNotFound` | unknown device ID, or input device disabled | none |
| `DeviceNotActive` | virtio input has not reached `DRIVER_OK`, or was reset | none |
| `GenericError`, `pocketforge-input: reason=invalid-parameter index=<n>:` | empty/oversized batch, malformed framing, undeclared type/code, invalid key value/state, out-of-range ABS | none |
| `GenericError`, `pocketforge-input: reason=resource-busy:` | guest event queue cannot hold the validated batch | none |

The prefix through the reason token (and event index where present) is ABI 1.0.
pfvd branches on that token, not the human text after the next colon. Display
option/ownership failures use the same grammar with the `pocketforge-display:`
namespace and `invalid-parameter`, `resource-busy`, `duplicate-gpu`, or
`unsupported-format` reasons.

### 4.4 Control-name sugar and the shared path

The existing QOM property remains stable:

```json
{"execute":"qom-set","arguments":{"path":"/machine/pocketforge-input","property":"event","value":"dpad-up:press"}}
```

All current strings continue to work unchanged: `dpad-left`, `dpad-right`,
`dpad-up`, `dpad-down`, `l1`, `r1` and `menu`, each with `press` or `release`.
All other keys and analog controls use the primary raw batch ABI; v1 does not
add a second named-value grammar.

The property parser does not send events itself. It translates one named action
or value to raw triple(s), appends `EV_SYN/SYN_REPORT/0`, and invokes the same
batch validator/preflight/commit routine used by `pocketforge-input-send`.
Existing named-state strictness therefore remains, while there is only one
capability and queue-validation implementation.

### 4.5 Power, volume and switches

Power and volume do not belong to `TRIMUI Player1` and will not be added to its
capability bitmap:

- the owned A133 platform descriptor identifies volume-up/down as
  `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` on the separate `sunxi-keyboard` subsystem;
- the owned build-6 kernel's AXP717 MFD creates an `axp20x-pek` child, whose
  input driver advertises `KEY_POWER` on a separate input device;
- the current bounded QEMU AXP717 model intentionally produces no PMIC events
  and exposes no interrupt output, so it cannot honestly inject that power key;
- the board DT also has a separate hall/gpio switch path, but this v1 machine
  must not invent an EV_SW device.

Accordingly v1 queries only `gamepad`. Volume, power, hall and touch remain
unmodeled rather than being mislabeled as gamepad inputs. When their actual
guest-visible devices are modeled, they can appear as additional `devices`
entries in a later minor ABI and use the same raw triples.

Evidence is source-owned: `platform/devices/a133/capabilities.toml` at platform
commit `5c21b42520e82c45088778aba2088379a163696a` records the volume subsystem;
build-6 kernel commit
`c22dbc0226242cd1e582073eac5d82d36953c0d8` contains the AXP717 MFD/PEK input
path and board DT; and `pocketforge/a133-mmio-coverage.md` documents the current
PMIC model's no-event/no-IRQ boundary. No vendor BSP source was consulted.

## 5. Launcher inventory and migration

The machine now owns transport 0 and rejects any user-added `virtio-gpu*`
device during construction with
`pocketforge-display: reason=duplicate-gpu:`. The pre-implementation launcher
inventory is:

- qemu-tsp has no launcher that adds a GPU. `README.md:74-82` only documents
  outputs and direct-kernel media; its new launch documentation says the
  machine owns the GPU.
- pocketforge-automation `scripts/qemu-a133-virt.sh:112-121` adds
  `virtio-gpu-device` to generic `-M virt`, so it is unaffected.
  `scripts/qemu-pocketforge-a133-boot.sh:119` and
  `scripts/qemu-pocketforge-a133-ui.sh:54-62` launch this machine without an
  explicit GPU. `scripts/pf-qemu-a133-input:270-295` is likewise GPU-free and
  currently uses `-display none`; it should select
  `pocketforge-display=off` when deliberately retaining that headless lane.
- cloud-init-tsp PR #3 head `10aa8ae9` uses `-M virt` in
  `.github/ci/run-pocketforge-qemu.sh:9-13`; its harness at
  `.github/ci/pocketforge_qemu.py:605-641` adds no GPU and uses
  `-display none`, while `:711-715` currently rejects a non-virt machine. Its
  cifull lane must remove any future explicit GPU and choose the intended
  display profile before switching to `pocketforge-a133`.
- the sim repository is qemu-user-only
  (`harness/run-in-harness.sh:50-62`) and launches no system machine or GPU.

Those other repositories are follow-ups/Related PRs; this lane does not edit
their branches.

## 6. RED-first qtest plan

Phase 2 starts by adding the named tests and recording their failures against
the pre-implementation tree. The current expected RED reasons are unknown
machine property/QMP command and, without the configure delta, unavailable
`-display dbus`. No failing test patch will be committed to the final stack.

### 6.1 `pocketforge-a133-display-test`

`/a133/display/profile-dbus-bytes` will use the upstream qtest D-Bus socketpair
and generated listener skeleton. In one test invocation it will:

- start the default hardware profile, verify the machine owns exactly one GPU
  on transport 0, and read 720x1280 through virtio display-info and EDID;
- create a deterministic XR24 resource/scanout, register a D-Bus listener, and
  assert `Scanout` width, height, stride, pixman format, byte length
  (`stride * height`) and selected pixel offsets; the resource has a marker only
  in native top-left, and the test asserts its clockwise-90 presented corner so
  an incorrect 270-degree direction cannot pass;
- query `query-pocketforge-display` and assert its live dimensions/stride/fourcc
  describe those same received bytes, while native rotation fields remain
  plane 0/presentation 90;
- issue a deterministic partial damage and validate the matching `Update`
  rectangle, stride, format and bytes;
- run the negative control with `pocketforge-display=off`, proving transport 0
  is empty, `active_scanout` is absent and no listener frame arrives; and
- assert an unknown profile value fails machine construction.

The positive and negative controls are selected in the same Meson test process,
not inferred from a process error or a partial read.

### 6.2 `pocketforge-a133-input-test`

The existing input qtest will gain `/a133/input/raw-abi` and
`/a133/input/named-sugar-parity`. One Meson invocation will cover positive and
negative controls together:

- byte-compare the query identity/capabilities/ranges with virtio-input config;
- inject press and release for all nine keys; min/mid/max for all four sticks;
  min/mid/max for both triggers; and `-1/0/1` for both hat axes;
- inject multi-event chords and multiple SYN-framed reports and assert exact
  guest order and report boundaries;
- for every named-sugar mapping, compare guest events with the equivalent raw
  batch, including all previously supported strings;
- assert the `reason=invalid-parameter` token for an undeclared event type, undeclared key and ABS
  code, every ABS value just outside its range, bad EV_KEY values/state, missing
  or malformed SYN, an empty report and an oversized batch;
- put an invalid triple in the final report of a multi-report batch and prove
  that no earlier event, descriptor or state escaped;
- provide one fewer descriptor than a valid batch needs, assert the
  `reason=resource-busy` token, prove descriptors/state are unchanged, then add capacity and
  successfully retry the identical batch;
- assert `DeviceNotActive` before DRIVER_OK and after reset, and
  `DeviceNotFound` for an unknown ID and with
  `pocketforge-input-device=off`; and
- follow each failure on the same live QEMU instance with a valid positive
  injection, so an error message or partial read cannot masquerade as the
  negative control.

## 7. Real build-6 headless smoke

The smoke is a TCG-only, unprivileged CI run against the identity-pinned tuple
already recorded in `pocketforge/a133-mmio-coverage.json` and README:

- descriptor
  `ef573704f42e759b27bd3ba280aad0607d117cf278310b32ddbcb079992edba5/ImageSource.json`;
- raw SD SHA-256
  `c2e684bda333be6784fa47839776ba6b805ecdd70585cfe841f268f3c9f117ae`;
- kernel Image SHA-256
  `db8a54a5c850afb3d1655a3e8062bbff77315f08cef1c1563611d676761d715d`;
- DTB SHA-256
  `1a9042d839ee9d1548062efacc5dde332789c6dc846f21bb2c7877045d49b358`;
- initrd SHA-256
  `93504ef5954b7cda5fdeb16577353277a89ac8f12bb321cff227f059c54c9fe6`.

CI verifies the immutable sources, makes a bounded writable SD copy, extends
that copy to the QEMU-required 2 GiB, and uses the existing cloud-init
POCKETFORGE partition protocol to place a tiny guest-side evdev observer. This
is boot data, not a guest agent or a modified DT/initrd/kernel. The observer
finds the input by its queried name/ID, records binary input events, and emits
bounded serial markers.

QEMU starts with the real Image/DTB/initrd/SD, `-accel tcg`,
`pocketforge-display=hardware`, a QMP socket and `-display dbus,p2p=yes`. A
host-side GIO listener waits for a real guest scanout, validates its payload and
cross-checks `query-pocketforge-display`. After input `ready`, the host queries
ABI 1, sends one face key press/release, one stick position, one trigger value
and one hat transition through the raw command, and matches the guest's serial
records exactly. It verifies no synthesized release, asks the guest to flush
its evidence, then quits QEMU through QMP and waits for clean termination.

The smoke is added to the existing CI selection without raising any timeout.
It uses no KVM, render node, device, bench, privileged container, systemd or
host input node. If its measured step timing approaches an existing job limit,
that is a design blocker to report rather than a reason to increase the limit.

## 8. Phase 2 patch and validation sequence

After gpu-14 approval only:

1. Rebase the patch stack on current `origin/main`; report a conflict with the
   qsd work rather than editing another worker's branch.
2. Add and run the RED tests above, retaining the exact failing output as work
   evidence.
3. Append new numbered patches after 0014 for the machine-owned display/profile,
   display query, input query/raw batch command, shared validator and QAPI error
   tokens. Update `build.sh` in numeric order.
4. Make the named tests GREEN, run the focused qtests and build-6 smoke locally
   in disposable, host-UID containers, then push and rely on GitHub CI for the
   complete gate.
5. Expand `docs/pocketforge-a133-input.md` as the single normative contract and
   update README/build notes. Keep v1 explicitly copy-only.
6. Open the required PR without merging, verify jobs actually ran, execute the
   author review adapter on the exact green head, answer every thread, continue
   review as required, and run the merge script's dry-run before handback.

## 9. Approval points

gpu-14 approval of this design fixes these public names and semantics before
implementation:

- `pocketforge-display=hardware|off`, default `hardware`;
- `query-pocketforge-display`;
- `query-pocketforge-input` and ABI 1.0;
- `pocketforge-input-send` with raw, explicitly SYN-framed batches;
- existing QOM `event` strings retained through the shared validator; and
- `docs/pocketforge-a133-input.md` as the one stable documentation path.

gpu-14 approved the design with the four amendments recorded above before
Phase 2 implementation began.
