# Reader interaction and browser acceptance — 2026-09-09

This increment addresses the remaining usability gate in the original T01–T06
scope, not real key-capture or universal compatibility. No real WeChat process,
account discovery, native picker, retained key, or real archive was used.

## Reproduced defects and fixes

- At 390×740 the document grew to 1252 px and the export card exceeded the
  viewport. The chat now scrolls inside a fixed viewport; the export dialog has
  bounded internal scrolling and reachable sticky actions, including 740×360.
- Opening export left focus on the background trigger. Dialog focus is now moved
  inside, background sections are inert, Tab/Shift+Tab stay inside, Escape and
  close restore focus. The narrow conversation selector has the same behavior;
  its previously permanently hidden return button is available. Selecting a
  conversation closes that selector and releases the background.
- Cards were squeezed by nested percentage constraints. A single message-width
  constraint, consistent media-card width and initial-based avatar placeholders
  improve the WeChat-like two-column reader without adding a frontend framework.
  These are not downloaded real avatars.
- Media arriving after initial rendering left a 299 px gap below the viewport,
  hiding the latest messages. Initial tail-follow now handles media settlement;
  wheel/touch/pointer/keyboard interaction stops automatic tail-follow.
- Initial-message/pagination failures no longer strand `loading=true`; a visible
  alert and retry action recover. Old pagination/search results are ignored after
  changing conversations. Event handlers no longer accumulate after archive opens.
- “Only text” is now “readable content”, matching the actual filter (which can
  include readable structured-message cards). File cards say the binary was not
  exported, rather than asserting it does not exist locally.
- Export success mentions coverage/attachment companions and absent binary media.
  Polling no longer silently abandons a running job after a fixed 240 seconds;
  cancel/reveal failures are handled rather than unhandled promise rejections.

## Reproducible browser smoke

`tests/browser/guided-ui.cjs` starts its own loopback server and Chromium, makes a
private temporary copy of the fictional example archive, exercises real UI
controls and actual export endpoints, and stops its owned processes. No runtime
or package dependencies were silently installed by the test.

```sh
# Use an existing local Playwright installation; never point at real archives.
WLA_PLAYWRIGHT_MODULE=/absolute/path/to/node_modules/playwright \
  node tests/browser/guided-ui.cjs
```

Optional `WLA_PYTHON` selects Python; the default is checkout `.venv/bin/python`.
The module version used locally was 1.60.0. The test writes synthetic-only
screenshots/server logs beneath its reported temporary evidence directory.

Passed actual browser checks:

- 1280×900, 390×740, 320×568, 740×360: no page overflow, bounded dialog.
- Keyboard entry, 24 forward + 24 reverse Tab steps per viewport, Escape/focus
  restoration, background inertness, narrow conversation selection/closure.
- Empty multiselection is disabled; explicit all previews 12 synthetic records.
- Deliberate HTTP 503 on messages → visible alert → retry → real messages.
- Invalid date error → corrected input → successful preview without reload.
- JSONL, CSV, Markdown and offline HTML each export 12 records; actual files,
  manifests and coverage companions exist with live-db/unverified provenance.
- No unhandled browser page errors; initial view reaches the last message after
  image settlement. Desktop/narrow/short-window screenshots were inspected.

Final browser log: `/tmp/wla-ui-browser-tail-final.log`.
Full Python suite: **306 passed, 74.146 s** (`/tmp/wla-ui-final-full-tests.log`).
The last JavaScript-only tail-follow change was checked by the final real browser
smoke and Node syntax validation. No Python changed after this suite run.

The Paseo browser's evaluate path was rejected by CSP and screenshot capture had
no frame. We used an owned headless Chromium instead; **CSP was not weakened**.

## Privacy and test isolation details

The initial browser prototype used checkout `--demo`, which writes synthetic
slices next to the example archive. Its single exact output was moved into that
prototype's owned temporary evidence directory. The committed test now uses an
explicit private copy and never exports into the checked-in example directory.

Opening the demo refreshed its derived SQLite index for current sidecar hashes.
Before updating its asset review, every table (including metadata/FTS) was
compared against an independent fresh rebuild of the reviewed 12-record canonical
fixture: equal rows, integrity ok, freelist zero, no operator-home path. This is
an exact synthetic-asset review, not a general approval of arbitrary SQLite.

CJS/MJS source is now handled by the privacy scanner's text rules, with a test
that credential rules still apply. The runtime presentation lock is gitignored.
No commit, push, history rewrite, public artifact upload or remote CI run occurred.

## Remaining acceptance limits

This is **Chromium/synthetic browser acceptance**, not VoiceOver/Safari/iOS,
real-account first-read, independent Mac, TCC/native-picker or removable-volume
acceptance. Automated tests do not validate subjective resemblance to every
WeChat version. Binary media recovery remains outside this increment.

The observed 269631 installation still has no newly authorized compatibility
entry. The reviewed compatibility registry remains empty. Refer to
[fingerprint delivery](fingerprint-reader-delivery.md) and
[source/attachment accounting](attachment-accounting-delivery.md) for remaining
release gates. Full original-goal completion remains unproven.

## Final package verification

- Fresh independent installed virtual environment, outside-checkout `python -I`
  smoke passed: 12 synthetic messages, revision-bound export, registered external
  output and bundled empty registry (`/tmp/wla-ui-installed-smoke.log`).
- Wheel: `dist/wechat_export-0.2.0-py3-none-any.whl`.
  SHA256: `2d7e416c984cdba98419270ce4d8de58fdd96774c19bbbe998c438c4691192d9`.
  All packaged source/static/registry bytes match the working tree, including
  `interactions.js`; no private roots in the wheel.
- Final working-tree privacy rule scan has no findings, with
  `privacy_certified=false` and no private identity needles supplied.
- Node syntax checks for all three browser scripts and `git diff --check` passed.
- This turn's owned listeners and browsers were stopped; user services were not.
