# GTS Protocol Findings
**Project:** GratisGraph — open-source Python driver for Gravograph M20 engraving machines
**Licence:** GPL v3
**Target:** M20 machine USB protocol & binary format
**Vendor driver (Windows):** GT Smartstream 4.06 (Gravograph)
**Last updated:** Session 11 — execution model confirmed, counted-packet framing, !JF semantics
**Status:** 🟢 Protocol fully confirmed — G-Code → GTS binary → USB working end-to-end

> **Note:** the normative wire-format reference is now [`GTS_FORMAT.md`](GTS_FORMAT.md).
> This document is the research log: how findings were made, what changed, and what
> remains open. Where the two disagree, GTS_FORMAT.md wins.

---

## Legal Notice

This document describes findings from interoperability research conducted on hardware owned by the authors, pursuant to the right to study and observe software under normal use, and for the purpose of developing an independent open-source driver. No proprietary source code was used or reproduced. Gravograph, Gravotech, Gravostyle, and GT Smartstream are trademarks of Gravotech Group. This project is not affiliated with or endorsed by Gravotech.

---

## Naming Note

"GT Smartstream" is the name of the **Windows driver package** (Language Monitor + OEM UNIDRV plugin), not the name of the underlying machine protocol. The machine protocol has no publicly documented name. Within this project we use **GTS** as a shorthand for the binary format, and **`.m20`** as the file extension for M20-targeted binaries. Future machine variants (M40 etc.) would use their own extension (`.m40`) while sharing the same GTS format family.

---

## USB Device

- **VID:PID:** `0xFFFD:0x0001` — confirmed from live USB enumeration
- **Product string:** `USB Printer`
- **USB Class:** Printer Class (bInterfaceClass=0x07), Bidirectional protocol
- **Endpoints:** EP 0x01 Bulk OUT (host→machine), EP 0x82 Bulk IN (machine→host). Both 64-byte max packet.
- **USB Speed:** Full Speed (12 Mbit/s)
- **macOS:** AppleUSBPrinter DriverKit extension claims interface 0 exclusively. `detach_kernel_driver()` requires `sudo`. Do **not** call `usb.util.release_interface()` — hold the claim for the process lifetime. Releasing immediately allows the extension to reattach.

---

## Execution Model (Confirmed on Hardware, 2026-06-09)

