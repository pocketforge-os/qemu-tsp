# PocketForge A133 virtual input

`-M pocketforge-a133` owns a virtual input device for deterministic launcher and
Poolsuite navigation without a physical controller. The model is attached to the
machine's second modern virtio-mmio transport. The first transport remains available
for `virtio-gpu-device`.

The guest sees the compatibility identity used by the owned A133 input decoder:

- name: `TRIMUI Player1`
- input ID: `BUS_USB`, vendor `045e`, product `028e`, version `0110`
- keys: `BTN_SOUTH`, `BTN_EAST`, `BTN_NORTH`, `BTN_WEST`, `BTN_TL`, `BTN_TR`,
  `BTN_SELECT`, `BTN_START`, and `BTN_MODE`
- axes: `ABS_X/Y/RX/RY` (`0..4095`), `ABS_Z/RZ` (`0..255`), and
  `ABS_HAT0X/Y` (`-1..1`)

This is an owned compatibility identity, not imported vendor code. It lets the real
PocketForge input broker match and validate the device. This bounded injection slice
controls the D-pad, the two digital bumpers, and the chassis Menu/Guide button.

## QMP injection ABI

Wait until the device reports ready:

```json
{"execute":"qom-get","arguments":{"path":"/machine/pocketforge-input","property":"ready"}}
```

Then write one transition per `qom-set` request:

```json
{"execute":"qom-set","arguments":{"path":"/machine/pocketforge-input","property":"event","value":"dpad-up:press"}}
{"execute":"qom-set","arguments":{"path":"/machine/pocketforge-input","property":"event","value":"dpad-up:release"}}
```

The exact value grammar is `<control>:<action>`:

| Control | Press event | Release event |
| --- | --- | --- |
| `dpad-left` | `EV_ABS ABS_HAT0X -1` | `EV_ABS ABS_HAT0X 0` |
| `dpad-right` | `EV_ABS ABS_HAT0X +1` | `EV_ABS ABS_HAT0X 0` |
| `dpad-up` | `EV_ABS ABS_HAT0Y -1` | `EV_ABS ABS_HAT0Y 0` |
| `dpad-down` | `EV_ABS ABS_HAT0Y +1` | `EV_ABS ABS_HAT0Y 0` |
| `l1` | `EV_KEY BTN_TL 1` | `EV_KEY BTN_TL 0` |
| `r1` | `EV_KEY BTN_TR 1` | `EV_KEY BTN_TR 0` |
| `menu` | `EV_KEY BTN_MODE 1` | `EV_KEY BTN_MODE 0` |

Every accepted transition is followed immediately by `EV_SYN SYN_REPORT 0`.
Successful QMP command order is guest event order. The model does not synthesize a
release: every press requires its matching release.

Empty, oversized, malformed, or unsupported values fail. A duplicate press,
release-before-press, or opposite direction on an already-held hat axis also fails.
Injection before the guest sets virtio `DRIVER_OK`, after reset until it is ready again,
or without enough guest event buffers fails without changing device state.

The read-only `held` QOM property is true while any injected control lacks a matching
release. Automation must release all successfully pressed controls and verify
`held == false` during cleanup. Device reset and unrealize clear this per-process state.
The model owns no host file descriptor, input device, chardev, daemon, or other shared
mutable resource.

For a no-device negative control, start QEMU with:

```text
-M pocketforge-a133,pocketforge-input=off
```

The virtio-mmio slot then reports no device and `/machine/pocketforge-input` does not
exist.

## Automation handoff

The follow-on `pocketforge-automation` worker must update its pinned qemu-tsp artifact,
connect QMP, and stop adding `virtio-keyboard-device`: the machine owns input slot 1.
It should wait for `ready`, send the exact transitions above, release in failure cleanup,
verify `held == false`, issue `quit`, and wait for the QEMU process.

Launcher assertions should cover D-pad focus movement, `BTN_TL`/`BTN_TR` room movement,
and the `BTN_MODE` Menu/SafeReturn path. Poolsuite should assert a D-pad focus/selection
transition. Controls must include no device, unknown input, missing release, wrong order,
an empty stream, and a valid injection whose expected UI/state transition deliberately
does not occur. Guest evidence must read the evdev identity/capabilities and ordered
`input_event` records; replaying a host fixture is not equivalent.
