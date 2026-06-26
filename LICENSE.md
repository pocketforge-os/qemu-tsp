# Licensing

`pocketforge/0001-linux-user-evdev-uinput-ioctl-passthrough.patch` modifies
`linux-user/syscall.c` from QEMU, which is **GPL-2.0-or-later**. The patch is therefore a
derivative work under the same license (GPL-2.0-or-later), consistent with upstream QEMU.

The PocketForge tooling in this repo (`build.sh`, `regression/run-regression.sh`,
`regression/probe.c`, `regression/mkuinput.c`, docs) is original PocketForge work and may
be used under the same GPL-2.0-or-later terms for simplicity.

Upstream QEMU source is NOT vendored here; it is fetched at the pinned ref in `UPSTREAM`.
