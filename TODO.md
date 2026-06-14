# TODO

Single source of truth for task tracking. Update here, not in scattered docs.
Last updated: 2026-06-14 (release-review remediation: SSE ticket auth, MC/telemetry/profile doc fixes).

## Decisions needed (Steve)

- [x] **`send_gts(home_first=True)` default** — DECIDED 2026-06-09 (Steve):
      stays `True`. Homing first gives every job a known position reference;
      callers that want direct execution pass `home_first=False` / `--no-home`
      explicitly. Decision noted in the send_gts docstring.
- [x] **Execution-model wording** — RESOLVED on hardware 2026-06-09:
      connected sends QUEUE (beep per job, !JF receipt ack ~2s later); they do
      NOT auto-execute; ONE green press runs the entire queue in order; green
      again replays; disconnect re-beeps after ~15s but does not execute
      without !CM arming. The findings doc's "connected workflow executes
      without green" claim and the "two green presses" claim are both WRONG —
      corrected in docs/GTS_FORMAT.md §6, send_gts docstring, and the monitor
      (!JF no longer treated as completion; regression test added). Findings
      doc itself still needs the same correction when exported into the repo.

## Hardware-blocked

- [x] **Stroke-header semantics — RESOLVED: RELATIVE (2026-06-09).** Paired
      hardware runs + telemetry (!MY 7999 vs 3999) confirmed every wire
      coordinate is a delta. `build_gts` rewritten to the proven format
      (byte-locked regression), `gts_preview` integrates deltas, GTS_FORMAT
      §5.6 and findings doc updated. End-to-end sealed same night: the
      rewritten build_gts run on hardware landed both strokes at commanded
      positions. THE WEB MVP IS UNBLOCKED.
- [x] Felt-tip marker adapter — built and used for the deciding runs 2026-06-09
      (tip degrading; rebuild before long jobs).
