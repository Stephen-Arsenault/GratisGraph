# GTS Wire Format Specification

**Status:** Confirmed on hardware (Gravograph M20 Express, Sirius FW boot 1.2 / prog 1.30).
Multi-stroke header semantics confirmed 2026-06-09 (telemetry-verified to 0.01 mm).
**Scope:** The USB transport and binary job format used by Gravograph M20/M40-family
contact engraving machines. This document is the implementation contract for all
GratisGraph translators (Python reference implementation, planned TypeScript port).
If code and this document disagree, this document wins — fix the code.

> Findings here were obtained through interoperability research on hardware owned by
> the authors, by observing USB traffic between the machine and the vendor's software,
> and confirmed by independent implementation. No proprietary code is reproduced.
> Gravograph, Gravotech, Gravostyle, and GT Smartstream are trademarks of Gravotech
> Group; this project is not affiliated with or endorsed by Gravotech.

---

## 1. Conventions

- All multi-byte integers are **big-endian**.
- Coordinate unit: **0.01 mm** (100 units/mm). Confirmed by physical measurement.
- Coordinate system: home (0,0) = front-left of work area; +X = tool right;
  +Y = table moves backward (away from operator).
- Work envelope: 100×100 mm software, ~120×120 mm physical (M20).
- "GTS" is this project's name for the format; the vendor does not document one.
  File extension: `.m20` (per-machine extensions for future variants).

## 2. USB transport

| Property | Value |
|---|---|
| VID:PID | `0xFFFD:0x0001` (unregistered VID; product string "USB Printer") |
| Class | USB Printer Class (0x07), bidirectional |
| Speed | Full Speed (12 Mbit/s) |
| EP OUT | `0x01` bulk, 64-byte max packet (host → machine: jobs, commands) |
| EP IN | `0x82` bulk, 64-byte max packet (machine → host: telemetry, responses) |

Jobs are written to EP OUT as a raw byte stream — no envelope, no framing, no
acknowledgement handshake required before sending. (The ASCII `!PS/!OR/!MM` envelope
used on the Windows print path is consumed by the spooler stack and never
reaches the wire.)

OS note: macOS's AppleUSBPrinter claims the interface exclusively; detach requires
root. Hold the interface claim for the process lifetime — releasing it lets the
kext reattach immediately.

## 3. Device → host packets: counted framing

**Every machine response follows one framing rule** (confirmed live 2026-06-09):

```
!XX N;<N payload bytes>
```

ASCII prefix: `!`, two command characters, space, decimal byte count, `;`.
The count is the exact payload length. There are no delimiters between packets.

| Packet | Count | Payload | Meaning |
|---|---|---|---|
| `!ID 28;` | 28 | 7 × int32 BE | Identity (see §4) |
| `!MX 4;` `!MY 4;` | 4 | uint32 BE | Axis absolute position, 0.01 mm units |
| `!MZ 4;` | 4 | uint32 BE | Z axis activity — scaling DIFFERS from X/Y (values exceed physical travel at 0.01 mm; possibly 0.001 mm units or a counter). Do not interpret as 0.01 mm |
| `!MN 4;` | 4 | uint32 BE | Motor neutral / heartbeat counter |
| `!ME 4;` | 4 | uint32 BE | Motion end / encoder marker |
| `!JF 0;` | 0 | — | Job RECEIPT acknowledgement — emitted ~2s after a job is queued, BEFORE execution. NOT a completion signal (confirmed 2026-06-09) |
| `!ET 4;` | 4 | uint32 BE | End of motion. Plausibly engrave-job execution time in seconds: identical jobs reproduce the same value (9, 9); longer job → 13; wall-clock window consistently ~4 s larger (home job excluded?) |
| `!OR 8;` | 8 | 2× uint32 BE | Origin report: machine→host packed (X, Y) at 0.01mm, sent when the operator jogs the head and confirms a new origin on the panel (confirmed 2026-06-09) |
| `!TU 4;` etc. | 4 | uint32 BE | Command acknowledgements |

