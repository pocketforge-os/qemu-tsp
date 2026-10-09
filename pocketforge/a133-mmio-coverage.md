# PocketForge A133 pinned-DTB MMIO coverage design

This is the design and review contract for `tsp-fb710d5aec2820c20246.2`.
The machine must boot the profile-selected DTB without hiding an incomplete
system-memory map behind a permissive catch-all region.

## Pinned source and red baseline

The inventory is derived from
`sun50i-a133-pocketforge-odyssey.dtb`, selected by the A133 open profile at
platform commit `a57d2b92f22f1b26aadc33b60a534f8f91e796ae` and built from
kernel commit `6ccb87902144babc2838b03840c0413bf0941ab4`.  The exact locally
built DTB has SHA-256
`8d7d185c1a1f78f72861cf8a98577c38a60b500291a2a9b49e42bcc1a54e0440`.
The fidelity workflow deliberately republishes that artifact under the legacy
receipt basename `sun50i-a133-pocketforge-tsp.dtb`; the selected source is
still Odyssey.

The QEMU side is based on upstream commit
`11aa0b1ff115b86160c4d37e7c37e6a6b13b77ea`.  Before this work, the exact
applied patch stack was inspected through the running machine with HMP
`info mtree -f`.  That behavioral map lacked many enabled DT resources.  The
preserved red result is the reference fidelity run 36341301460, job
108681805081: all three positive cases reached the unmodified DTB and aborted
in `sun8i_ce_probe+0x35c` on `sun8i-ce 1904000.crypto`, at the uncovered CE
base `0x01904000` (DT extent `0x800`).

## Inventory derivation

`a133-mmio-coverage.json` is the machine-readable contract.  Its `reg_inventory`
is exhaustive for every `reg` tuple in the pinned DTB, including non-MMIO
identifiers and disabled nodes so that a changed status cannot silently change
the coverage set.

The derivation rules are:

1. A missing `status` means enabled.  Any non-`ok`/`okay` status on a node or
   ancestor disables the tuple.
2. A node's `reg` is decoded with its parent's effective `#address-cells` and
   `#size-cells`.  Cell defaults are inherited exactly as specified by DT.
3. Each address tuple is translated through every ancestor `ranges`.  Empty
   `ranges` is identity.  Non-empty `ranges` may contain multiple windows and
   the whole tuple must fit one window.  Missing or malformed translation is a
   hard error for an address resource.
4. A parent with `#size-cells = <0>` makes child `reg` values identifiers, not
   MMIO.  They remain in the inventory but require no QEMU region.
5. The SID calibration and speed-grade children are parent-local offsets: the
   SID provider has no `ranges`, so those tuples are recorded as `local` and
   must fit the enabled SID aperture; they are not independent CPU addresses.
6. Nested address resources may overlap their container by construction.  The
   SRAM section nodes and DE2 child resources are recorded independently, but
   one exact container region may cover them.

The root uses two address and two size cells.  `/soc` uses one of each and an
identity window from `0` through `0x3fffffff`.  The system-control bus has
empty identity `ranges`.  The DE2 bus maps child `0x0..0x3fffff` to
`0x06000000..0x063fffff`.  The inventory therefore contains 50 enabled
system-address tuples, two SID-local tuples, all enabled identifiers, and every
disabled address tuple.

RAM is not a source-DTB `reg` tuple: the machine maps its configured RAM at
`0x40000000`; the default is 1 GiB.  The machine also has exactly four
Cortex-A53 CPUs with IDs 0 through 3.  Both facts are explicit in the manifest.

## Coverage contract

The gate starts the built `qemu-system-aarch64` and obtains the live flat
system-memory view with `info mtree -f`; source constants and comments are not
accepted as map evidence.  It then enforces all of the following:

- every enabled translated DT address tuple is fully covered by the union of
  DT-backed QEMU regions;
- every DT-backed QEMU region is wholly contained in at least one enabled DT
  address tuple (no oversized or catch-all mapping);
- actual system-memory regions do not overlap;
- the live DT-backed, synthetic virtio, and RAM regions match the manifest at
  their exact bases and extents;
- the DTB hash and source pins match the manifest and workflow pins;
- malformed cells, truncated tuples, ambiguous/missing translations, zero
  sizes, stale pins, uncovered or partially covered resources, overbroad
  regions, overlap, status/ancestor changes, translated-range changes, and
  multi-range changes all fail closed.

The two QEMU-only virtio-mmio transports at `0x0a000000` and `0x0a000200` are
typed `synthetic`; they are intentionally not used to cover real-DT ranges.

## Model and stub decisions

The GICv2, four DesignWare APB UARTs, RTC, R-I2C0, AXP717 PMIC, virtio
transports, Cortex-A53 CPUs, and RAM use functional or bounded models. The
UART aperture is expanded to the DT-exact
`0x400`, and enabled UART0/1/3/4 are instantiated with their DT SPI lines.
QEMU's generic GIC model exposes only `0x1000` at the first half of the DT's
`0x03024000/0x2000` virtualization-interface tuple, so an exact
`0x03025000/0x1000` residual stub covers only the missing half.

R-I2C0 uses upstream QEMU's `allwinner.i2c-sun6i` register model at
`0x07081400` with SPI 113. Its defined register bank is `0x24` bytes; only the
register-free `0x3dc`-byte tail of the DT's `0x400` reservation remains an
inert residual stub. The AXP717 at address `0x34` models only IRQ enable
`0x40..0x44`, W1C IRQ status `0x48..0x4c`, regulator enable `0x80`, and
regulator controls `0x83..0x9f`. Unmodelled PMIC registers read zero and
ignore writes. The stored values are guest-programmed state, not hardware
measurements; the model produces no PMIC events or interrupt output.

All other enabled resources use uniquely named, exact-size RAM-backed
register stubs. They reset to zero and provide byte-addressable read-after-
write storage. This is deliberately an inert probe surface, not a claim of
device functionality. The driver audit is:

- `mmio-sram` requires storage semantics; exact RAM-backed apertures are the
  appropriate model.
- The open PowerVR driver is disabled in the pinned kernel configuration, so
  the GPU aperture has no active driver semantics.
- Cedrus maps the video engine at probe but performs hardware work only for a
  decode job; functional decode is outside this boot-fidelity scope.
- `sun8i-ce` reads `CE_CTR` during probe but does not issue or poll a crypto job
  there.  Zero-backed storage removes the observed abort.  Functional crypto
  requests remain intentionally unsupported.
- `sunxi-mmc` has bounded reset/status timeouts.  Storage permits safe probe
  failure without pretending that removable media exists.
- LEDC, DSI/DPHY, display, clocks, DMA, SID, thermal, pinctrl, watchdog, IOMMU,
  USB, IR, and power-domain probe paths use ordinary register reads and
  writes or bounded timeouts; none requires an unbounded hardware completion
  transition for the accepted boot path.
- The audio codec and IR drivers are modules and are absent from the fidelity
  initramfs.  Their exact apertures are still present because the enabled DT
  advertises them.

Disabled MMC2, UART2, I2C0-3, Ethernet, EHCI0/OHCI0, R-UART, and R-I2C1 are
not mapped.  In particular, the prior EHCI0/OHCI0 stubs are removed rather
than allowed to violate effective-status coverage.

The QEMU qtest validates every final aperture from the running machine's
flat map, tests reset and read-after-write storage at both ends of every new
stub, checks the exact UART/model boundaries, exercises R-I2C0 reset/status,
and keeps AXP717 register positives and an unpopulated-address NACK in one
invocation. It also proves disabled resources are absent. The automation
mutation suite independently exercises every
fail-closed category named above.