- [x] **Gravograph Quick USB capture — DONE 2026-06-09.** 11 captures via RP2040
      sniffer (data/quick_capture/, raws kept + .summary.txt each via
      data/summarise_capture.py, which imports the sniffer's own frame_parser).
      KEY RESULT: Quick's wire format IS GTS exactly as we documented it —
      independent byte-for-byte confirmation. Corrections: MC = spindle enable
      (not machine-coords); PZ = engraving depth (Quick drives it from its depth
      field, 0.01"->PZ 25, 0.99"->PZ 100); material = pass-count, not a wire
      depth param; Quick's ident = our !ID 0; ×3 + !1B. See findings doc
      'Gravograph Quick Capture Findings'. Serial-number question RESOLVED 2026-06-13 (Steve): the machine does NOT relay a serial number to Quick (no SN readback on the wire).
- [x] Multi-stroke wire format validated directly on hardware 2026-06-09 —
      stronger than a Gravostyle capture comparison (which is now optional
      corroboration if one ever turns up).

## Code

- [x] **`gts_capture.py` — user-side USB capture + summariser** — DONE 2026-06-09
      (8 summariser tests; validated against replayed bytes from the live
      2026-06-09 session; reuses GTSConnection taps — no second parser;
      hardware smoke run still pending). Original spec follows: (aids support for
      other family models). Captures EP IN/OUT traffic during a session and emits a
      summary that is *informative and minimal*. **#1 design goal: minimise token
      cost of analysing the output** — all processing happens on the user's side,
      the report is the deliverable, never the raw stream. Spec:
      - Decode every known packet type to one line (!ID fully fielded; setup
        opcodes as `VS=25 VZ=25 PZ=30 DZ=500`; segments as count + bounds, not
        per-segment dumps).
      - Deduplicate repetitive telemetry: `!MX ×142 (values 0–9000, monotonic)`
        instead of 142 lines. Collapse identical packets to `×N`.
      - Hex-dump **only** unknown/unparseable bytes, capped (e.g. first 64 bytes
        per unknown packet, max ~20 unknowns), flagged `UNRECOGNISED` — these are
        the interesting bits for new-model support.
      - Header block: timestamp, gts_api `__version__`, OS, VID:PID, decoded !ID
        (platform, model code, boot/prog versions), selected profile.
      - Hard cap total report size (~4–8 KB) so a pasted report is always cheap
        to ingest. Deterministic ordering for diffability between sessions.
      - Plain text to stdout/file; one command, zero config:
        `python3 gts_capture.py --duration 30 -o report.txt`.
      - **`--raw` option, deliberately undocumented in help/README**: writes the
        unprocessed byte stream to a `.bin` alongside, no summarisation. For
        deep analysis only; never the default and never pasted into reports.
      - Implementation: **reuse `GTSConnection._parse_rx_buf` for decoding** —
        do not grow a second parser, or the capture tool drifts from what the
        driver actually sees. May require refactoring the parser into a
        standalone function that both the connection and the summariser call.
        The only genuinely new logic is run-length/range collapsing and the
        report formatter.
      - Follow-up once shipped: the unknown-model-code warning in
        `_auto_select_profile` should point users at `gts_capture.py` as the
        one-command way to file a useful report.
- [x] Write real tests for `console.py`, `calibrate.py`, `gts_preview.py` —
      DONE 2026-06-09 (16 tests: REPL dispatch with stubbed connection,
      calibration patterns translate + fit envelope at all scales, preview
      parses builds AND the real Gravostyle regression capture byte-for-byte).
- [x] Prune legacy tables in `gts_api.py` — DONE 2026-06-09. `!GA`/`!DL` probe
      observations preserved in GTS_FORMAT.md §8 so they aren't re-discovered.
- [x] Delete `backup/` — DONE 2026-06-09.
- [x] Remove duplicate `data/00013.SPL` — DONE 2026-06-09.

## Docs

- [x] Findings doc — DONE 2026-06-09: exported into the repo as
      `docs/PROTOCOL_FINDINGS.md` (Session 11) with all corrections
      applied: OQ #2/#5 moved to Resolved, execution model rewritten, !JF
      semantics, counted framing, model_code-vs-platform_type note, DZ-sweep
      added as a new open question. It is now the version-controlled research
      log; GTS_FORMAT.md is the normative spec. The copy in the Claude Project
      is superseded — Steve to refresh/replace it there.
- [x] Pre-merge checklist — satisfied 2026-06-09: the `.job` section was
      excluded from the repo export entirely.
- [ ] **User-facing docs** — there is effectively no end-user documentation yet
      (README is dev-oriented). When written (Quickstart: install the tray or
      run the server, pair, open gratisgraph.com, send a job), include:
      (a) the pairing token is a LOCAL loopback secret, not an account
      credential — it never leaves the machine and only gates 127.0.0.1;
      (b) the macOS one-time sudo/kext-detach step; (c) where support captures
      come from (gts_capture.py). [from RELEASE_REVIEW 3.5]

## Web MVP roadmap (in order)

- [x] **0.** Stroke-header measurement — DONE 2026-06-09. Roadmap unblocked.
- [x] **1.** Extract GTS wire format into `docs/GTS_FORMAT.md` — the contract for
      the JS port, and the publishable interop artifact. *(Done 2026-06-09;
      §5.6 carries the open stroke-header question; update it when the
      measurement lands, then it's the oracle for the TS port.)*
- [x] **2.** `gts_server.py` — done 2026-06-09, curl-proven against a stubbed
      connection, 12 HTTP-level tests. **Deviations from original spec, both
      deliberate:** stdlib `http.server` instead of FastAPI+uvicorn (zero deps to
      install/freeze, smaller attack surface) and SSE instead of WebSocket
      (one-way stream, EventSource auto-reconnect, stdlib-serveable).
      Endpoints: `GET /` (unauth liveness), `GET /machine`, `POST /job` (raw
      .m20, ?home_first=0|1, 202+job_id), `GET /job/<id>`, `GET /telemetry`
      (SSE), `POST /abort`. 127.0.0.1 only, pairing token at
      `~/.gratisgraph/token`, per-origin CORS allowlist via `--allow-origin`.
      Added `GTSConnection.add_tap()` fan-out (also what gts_capture needs).
      Remaining: smoke-test against real hardware; PyInstaller packaging later.
- [x] **3.** TypeScript GTS engine — COMPLETE 2026-06-10. All five
      modules ported and proven byte/hash-identical to Python across the
      corpus: round.ts, gcode.ts (incl. arcs — JS trig bit-matches Python),
      optimise.ts (TSP/2-opt decisions identical), gts.ts, preview.ts
      (parseGts matches on all 15 golden .m20 + round-trip). 50 TS tests.
      Compiled browser engine (app/engine/, npm run build:engine) verified
      byte-identical to Python including the optimiser path. Plus scene.ts:
      editable layout model with full {tx,ty,rotation,scale} affine, unit-
      tested (rotate-90/scale-2x pass) — rotate/scale UI is future work only.
      2026-06-09 after debating both ways — record so it stays decided:
      *Considered*: server-side translation (one implementation, already
      hardware-proven) — rejected because it puts the volatile code (format,
      optimiser, machine profiles) behind a user reinstall, while browser
      code on static hosting hot-updates on page load. An auto-updating
      server was rejected harder: auto-update on a motor-driving localhost
      daemon is RCE-by-design and breaks the "talks to nothing" guarantee.
      *Division of labour = change-rate gradient*: USB transport (changes
      never) → installed server, finished software; format/optimiser/
      profiles (change with every new model report) → browser, hot-fixable.
      Python stack remains the reference implementation + CLI + corpus
      generator; golden corpus (DONE — tests/golden/, 15 cases, 32-test
      drift guard, hardware-anchored) binds the two.
      *The stroke-JSON seam* (Steve's insight) lives in the browser:
      parsers (SVG/DXF/G-Code → strokes) are format-dumb; the GTS engine
      (strokes → optimise → .m20) is the port. Stroke JSON doubles as the
      editor's save/open format (.ggs) — schema in docs/STROKE_SCHEMA.md.
      *Port order*: half-to-even rounding util FIRST (the #1 byte-mismatch
      trap, see schema doc) → binary builder → optimiser (port faithfully,
      same operation order, not elegantly) → G-Code parser → preview parser.
      Conformance = walk tests/golden/manifest.json, byte-compare.
      Server gains NO /strokes endpoint; gts_server.py is feature-stable
      after the capture smoke run + packaging.
- [x] **System tray app — DONE 2026-06-09** (tray/, own top-level dir,
      pystray). Wraps the current Python gts_server. Headline feature per
      Steve: a SUPPORT VERSION SNAPSHOT — every pipeline component's version
      (gts_api/translator/optimise/prepare/preview/server) + OS/Python, read
      LIVE from the server's diagnostics (never hardcoded), one-paste via
      'Copy support snapshot'. Added version.py registry + per-module
      __version__; server GET / now returns diagnostics. Menu: pairing code
      (copy), machine status, Versions submenu, regenerate token, Unlock
      (visible only during lockout, in-process reset = proof of presence,
      no HTTP unlock endpoint), Open web UI, Quit. Logic split from pystray
      rendering -> 7 headless tests. If a native server ever ships, revisit.
- [~] **Step 4 — web UI (IN PROGRESS 2026-06-09).** web/app/ — index.html +
      styles.css + app.js, consuming the proven engine. Design: machine-bed-as-
      interface, brushed-graphite, one accent (--cut #ff6a3d) for the engraved
      line, --rapid blue for pen-up, machinist grid, Space Grotesk + JetBrains
      Mono. GTS-DRIVEN preview (renders the built .m20 parsed back via parseGts,
      per the standing principle). Engine compiled to browser JS via
      tsconfig.build.json (rewriteRelativeImportExtensions -> app/engine/*.js,
      gitignored, npm run build:engine) — VERIFIED byte-identical to Python incl.
      optimiser. pipeline.ts adds fromGcode + .ggs save/open (lossless round-trip
      test). Wired: file drop (.gc/.gcode/.ggs), job settings (speed/depth/origin/
      optimise/flipY), server polling, pairing token, send job, SSE telemetry with
      a head crosshair, save .ggs, report-machine (opens GitHub issue).
      SMOKE-TESTED LIVE 2026-06-10: server pill green (CORS via --allow-origin),
      paired, machine identifies (M20Express), BetterBit renders. Since then:
      light/dark theme, two-mode scene editor (Edit=arrange objects w/ drag +
      gray bbox + 1mm snap, Prepare=GTS-driven preview), multi-file objects,
      object list, New scene/Delete, scene autosave to localStorage (survives
      refresh+restart), token persistence w/ paired-collapse + distinct
      rejected state, toasts top-center, non-selectable chrome, thinner grid
      w/ labels outside bed, true-scale toggle, Prepare button moved above Send.
      LIVE SEND TEST PASSED 2026-06-10 (Steve): add file → Prepare → Send →
      green → engraved. Full stack proven end to end.
      REMAINING (Steve-prioritised 2026-06-10):
      (1) HIGH: file parsers. DXF DONE 2026-06-10 (src/dxf.ts, from scratch,
      zero deps per Steve — note: parser is browser TS, no tray-installer
      impact). Entities: LINE, LWPOLYLINE incl. bulge arcs BOTH directions
      (positive bulge = CCW per AutoCAD spec = bows right of chord — locked
      in tests after my test initially encoded the wrong intuition), legacy
      POLYLINE/VERTEX/SEQEND, ARC, CIRCLE, ELLIPSE, SPLINE (de Boor NURBS),
      $INSUNITS scaling, skip-with-warning for the rest. Chord tol 0.02mm.
      10 geometric tests (no Python oracle for this module — known-shape →
      known-coordinate asserts). Wired into addFile (.dxf accepted).
      SVG DONE 2026-06-12 (parseSvgToStrokes in app.js — browser-native
      flattening via getPointAtLength + getCTM, splits subpaths at pen-up
      jumps, skips defs/markers; SVG is Y-down = machine frame, no flip;
      text/fills still out of scope v1). Real CAD-export acceptance DONE
      2026-06-12: GratisGraph.dxf (22 polylines) + betterbit_wordmark.dxf
      (11) both engraved on hardware after fixing the SEQEND parser bug —
      legacy POLYLINEs end with a zero-code SEQEND, so readEntity's
      "don't stop on the first 0" guard swallowed each following POLYLINE
      header and only the first survived. Guard removed; all 10 dxf tests
      still pass; engine rebuilt.
      (3) LOW: Head-position dot — !MX/MY/MZ value is a motion COUNTER not a
      position (gts_api 150/1391); jog streaming unconfirmed. Options: !OR
      static marker / motion indicator / drop.
      (4) Favicon — DONE 2026-06-12: themed favicon.svg ("G" monogram,
      @media prefers-color-scheme for OS dark/light) + <link> in index.html.
      ALSO DONE 2026-06-12 (web UI polish — app.js/index.html/styles.css,
      no engine rebuild except dxf.ts):
        - Text orientation: fonts are Y-up; flipped into the Y-down machine
          frame + centred on the bed. Verified on hardware — "F" engraves
          upright AND matches the canvas (WYSIWYG).
        - Mirror tools: horizontal/vertical buttons in the toolbar (mirror
          selected object about its local centre, world position preserved).
          The old "Flip Y (LightBurn)" toggle was removed.
        - Estimated duration shown in Prepare (port of gts_optimise.
          estimate_duration: VS*0.995 mm/s, 0.5s Z-dwell/stroke, rapid=1.0).
        - Imports scale-to-fit (~90mm) + centre on the bed.
        - Object scale floor lowered 0.05 -> 0.001 (field + gizmo) for very
          large DXFs.
        - Pairing-token field no longer triggers Chrome's save-password
          prompt: type=password -> type=text masked via CSS
          -webkit-text-security + data-1p-ignore/data-lpignore.
        - Coordinate docs/strings corrected to the marker-confirmed mapping
          (home = BACK-left / nearest the gantry, +Y toward the operator) in
          gcode.ts, gts_translator.py, GTS_FORMAT.md, PROTOCOL_FINDINGS.md,
          STROKE_SCHEMA.md, gts_api.py, gts_prepare.py, dxf.ts.
      STILL OPEN under Step 4: head-position dot (low - !MX/MY/MZ is a motion counter, not a position). Loose threads (untracked, low priority): DXF open-polyline
      auto-close (only if a wordmark shows a gap); G-code import Y
      convention (deferred).
- [ ] **Single C server for ALL platforms** (Steve's proposal, DECIDED
      2026-06-09; replaces the XP-only C sender). STATUS 2026-06-13: XP dropped, so the XP-only justification is void; Windows now ships as a PyInstaller Python onefile (Win10/11, see Distribution & ops). This C dumb-pipe server is DEFERRED - remaining rationale is static ~300KB binaries + no AV-flag tax. DECIDE: keep as a future optimisation or drop. The TS-engine decision makes
      this strictly better: with protocol interpretation in the browser
      (hot-updatable), the server needs ZERO GTS knowledge — no packet
      parser (a C parser would have been the forbidden third), no /machine,
      no profiles, no completion logic. Scope, frozen by design: loopback
      HTTP via civetweb (MIT — NOT mongoose, GPLv2-only conflicts with our
      GPLv3), token auth + lockout, POST /write (opaque bytes → EP OUT),
      GET /stream (raw EP IN relay), status. libusb everywhere. ~1–1.5k LOC, static binaries ~300KB, no PyInstaller AV-flag tax.
      gts_server.py is DEMOTED, not deleted: reference implementation + dev
      server. Define the HTTP contract as docs/SERVER_CONTRACT.md; the
      existing HTTP test suite becomes the shared black-box conformance
      suite for both servers (golden-corpus pattern, applied to HTTP).
      SEQUENCE AFTER STEP 3: dumb-pipe presumes browser-side telemetry
      decode. Path: TS engine → add raw-relay SSE mode to Python server
      (add_raw_tap already exists) → prove browser decode against Python →
      C server as drop-in. Memory-safety posture: civetweb parses HTTP,
      handlers are 5 fixed routes, GTS bytes stay opaque, loopback-only,
      fuzz the handlers.

## Distribution & ops (2026-06-13 session)

Hosting, mirroring, donations, feedback, cross-platform packaging. Full rationale
is in the session handoff doc; this is the action tracker.

### Decided
- [x] **Hosting -> Cloudflare Pages.** Remote (not same-origin, not GitHub Pages)
      so the web app hot-updates on git push. gratisgraph.com LIVE: zone Active,
      custom domain working, loads on mobile. Build: root `web`, cmd
      `npm install && npm run build`, output `app`, NODE_VERSION=22 (engine is
      gitignored -> built on deploy). Consequence: the bridge must do CORS +
      Private Network Access for the hosted origin (action below).
- [x] **macOS tray -> arm64-only (Apple Silicon, M1+).** No Intel/universal.
      macOS 26 is the last Intel macOS; 27 is ARM-only; Rosetta is Intel->ARM
      only, so an arm64 app won't launch on Intel. Intel holdouts can run
      gts_server.py from source + the web app.
- [x] **Windows tray -> PyInstaller x64 onefile, Python 3.13/3.14, Win10/11
      only.** Dropped XP and 8/8.1. Bundle libusb-1.0.dll. ARM Windows runs x64
      via emulation. (Reconcile vs the DEFERRED C server above.)
- [x] **Donations -> Ko-fi.** No donor account, one-time tips, 0% on the free
      tier (the friction priority). Rejected GitHub Sponsors (donor needs an
      account) and Liberapay (donor account + recurring-only, despite the values
      appeal). Card rails still ride Stripe/PayPal (freeze risk); optional crypto
      hedge. Ko-fi bio drafted (in handoff).
- [x] **Feature requests -> GitHub Discussions (Ideas)** + in-app Feedback link,
      kept separate from bug reports. Revisit a Cloudflare Worker + Turnstile
      form only if non-GitHub users prove a meaningful segment.
- [x] **Hardware/coverage -> capture-by-proxy first.** Owners run the existing
      sniffer + gts_capture.py on machines they own and send captures (scales,
      zero shipping/customs). Physical gear: prefer a Ko-fi goal to fund a used
      M40 over shipping; prefer gifts over loans (the "hardware owned by the
      authors" legal framing stays clean). IM4E is a separate serial-protocol
      project - take if offered, don't chase.

### Actions (next desktop session)
- [ ] **Commit the uncommitted tray work FIRST:** gts_api.py, tray/usb_helper.py,
      tray/gts_tray.py, tray/make_icon.py, tray/setup_app.py, tray/resources/,
      .gitignore, TODO.md -> one commit + push.
- [ ] **macOS tray finalize:** fix `_copy` -> real clipboard via `pbcopy` (today
      it only notifies); add bridge CORS + PNA (gts_server allow-lists
      https://gratisgraph.com, answers the OPTIONS preflight, returns
      `Access-Control-Allow-Private-Network: true`; tray run() defaults
      `--allow-origin https://gratisgraph.com`) -- THIS is what stops the hosted
      app saying "server offline"; rebuild the .app (arm64).
- [ ] **Windows tray:** build the PyInstaller onefile; bundle libusb DLL; solve
      the usbprint.sys vs WinUSB claim (libusbK/Zadig) - the Windows analog of
      the macOS kext-detach.
- [ ] **Cloudflare/web:** add web/app/_headers (`Cache-Control: must-revalidate`)
      for instant updates; optional CSP with the bridge in connect-src.
- [ ] **GitLab mirror:** one-time GitHub->GitLab import now (snapshot); GitHub
      Action push-mirror (.github/workflows/mirror.yml + GITLAB_TOKEN secret -
      secret needs Safari desktop mode, can't be set from the GitHub mobile app).
- [ ] **Feature-request wiring:** Discussions Ideas category + Feedback link in
      the web-app About modal (app version auto-prefilled for triage).
- [ ] **Donations wiring:** claim the Ko-fi page (reserves the handle); add a
      Donate link in the About modal + `ko_fi:` in .github/FUNDING.yml (also
      gives the repo a Sponsor button).
- [ ] **Hardware program:** "Help expand coverage" wishlist in README + web app,
      each item tied to the capability it unlocks (M40 -> confirm M40 profile;
      M20 Jewel -> confirm negate-X; spindle -> map spindle vs solenoid Z).
- [ ] **Housekeeping:** delete the `backup/pre-squash` git branch if it still
      exists; keep `RELEASE_REVIEW.md` untracked.

### Files to stage (offered, not yet written)
mirror.yml (GitHub->GitLab push-mirror) | web/app/_headers (cache-control +/-
CSP) | .github/FUNDING.yml (ko_fi:) | About-modal snippet (Feedback + Donate).

### Exploratory
- [ ] **iPad DriverKit dext** - M-series iPad only, iPadOS 16+; a new native C++
      codebase (the Mac uses libusb, nothing to "port"); App Store distribution
      + review + $99/yr; the distribution entitlement is Apple-granted per-team.
      Caveat: the App Store is the most centralized/easily-pulled channel - treat
      as a convenience, not the resilient core. Upside: the dext matches the M20
      by VID:PID directly, likely sidestepping the kextunload-as-root dance.

## Long horizon

- [x] **Telemetry/analytics — DECIDED 2026-06-09 (Steve): no automated
      telemetry, by design.** Posture dissonance (clean interop stance; the
      audience runs open drivers to escape vendor behaviour — "phones home"
      is an asymmetric reputation wound), permanent infra/privacy tax on a
      zero-server architecture, and it would muddy gts_server.py's cleanest
      security claim ("binds loopback, talks to nothing"). Consent-by-action
      channel: gts_capture.py. Folded into Step 4: a "Report your machine"
      button that pre-fills a GitHub issue, report shown before sending.
      TELEMETRY.md analytics half marked superseded. Revisit bar, if ever:
      opt-in at first run, payload shown verbatim, model_code+version only,
      open endpoint code.
- [ ] Hackaday submission for the sniffer project (draft exists).
