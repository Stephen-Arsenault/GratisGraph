# Telemetry

**Status:** SUPERSEDED 2026-06-09 - GratisGraph collects no automated telemetry, by design (rationale below).

---

> **SUPERSEDED — DECIDED 2026-06-09: GratisGraph collects no automated
> telemetry, by design.** Rationale recorded in TODO.md: posture dissonance
> with the project's clean interoperability stance, a permanent
> infrastructure/privacy tax on a zero-server architecture, and it would
> muddy gts_server.py's cleanest security claim ("binds loopback, talks to
> nothing"). The consent-by-action channel is `gts_capture.py`; the Step 4
> web UI will add a "Report your machine" button that pre-fills a GitHub
> issue with the capture report shown before sending. The sections below are
> retained for historical context only.

## Concept

Lightweight, opt-in, privacy-respecting usage telemetry to measure real-world adoption of GratisGraph across the GT Smartstream machine family.

---

## What to Collect

Minimum viable payload per event:

```json
{
  "event":        "job_sent",
  "version":      "0.1.0",
  "platform":     "darwin",
  "model_code":   "0x0204",
  "machine_name": "M20",
  "install_id":   "a3f7c2d1-...",
  "serial":       "XXXXXXX"
}
```

**Fields:**
- `event` — `job_sent`, `job_complete`, `job_failed`
- `version` — `gts_api.__version__`
- `platform` — `sys.platform`
- `model_code` — hex model code from `!ID` response
- `machine_name` — resolved from `PROFILE_FOR_MODEL_CODE`
- `install_id` — stable anonymous UUID, generated once on first run and stored in a local config file (`~/.gratisgraph/config.json`). Never tied to a person or machine identity.
- `serial` — machine serial number if available from `!ID` response (see open question below)

**Explicitly NOT collected:**
- Job content, coordinates, or file names
- IP address (beyond what a standard HTTP request reveals)
- Any personally identifiable information

---

## Machine Serial Number

Gravograph Quick appears to have access to the machine serial number. The `!ID` response returns 7 × int32 fields — we have confirmed 6:

| Field | Offset | M20 Express live value |
|-------|--------|------------------------|
| boot_ver_major | +0  | 1 |
| boot_ver_minor | +4  | 2 |
| prog_ver_major | +8  | 1 |
| prog_ver_minor | +12 | 30 |
| platform_type  | +16 | 4 |
| model_code     | +20 | 0x0204 |
| model_code_alt | +24 | 0 ← always 0 on our unit, possible serial field |

**Open question:** Is the serial number encoded in `model_code_alt` (currently always 0), in a separate USB command not yet captured, or in a different response entirely? Worth capturing a Gravograph Quick USB session with the sniffer to identify the serial number request/response pair.

---

## Implementation Plan

### Client side (`gts_api.py`)

- `_report_telemetry(event: str)` — private method on `GTSConnection`
- Called from `send_gts()` after successful job dispatch
- `requests.post(TELEMETRY_URL, json=payload, timeout=2)` — fire and forget
- Wrapped in `try/except` — never blocks, never raises
- Skipped if `GRATISGRAPH_NO_TELEMETRY=1` env var is set or config opt-out is true

### Install ID

Generated on first import if not present:

```python
config_path = Path.home() / '.gratisgraph' / 'config.json'
```

### Backend options

- **Self-hosted** — FastAPI endpoint writing to a database. Full control, requires infrastructure.
- **Cloudflare Worker + KV** — zero-cost for low volume, fast to deploy.
- **PostHog** — open-source analytics, self-hostable, has a Python SDK.
- **Simple webhook** — Airtable or similar for early-stage counting before committing to infrastructure.

### Opt-out

```bash
export GRATISGRAPH_NO_TELEMETRY=1
```

Or in `~/.gratisgraph/config.json`:

```json
{ "telemetry": false }
```

---

## Privacy Commitment (draft, for README/docs)

GratisGraph collects anonymous usage counts to understand which machines are in use and how often. No job content, coordinates, file names, or personal information is ever collected. You can opt out at any time by setting `GRATISGRAPH_NO_TELEMETRY=1`.

---

## Duration Estimation

**Status:** ✅ Characterised (Session 10) — implemented in `gts_optimise.estimate_duration()`

