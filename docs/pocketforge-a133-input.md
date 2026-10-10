# PocketForge A133 display and input ABI

This is the stable reference for every PocketForge-specific `pocketforge-a133`
machine option and for the display and input QMP ABI. ABI examples use QMP's
JSON spelling; Linux input constants are the numeric evdev values advertised by
the query.

## Machine options and display ownership

`pocketforge-display=hardware|off` selects the display profile and defaults to
`hardware`. The hardware profile creates exactly one `virtio-gpu-device` on
modern virtio-mmio transport 0, enables EDID, and advertises the native
720x1280 mode. A launcher must not add another virtio GPU; machine construction
fails with `pocketforge-display: reason=duplicate-gpu:` if it does. `off` leaves
transport 0 empty. The profile cannot change after machine initialization.

`pocketforge-input-device=on|off` defaults to `on`. It places the deterministic
gamepad MCU source behind the real DT's RX-only DW APB UART3 (right half) and
UART4 (left half). It does not add a guest-visible device or DT node. The
unmodified release image's `pf-input-decode.service` consumes the MCU frames
and creates its normal `TRIMUI Player1` uinput node. With `off`, the source is
absent, `query-pocketforge-input` returns an empty `devices` list, and injection
returns `DeviceNotFound`.

The copy-only v1 display is enabled at build time with `-Dgio=enabled
-Ddbus_display=enabled` and at run time with `-display dbus,p2p=yes`. Upstream
QEMU D-Bus `Scanout` and `Update` listener calls carry copied pixel arrays.
DMA-BUF, OpenGL/GBM, audio, snapshots, and a guest agent are not ABI 1.0
features.

## Display query (ABI 1.0)

`query-pocketforge-display` returns panel metadata and, when present, the live
QEMU display surface:

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

`native_width` and `native_height` describe the portrait-native panel, never a
landscape convenience size. `plane_rotation` is the guest primary-plane value.
`presentation_rotation` is degrees clockwise that a host rotates the
native-order buffer to show it upright. Its value is 90: the owned build-6 DT at
kernel commit `c22dbc0226242cd1e582073eac5d82d36953c0d8`,
`arch/arm64/boot/dts/allwinner/sun50i-a133-pocketforge-tsp.dts:289-294`, states
that the portrait glass needs 90 degrees for upright landscape and that 270 is
upside down, then sets `rotation = <90>`. D-Bus bytes remain in native order;
the client applies this presentation rotation.

`active_scanout` is omitted when the display profile is off or no surface is
available. Its dimensions, stride, and fourcc are read from the active surface,
not repeated profile constants. The D-Bus listener's width, height, stride,
pixman format, and byte count must agree with it. The supported conversion is
XR24=`PIXMAN_x8r8g8b8`, XB24=`PIXMAN_x8b8g8r8`, and
AR24=`PIXMAN_a8r8g8b8`; another active format fails with the stable
`reason=unsupported-format` token.

## Input capability query (ABI 2.0)

`query-pocketforge-input` reports the batch limit and every injectible device:

```json
{
  "return": {
    "abi_version": {"major": 2, "minor": 0},
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
          {"code": 16, "name": "ABS_HAT0X", "min": -1, "max": 1,
           "fuzz": 0, "flat": 0},
          {"code": 17, "name": "ABS_HAT0Y", "min": -1, "max": 1,
           "fuzz": 0, "flat": 0}
        ]}
      ]
    }]
  }
}
```

`caps` is per device and is the exact allow-list accepted by
`pocketforge-input-send`, so clients can disable controls unavailable in a
tier. ABI 2.0 exposes the nine digital controls and the two hat axes. The guest
decoder's eventual uinput node also declares the four 12-bit stick axes and two
8-bit triggers, but QEMU does not advertise or accept those until the MCU
analog follow-up lands as an additive ABI 2 minor revision. All ABS entries
include `fuzz` and `flat`; both are zero for this device.

