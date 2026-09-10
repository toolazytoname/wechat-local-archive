# Structured content and offline HTML — 2026-09-09

Developer preview only. `source_kind=live-db`; `backup2_coverage=unverified`.
This continuation used synthetic records only: no WeChat process, key capture,
real archive upload, commit or publication.

## Correctness fixes

- Reproduced seven failing structured-payload subcases before fixing detection:
  self-closing/unknown/system/media roots and BOM-prefixed XML must not become
  readable text. A structured title containing another XML payload is suppressed.
- Presentation version is now **4**; derived indexes refresh. Canonical raw records
  retain the original payload. Ordinary name prefixes and embedded literal markup
  remain text. Leading structured tags are treated conservatively as structured
  content; this is not a general-purpose secret redactor or anonymizer.
- Direct and slice HTML share escaping, responsive rows and a restrictive CSP:
  `default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`.
- Direct HTML streams to private owned scratch, flushes/fsyncs and publishes
  exclusively. Existing files/symlinks are refused, not overwritten; failed
  generators publish no partial destination. Attachment binaries are not embedded.

## Evidence

- Python full suite: **329 passed**, 68.615 seconds
  (`/tmp/wla-content-full-tests.log`).
- Content browser acceptance: **19 synthetic records**, live external requests 0,
  offline HTTP requests 0, no injected execution/dialogs/page errors. A delayed
  search response from a previous conversation was ignored correctly.
- Both HTML writer outputs opened via `file://`, with the source server stopped,
  at 1280 and 390 CSS pixels. No horizontal overflow. Narrow slice and desktop
  standalone screenshots were visually inspected: wrapped metadata, white/green
  bubbles and media placeholders; malicious-looking fixture names render literally.
- Broader guided Chromium regression repeated: four viewports, keyboard/focus,
  error recovery and four-format export of 12 synthetic messages passed
  (`/tmp/wla-final-guided.log`).
- Fresh wheel installed offline into an independent temporary venv. Isolated
  installed smoke exported 12 synthetic records with revision binding, external
  destination registration and provenance checks (`/tmp/wla-final-install.log`).

## Refreshed local artifacts

- `dist/wechat_export-0.2.0-py3-none-any.whl`
  SHA-256: `f016e9ff2c1d2542dd7a407d9ce8f2ab6d8e86a5375372c3c899e090c998e738`
- `dist/wechat-local-archive-preview-r2.zip`
  SHA-256: `0390e582e5436dd62cf7b32320e7df8557978ded7737d3322f041e5dd0a64951`
- Bundle built twice with identical bytes; builder compared package bytes with
  current source and required an empty compatibility registry. Old preview zip
  remains a historical artifact. Nothing was uploaded or publicly released.

## Still required before calling the original goal complete

1. Eligible exact-build, UI-driven no-existing-key first-read and human samples.
   Current local 269631 observation does not inherit earlier 269630 evidence.
2. Independent Mac/new-user acceptance and explicit reviewed compatibility entry;
   shipped registry remains empty, not a claim of supported production builds.
3. Successful native chooser/TCC acceptance (last actual attempt timed out).
4. Physical removable-disk testing; mounted APFS images are narrower evidence.
5. Owner-approved public-tree/history review and publication; rule scans are not
   privacy certification. Older history metadata has not been rewritten.

See [original acceptance audit](acceptance-audit-2026-09-09.md),
[installation delivery](bootstrap-delivery.md) and
[original scope](mac-guided-export-plan.md). No claim of complete media recovery,
backup-2 decoding, large-HTML browser scalability, or universal Mac support.

## Installed-browser follow-up

The browser harness now has an explicit `WLA_INSTALLED_PYTHON` mode. Merely
setting `WLA_PYTHON` previously did not establish wheel isolation because the
server still ran in the checkout. Installed mode probes the resolved package,
rejects imports beneath the checkout, launches with `-I` and uses an owned
outside-checkout working directory.

```sh
WLA_INSTALLED_PYTHON=/path/to/installed/venv/bin/python \
WLA_PLAYWRIGHT_MODULE=/path/to/playwright \
  node tests/browser/guided-ui.cjs
```

Actual refreshed-wheel browser run passed all four viewports, keyboard/focus,
recovery and four-format exports of 12 synthetic records, with
`installed_package=true` and `isolated_python=true`
(`/tmp/wla-isolated-browser.log`). Negative control pointing to the editable
checkout environment failed before server launch with `Installed mode imported
checkout` (`/tmp/wla-isolated-browser-negative.log`). This closes an installation
verification gap; it does not certify first-read or remote CI.

Read-only `Info.plist` recheck still reports 4.1.13 / 269631 on arm64. No WeChat
process was launched. No compatible-build or stage-authorization gate was relaxed.
The package bytes are unchanged by this test-only follow-up; r2 hashes above
remain applicable.
