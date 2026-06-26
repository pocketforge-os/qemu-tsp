# Regression baselines

`out.native.txt` — the native x86_64 probe of the host-synthesized "TRIMUI Player1"
(045e:028e) uinput device (from the original 2026-06-26 qemu-evdev spike). This is the
GROUND TRUTH the qemu-tsp arm64 probe must match byte-for-byte.

`../out.qemu-tsp.txt` — the arm64 probe of the SAME device run under qemu-tsp. Acceptance:
`diff baseline/out.native.txt ../out.qemu-tsp.txt` is EMPTY. Proven 2026-06-26 (tsp-an4.1).