This supersedes earlier, partially contradictory notes ("connected workflow executes
without green" and "two green presses" were both **wrong**).

- Jobs **queue** on receipt — the machine beeps once per job and does **not**
  execute while connected.
- ~2 s after queueing, the machine emits **`!JF 0;` per job** on EP IN. This is a
  **receipt acknowledgement**, *not* a completion signal — it arrives before any
  execution. (An earlier driver build treated it as completion and disconnected
  with the job unexecuted.)
- **One green press executes the entire queue in order** — a home job and an
  engrave job both ran on a single press.
- The last executed sequence persists in the buffer; pressing green again re-runs
  it. Cleared by power cycle.
- Disconnecting triggers a ~15 s end-of-stream timeout: the machine **re-beeps**
  for queued jobs but does **not** execute them unless armed (below).
- **Auto-execution:** `!CM 4;\x00\x00\x00\x01` (10 bytes) sent after a job arms it
  to execute after the disconnect timeout, pausing at the first `PD` for a green
  press if one is present. `PD` is the deliberate operator confirmation gate.
- **Job completion detection:** axis telemetry returning to 0 plus `!MN`
  heartbeats. Never `!JF`.
- `!ID 0;` only responds shortly after power-on, not while long-term idle
  (mechanism unknown — open question).

---

## Device → Host Packet Framing (Confirmed 2026-06-09)

Every machine response uses **counted framing**: `!XX N;` + exactly N payload
bytes, where N is the ASCII decimal before the `;`. Confirmed live:
`!ID 28;`+28B, `!MX 4;`+4B, `!JF 0;`+0B. A parser that assumes a fixed length
desynchronises on `!JF 0;`. Some firmware revisions prepend a stray NUL byte.

Axis telemetry streams continuously during execution on EP IN:
`!MX/!MY/!MZ` (absolute position, **0.01 mm units** — characterised Session 10
against known 90 mm travel), `!MN` (heartbeat counter), `!ME` (motion
end/encoder marker). Packets arrive in bursts. Real-time position tracking and
stall detection are feasible features.

---

## Coordinate System (Confirmed from Hardware)

- **Home (0,0) = back-left corner** of work area (nearest the gantry, farthest from the operator; top-left from the operator's view)
- **+X = mark moves right**
- **+Y = mark moves toward the operator** (the table translates away from the operator; confirmed with a felt-tip marker fiducial — supersedes the earlier provisional "front-left, +Y to back" reading)
- **1 unit = 0.01mm** (`UNITS_PER_MM = 100`) — confirmed from hardware: 20mm square measured 20mm, 90mm wordmark measured 90mm
- **Software work envelope:** 100 × 100mm
- **Physical envelope:** ~120 × 120mm (conservative safe limit)

**LightBurn / Y-up G-Code:** Pass `flip_y=True` to `gts_prepare.from_gcode()` or use `--flip-y` on the CLI. Applies: `y_machine = envelope_height_mm - y_source`.

---

## GTS Binary Format

Full normative specification: [`GTS_FORMAT.md`](GTS_FORMAT.md). Research notes:

- 94-byte header (`!MM88;` magic), setup block `DR MC VS VZ PZ DZ`, stroke
  blocks (`hdr / ZA / PD / segments / PU×3`), final return-to-home, **one** `JF`.
- Segment records: 12 raw bytes `>hhhhhh` = pw1, pw2, speed, accel, dx, dy;
  `accel=551` in every observed record. dx/dy are relative deltas.
- `DZ`/`PZ` have minimal effect on actual Z travel — depth is set by the physical
  spring-tension knob; the solenoid pulls a fixed distance: **≈ 6 mm stroke,
  measured 2026-06-09**. No force/pressure value has been confirmed in the
  protocol. Practical consequence: the tool tip must rest within ~6 mm of the
  work surface at pen-up (tool seating / work height is the adjustment, not
  protocol values).
- Per-job `!JF` receipt acks confirmed 1:1 on hardware: two jobs sent → two
  beeps → two `!JF 0;` acks (2026-06-09).
- `NR` is **not present in any captured job** — it was an early mistaken
  assumption, since ruled out against real captures. Do not emit.
- Emitting `JF` per stroke stops the machine after the first stroke.
- The ASCII `!PS/!OR/!MM` envelope is a Windows print-path artefact consumed by
  the spooler stack — it never reaches the wire on the direct USB path.

### Stroke-header semantics — RESOLVED: RELATIVE (2026-06-09)

Confirmed by paired runs with felt-tip marking AND axis telemetry: absolute
headers + separate inter-stroke rapid double-offset stroke 2 to (60,80) mm
(`!MY` peak 7999); delta headers with no inter-stroke rapid landed both
strokes at commanded positions (`!MY` peak 3999 = 39.99 mm). Every wire
coordinate is a relative delta. `build_gts` emits the proven format
(byte-locked by regression test); `gts_preview` integrates deltas. The
earlier "inter-stroke rapid" reading of the wire layout was a
misinterpretation of the double-emission bug's own output. Telemetry doubles
as a 0.01 mm position-verification instrument — better than a ruler.

Newly observed during these runs: `!ET 4;` + uint32 at end of motion
(13 for the longer job, 9 for the shorter — plausibly execution seconds;
semantics provisional).

---

## !ID Identity Response

Reply to `!ID 0;\r\n`: `!ID 28;` + 28 bytes = 7 × big-endian int32:
boot_ver (major, minor), prog_ver (major, minor), platform_type, model_code,
model_code_alt. Live M20 Express: boot 1.2, prog 1.30, platform 4, model 0x0204,
alt 0.

**Platform types:** 3 = K2000/M20 standard, 4 = Sirius/M20 Express. Platform
type is a **firmware-family** identifier, not a product identifier —
implementations must key machine behaviour on **model_code**. (An early driver
build keyed profile selection on platform_type; profile auto-selection silently
never matched. Fixed 2026-06-09.)

---

## Model Code Table

| Code | Machine | Profile |
|------|---------|---------|
| `0x0204` | M20 / M20 Express | M20_PROFILE — **live confirmed** |
| `0x0205` | M20 XL | M20_PROFILE |
| `0x0206` | M20 Jewel | M20_PROFILE |
| `0x0207` | M20 PIX | M20_PROFILE |
| `0x0209` | M20 Jewel 2 | M20_PROFILE |
| `0x020D` | M20 IQ | M20_PROFILE |
| `0x020E` | M20 XL IQ | M20_PROFILE |
| `0x020F` | M20 Jewel IQ | M20_PROFILE |
| `0x0202` | M40 | M40_PROFILE |
| `0x0203` | M40G | M40_PROFILE |
| `0x0210` | M40 IQ | M40_PROFILE |
| `0x0211` | M40G IQ | M40_PROFILE |
| `0x0212` | M10 Jewel | M10_JEWEL_PROFILE (negate_x) |

---

## Machine Compatibility

### Confirmed Compatible (same GTS format)
All use `MAC_BIN_Gii_2d.DLL`, `ModeBidi=1`, `XyzMul=400,-400,-400`:
M20, M20 XL, M20 IQ, M20 XL IQ, M20 PIX, M20 Jewel, M20 Jewel 2, M20 Jewel IQ, M40, M40G, M40 IQ, M40G IQ

### Conditional
| Machine | Difference | Profile |
|---------|------------|---------|
| M10 Jewel | `XyzMul=-400` — negate X | `M10_JEWEL_PROFILE` |
| IS400 IQ | `XyzMul=500` — scale × 1.25 | `IS400_IQ_PROFILE` |

Both read from the published GT Smartstream INF/GPD files, never confirmed on live hardware.

### Not Compatible
| Machine | Reason |
|---------|--------|
| IS200/IS400 (binary) | No GT Smartstream bidi support |
| IS6000/7000/8000 | 3D CNC driver, different protocol |
| IM4E | RS-232 serial only |
| All laser machines | Different driver stack entirely |

---

## Firmware

Both platform firmware files use `!VP N;` framing for upload. Firmware body is encrypted via a hardware OTP key — its contents cannot be read without physical chip access. Not required for the primary project goal.

**Sirius mainboard confirmed from photo (JDE 49726/F2600-C, 2026-06-09):**
CPU is a **Freescale ColdFire MCF5272VM66** (66 MHz) with a silkscreened
26-pin BDM header (bottom-right). The earlier H8S/2600 reference was a
misread of the Micron MT48LC2M32B9 SDRAM beside it. Motion engine is an
Altera **Cyclone II FPGA** (own JTAG header); per-axis drive is 3× ST
**L6258EX** PWM stepper drivers (Axe X/Y/Z). Consequence for any future
firmware study: a BDM dump yields ColdFire code, but real-time motion logic
likely lives in the FPGA bitstream — a much harder target and the true
comprehension bottleneck.

| Platform | Boot | Prog |
|----------|------|------|
| K2000 (M20 std) | v1.1 | v2.46 |
| Sirius (M20 Express) | v1.2 | v1.30 |
| IM4E | v1.2 | v2.19 |

---

## Gravograph Quick Capture Findings (2026-06-09)

Captured Quick driving the M20 through the RP2040 sniffer (11 captures,
data/quick_capture/). Headline: **Quick's wire format IS GTS exactly as documented here** — `!MM88;` header, the `0x0204`/`0x0DE4` header
constants, `FF FD` opcodes, and 12-byte segment records with pw1=pw2=1,
speed, accel=0x0227(551), dx/dy — byte-for-byte identical to our independent
implementation. Independent confirmation of the entire format.

Corrections and new facts from vendor ground truth:
- **`MC` is the SPINDLE enable, not "machine coordinates."** `MC 1`=spindle on,
  `MC 0`=off. Isolated by diffing spindle-on vs spindle-off job captures: the
  single changed byte was MC's argument. Our driver always sent MC 0 (correct
  for diamond drag; we never had spindle control).
- **`PZ` is ENGRAVING DEPTH**, driven by Quick's depth field: 0.01"→PZ 25,
  0.99"→PZ 100, default→PZ 0. Reopens our "PZ minimal effect" finding (which
  was measured with diamond drag + spring tension at PZ 30).