`ready` means both physical UART streams have been configured by the guest and
have accepted their initial centered/all-released frame. ABI major 2 fixes
command names, required fields, numeric evdev meaning, explicit framing,
validation, per-stream ordering, and atomic failure behavior. A major change
removes or reinterprets one of those rules. A minor change may add optional
response fields, a new independently identified device, or capabilities;
clients may ignore additions from a newer minor but must reject an unknown
major. Removing an existing capability is not a compatible minor change.

Each UART source has a bounded 1024-byte pending FIFO in addition to the real
model's 16-byte RX FIFO. A 256-triple maximum batch can contain at most 128
one-control reports on one side, producing exactly 128 eight-byte frames, so
every accepted maximum batch can eventually drain at steady state. Capacity is
preflighted for both sides before state or either FIFO changes.

## Raw input grammar

`pocketforge-input-send` is the primary injection form:

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

The `events` array contains 1 through 256 ordered `(type, code, value)` triples.
It consists of one or more non-empty reports, each terminated explicitly by
`EV_SYN/SYN_REPORT/0`; another SYN code/value, an empty report, or a missing
final SYN is invalid. Every type and code must appear in that device's query.
EV_ABS values must be inside the advertised inclusive range. EV_KEY accepts
only 0 and 1 and rejects duplicate press or release-before-press. Hat release
to zero is explicit. QEMU never synthesizes a release.

QEMU validates the whole array on temporary state, snapshots at most one
complete frame for each affected UART at every SYN_REPORT, and preflights both
bounded FIFOs. Any validation or capacity failure delivers no byte prefix and
changes no held state. Frames are strictly ordered within each UART stream and
each frame is complete. UART3 and UART4 are independent physical streams, so
ABI 2.0 makes no guest-observable cross-stream ordering promise when one batch
touches both. QMP execution on the main loop prevents another producer from
interleaving a frame.

The numeric form is device-neutral. A future machine can advertise touch or
EV_SW without changing this command, but this machine advertises neither and
rejects both.

## Failure contract

ABI 2.0 adds no core QAPI `ErrorClass` values. Unknown/disabled devices return
`DeviceNotFound`, and an input device whose two UARTs are not ready returns
`DeviceNotActive`. Other errors use `GenericError` with one stable token at the
start of `desc`:

```text
pocketforge-input: reason=invalid-parameter index=<decimal>: <human text>
pocketforge-input: reason=resource-busy: <human text>
pocketforge-display: reason=invalid-parameter: <human text>
pocketforge-display: reason=resource-busy: <human text>
pocketforge-display: reason=duplicate-gpu: <human text>
pocketforge-display: reason=unsupported-format: <human text>
```

Clients branch on the class where specified and otherwise on the prefix through
the reason token; text after the next colon is diagnostic and may change.

## Retained control-name sugar

The existing QOM path remains supported:

```json
{"execute":"qom-set","arguments":{"path":"/machine/pocketforge-input","property":"event","value":"dpad-up:press"}}
```

Its exact grammar is `<control>:<press|release>`. Controls are `dpad-left`,
`dpad-right`, `dpad-up`, `dpad-down`, `l1`, `r1`, and `menu`. The parser maps
one string to its raw EV_ABS/EV_KEY triple plus SYN_REPORT, then calls the same
validator, capacity preflight, and commit routine as the raw command. The
read-only `held` property covers pressed keys and non-zero hats. Reset clears
held state and pending frames; automation must still send explicit releases
for accepted presses.

## Power, volume, touch, and switches

Power and volume are not gamepad capabilities. The owned A133 platform
descriptor at commit `5c21b42520e82c45088778aba2088379a163696a` assigns
`KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` to the separate `sunxi-keyboard` input. The
exact build-6 DT leaves its LRADC disabled, so no runtime enablement is assumed.
The owned build-6 kernel creates an `axp20x-pek` child for `KEY_POWER`; the
bounded QEMU AXP717 model intentionally generates no PMIC event or IRQ until
the dedicated PMIC follow-up. Touch and the hall/gpio switch are also
unmodeled. ABI 2.0 therefore exposes only `gamepad` and does not invent power,
volume, touch, or EV_SW on that device.