Position telemetry streams continuously during job execution in bursts.
Telemetry unit mapping confirmed: values are absolute position at 100 units/mm
(characterised Session 10 against known 90 mm travel).

A parser MUST read the count rather than assuming a fixed packet length —
`!JF 0;` carries zero payload, and assuming 4 bytes desynchronises the stream.
Some firmware revisions prepend a stray NUL byte; strip leading NULs.

## 4. Identity (`!ID`)

Host sends ASCII `!ID 0;\r\n`. Machine replies `!ID 28;` + 28-byte struct
(7 × int32 BE). The machine also broadcasts this packet spontaneously on USB
attach. Note: the machine reliably answers `!ID 0;` only shortly after power-on
(⚠ OPEN: idle-state behaviour unexplained).

| Offset | Field | M20 Express live value |
|---|---|---|
| +0 / +4 | boot_ver major/minor | 1.2 |
| +8 / +12 | prog_ver major/minor | 1.30 |
| +16 | platform_type | 4 (3 = K2000/M20 std, 4 = Sirius/M20 Express; firmware family, NOT product) |
| +20 | model_code | 0x0204 |
| +24 | model_code_alt | 0 (⚠ OPEN: possible serial-number field) |

**Model codes** (from firmware updater ID blobs): 0x0202 M40, 0x0203 M40G,
0x0204 M20/Express, 0x0205 M20 XL, 0x0206 M20 Jewel, 0x0207 M20 PIX,
0x0209 M20 Jewel 2, 0x020D M20 IQ, 0x020E M20 XL IQ, 0x020F M20 Jewel IQ,
0x0210 M40 IQ, 0x0211 M40G IQ, 0x0212 M10 Jewel (negate X).
IS400 IQ uses the same format with coordinates × 1.25. Implementations MUST key
machine behaviour on model_code, never platform_type.

## 5. GTS job binary

### 5.1 Overall structure

```
[94-byte header]
[setup commands: DR MC VS VZ PZ DZ]
[stroke blocks, simply concatenated — no records between them]
[12-byte return-to-home segment (delta)]
[JF]
```

### 5.2 Header (94 bytes)

| Offset | Bytes | Value |
|---|---|---|
| 0x00 | 6 | Magic `!MM88;` |
| 0x0A | 14 | `ot : 1\0` as UTF-16LE |
| 0x24 | 2 | `02 04` |
| 0x30 | 2 | `0x0DE4` BE (BOX_DIM = 3556 units) |
| 0x34 | 2 | `0x0DE4` BE (repeated) |
| 0x3D | 1 | `0x02` |
| 0x5D | 1 | `0x04` |
| (rest) | — | zero |

### 5.3 Opcode commands — 6 bytes each

```
FF FD <2-char ASCII opcode> <uint16 BE argument>
```

Setup block: `DR 0` (drag reset) · `MC` (SPINDLE/motor enable: 1=on, 0=off —
confirmed from Quick capture 2026-06-09, NOT "machine coords" as previously
documented) · `VS 25` (vector speed) · `VZ 25` (= VS) · `PZ` (ENGRAVING DEPTH
— Quick sets this from its depth field: depth 0.01"→PZ 25, 0.99"→PZ 100,
default→PZ 0; confirmed 2026-06-09) · `DZ 500` (drag depth).

**Spindle (MC):** Quick sends `MC 1` to spin the cutter, `MC 0` for drag/diamond
engraving with no spindle. Our driver always sent `MC 0` (correct for diamond
drag). To run a rotary cutter, send `MC 1`.