- **Material selection (alu vs steel) changes toolpath pass count, NOT any
  wire-level depth/force parameter** — identical setup blocks, different
  segment counts.
- **Quick's identification = our `!ID 0;`**, sent 3× with `!1B 0;` interleaved
  (new opcode, likely reset/init probe). Confirms our handshake is correct.
- Depth field is imperial inches on this install, UI-capped at ~0.393"≈10mm.

- **Machine→host origin report decoded (`11_set_center_xy`).** Jogging the head
  and confirming on the panel makes the M20 send `!MX`/`!MY` (live position,
  0.01mm) then **`!OR 8;`** carrying packed (X, Y) = the confirmed origin
  (captured: X=5002=50.02mm, Y=4887=48.87mm). `!OR` is a NEW opcode and the
  first machine-initiated response beyond passive telemetry. NOTE: this traffic
  is on EP2 (0x82 IN); the summariser was initially filtering EP1 only and
  missed it — fixed to keep both EP1 (OUT jobs) and EP2 (IN telemetry).

Open from these captures: the `13` (PZ 100 / 0.99") capture is anomalously
large (~500KB job) — not yet explained; `!1B` semantics; whether PZ has real
Z effect on our hardware across its range.

## Resolved Questions (formerly open)

1. **Axis telemetry units** — resolved Session 10: absolute position, 0.01 mm
   units (100 units/mm), characterised against known 90 mm travel.
2. **Motion constants for duration estimation** — resolved Session 10: cruise
   velocity linear in VS at ~0.995 mm/s per VS unit; rapids ≈ engrave speed;
   implemented in `gts_optimise.estimate_duration()`.
3. **Execution model / green-press semantics** — resolved Session 11 (above).
4. **`!JF` meaning** — resolved Session 11: per-job receipt acknowledgement.

## Open Questions

1. **`!ID 0;` idle behaviour** — machine only responds shortly after power-on.
3. **Machine serial number — RESOLVED (negative), 2026-06-09.** The M20
   Express exposes NO serial over USB. Quick captures show: device-descriptor
   iSerialNumber index = 0 (no serial string exists), only string descriptor is
   "USB Printer", IEEE-1284 device ID (0xA1) returns empty MFG:;CMD:;MDL:;, and
   !ID model_code_alt = 0. Quick's licensing keys on something other than a
   machine serial.
4. **`accel=551` field** — constant in all captures. Live parameter or
   sentinel? Test by sweeping values on identical segments.
4. **`!ET` packet semantics** — execution-time-in-seconds hypothesis
   strengthened: two byte-identical jobs both reported 9; a longer job
   reported 13; reproducible. Across all three runs the wall-clock motion
   window exceeds the value by a consistent ~4 s — possibly the home job's
   execution excluded, i.e. !ET may report the engrave job only.
4b. **`!MZ` telemetry scaling** — Z values (12 idle, 1295–2262 during
   pen-down dips) are incompatible with the 0.01 mm X/Y scale (22.6 mm >
   the ~6 mm solenoid stroke). Consistent with 0.001 mm units (2.26 mm ≈
   the shimmed tip-to-work gap) or a non-position counter. Do not treat
   !MZ as 0.01 mm position.
5. **`DZ` depth value** — "minimal effect" is from normal-range observation,
   not a systematic sweep; a `DZ 50` vs `DZ 5000` comparison has never been run.