> Velocity was measured from EP 0x82 axis telemetry using `data/capture_motion.py`
> (90mm isolated X strokes at VS=10/25/40): cruise velocity is linear in VS at
> ~0.995 mm/s per VS unit; rapids run at roughly engrave speed; acceleration is
> not modelled (cruise is reached within one telemetry sample). Z-dip overhead is
> a rough fixed 0.5s/stroke estimate. The methodology below is retained for
> reference and for re-characterising other machines in the family.

### Goal

Estimate job duration before sending, surfaced in the web interface as
"estimated time: 2m 34s". Useful for shop operators planning workflow.

### What needs to be characterised

#### 1. VS speed unit → mm/sec mapping

`VS=25` is the default engraving speed. The relationship between the VS
parameter and actual tool speed in mm/sec is unknown.

**Method:** Send a known-length line (e.g. 50mm at VS=25), time it with
a stopwatch from first motion to last motion. Repeat at VS=10, VS=50.
Confirm linearity. Derive `MM_PER_SEC_PER_VS_UNIT = measured_mm_per_sec / 25`.

#### 2. Rapid speed

The stroke header segment (pen-up rapid to stroke start) and inter-stroke
rapids likely use a higher speed than VS. Never characterised.

**Method:** Send a jog to a known position (e.g. 50mm from home), time it.
Compare against the engraving speed timing to derive a rapid speed multiplier.

#### 3. Z approach and Z lift duration

`ZA` (Z approach) and `PU` (pen up / Z lift) are solenoid movements.
Duration is fixed per stroke regardless of geometry.

**Method:** Send a job with one stroke of one zero-length segment. Time from
job start to completion. Subtract rapid time to isolate Z motion time.
Measure separately for ZA and PU if possible via axis telemetry.

#### 4. Acceleration profile

The segment `accel` field is always 551 (0x0227) in all observed captures.
This may be the actual acceleration parameter rather than a sentinel value.
The machine exhibits visible acceleration — short segments appear to move
slower than long ones at the same VS setting, consistent with a trapezoidal
velocity profile.

**Trapezoidal motion model:**

For a segment of length `d` (mm), top speed `v` (mm/sec), acceleration
`a` (mm/sec²):

```
If d >= v²/a  (long segment — reaches full speed):
    t = v/a + (d - v²/a) / v

If d < v²/a  (short segment — never reaches full speed):
    t = 2 * sqrt(d/a)
```

**Method to characterise `a`:**
Send segments of varying length (1mm, 2mm, 5mm, 10mm, 20mm, 50mm) at the
same VS, time each. Fit the trapezoidal model to the measured times to
derive `a` in mm/sec². Then verify whether the `accel=551` field maps to
this value at the confirmed `MM_PER_SEC_PER_VS_UNIT` scale.

**Method to test accel variation:**
Send identical segments with `accel` set to values other than 551 (e.g.
100, 250, 1000) and observe whether motion changes. If it does, `accel`
is a live parameter. If it doesn't, 551 is a sentinel.

#### 5. Commands to time (in order of priority)

| Command | Phase | Parameter | Unknown |
|---------|-------|-----------|---------|
| Stroke header seg | Pen-up rapid | Implicit rapid speed | mm/sec |
| `ZA 1` | Z approach | Fixed duration | ms |
| `PD 0` | Pen-down trigger | Negligible | — |
| Engraving segs | Pen-down motion | VS, accel | mm/sec, mm/sec² |
| `PU 0` × 2 | Pen-up sequence | Fixed duration | ms |
| `PU 1` | Z lift | Fixed duration | ms |
| Return seg | Pen-up rapid home | Implicit rapid speed | mm/sec |
| Inter-stroke rapid | Direct rapid | Implicit rapid speed | mm/sec |

### Implementation plan (once characterised)

```python
def estimate_duration(
    strokes,
    speed:          int   = DEFAULT_SPEED,
    mm_per_sec_per_vs: float = ...,   # to be filled in
    accel_mm_per_sec2: float = ...,   # to be filled in
    rapid_multiplier:  float = ...,   # to be filled in
    za_ms:            float = ...,    # to be filled in
    pu_ms:            float = ...,    # to be filled in
) -> float:
    """Estimate job duration in seconds using trapezoidal motion model."""
    ...
```

Will live in `gts_optimise.py` alongside `optimise_strokes()` — both are
geometry operations on the stroke list that don't require USB or machine access.