**Depth (PZ):** Quick actively drives PZ from the operator's depth field
(imperial inches on the captured install, UI-capped ~0.393"≈10mm). PZ 0→100
scale. This REOPENS the depth question: our "PZ has minimal effect" finding
came from sending PZ 30 with a diamond drag + spring tension; PZ may have real
effect with a spindle-driven cutter or across its full range. Untested on our
hardware. The ≈6 mm solenoid stroke (drag mode) finding still stands separately.

### 5.4 Segment records — 12 bytes, raw, NO `FF FD` prefix

```
int16 BE × 6:  pw1  pw2  speed  accel  dx  dy
```

`pw1 = pw2 = 1`; `speed` = VS value; `accel = 551` (0x0227) in every observed
record (⚠ OPEN: live parameter vs sentinel — untested). `dx, dy` are **relative
deltas** in 0.01 mm units. Parsers identify segment records by `accel == 551`.

The same 12-byte record serves as the **stroke header** (pen-up rapid to stroke
start) and as the **return-to-home** block before `JF`.

### 5.5 Stroke block

```
[12-byte stroke header — pen-up rapid DELTA from previous stroke's end
                         (first stroke: from home, so delta == absolute)]
[FF FD ZA 0001]      Z approach
[FF FD PD 0000]      pen down (operator confirmation gate when armed via !CM)
[N × 12-byte segments]
[FF FD PU 0000] × 2
[FF FD PU 0001]      pen up, home flag
```

Exactly **one** `JF` per job, after the final return segment. Emitting `JF`
per-stroke stops the machine after the first stroke. `NR` does not appear in
any captured job — do not emit.

### 5.6 Stroke-header coordinates — CONFIRMED RELATIVE (2026-06-09)

**Every coordinate on the wire is a relative delta — headers included.**
Confirmed by paired hardware runs with axis telemetry:

- Absolute-header emission with a separate inter-stroke rapid double-offset
  the second stroke: commanded start (30, 40) mm landed at (60, 80) mm
  (`!MY` peak 7999 = 79.99 mm).
- Delta-header emission with no inter-stroke rapid landed both strokes at
  their commanded positions (`!MY` peak 3999 = 39.99 mm), and marks on paper
  agreed.

Canonical multi-stroke form: each stroke's header is the delta from the
previous stroke's END to this stroke's start; stroke blocks are simply
concatenated; one return-to-home delta and one `JF` close the job. There is
no separate inter-stroke rapid record — the header IS the rapid. A parser
must integrate deltas to recover absolute positions.

## 6. Execution model (confirmed on hardware 2026-06-09)

- Jobs QUEUE on receipt — the machine beeps once per job and does NOT execute
  while connected. ~2 s after queueing it emits `!JF 0;` per job (receipt ack).
- **One green press executes the entire queue in order** (confirmed: a home
  job and an engrave job both ran on a single press). Earlier "one press per
  job" claims are wrong.
- The last executed sequence persists in the buffer — pressing green again
  re-runs it — until power cycle.
- Disconnecting triggers a ~15 s end-of-stream timeout: the machine re-beeps
  for queued jobs but does NOT execute them unless armed (below).
- **Auto-execution:** send `!CM 4;` + uint32 BE `1` (10 bytes:
  `21 43 4D 20 34 3B 00 00 00 01`) after the job, then disconnect; the machine
  executes after the ~15 s timeout, pausing at the first `PD` for green if
  present. `PD` is the operator confirmation gate in armed jobs.
- **Job completion detection:** axis telemetry returning to 0 plus `!MN`
  heartbeats. `!JF` is a receipt ack and MUST NOT be treated as completion.

## 7. Reference captures

Byte-level regression fixtures (real Gravostyle output, confirmed on hardware)
live in `tests/regression/`: `00013.SPL` (horizontal line, start (0, 16.56 mm),
Δx 35.40 mm) and `00018.SPL` (small curved stroke). Conformance = field-level
equality with these captures for setup values, stroke positions, segments, PU
sequence, return block, and JF count, ignoring header metadata bytes.

## 8. Probed but unconfirmed commands

Host-to-device ASCII probes observed to elicit a reaction, but with
uncharacterised semantics — recorded so nobody re-discovers them:
`!GA 4;\r\n` (queues a homing job, short beep) and `!DL 4;\r\n`
(beep; presumed download mode). Not used by the driver.

## 9. Open questions (consolidated)

1. **§5.6 stroke-header semantics** — the only item blocking multi-stroke
   conformance and the TypeScript port.
2. `accel=551`: live parameter or sentinel.
3. `model_code_alt` / machine serial number source.
4. `!ID 0;` unanswered while machine is long-idle.
