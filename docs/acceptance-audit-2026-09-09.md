> **2026-09-09 final content/package update:** [Structured-content safety and refreshed offline preview](content-safety-delivery.md): 329 Python tests, real offline HTML browser acceptance, four-viewport regression and fresh installed-wheel smoke passed. Use the local `preview-r2.zip`; real first-read and release gates remain open.

> **2026-09-09 installer update:** [Consent, isolated update/rollback and local preview bundle](bootstrap-delivery.md) now have real offline installation and unpacked-browser evidence. This is still a developer preview, not real first-read or public release certification.

> **2026-09-09 destination/filter update:** [Actual APFS cross-filesystem tests and readable-exclusion accounting](destination-and-filter-delivery.md) passed; native chooser timed out and is not certified. Physical external-disk and real first-read gates remain open.

> **2026-09-09 browser update:** [Reader interaction and four-viewport synthetic acceptance](browser-ui-delivery.md) now covers focus, responsive layouts, recoverable errors and four-format UI exports. This does not certify real first-read or independent-machine compatibility.

> **2026-09-09 accounting update:** [Source/attachment coverage delivery](attachment-accounting-delivery.md) adds explicit schema gaps, companion ledgers, local-media candidate observations and revision binding. This remains a developer preview; real first-read and release acceptance are not complete.

# Full-goal acceptance audit — 2026-09-09

Goal: finish the original guided Mac export scope, **not** merely accumulate green
unit tests. Requirements come from `mac-guided-export-plan.md` T01–T06 and
`review-brief.md`. The previous delivery is a developer preview, not proof that
all requirements were satisfied. No new real WeChat operation occurred in this audit.

## This continuation: observed defects and completed fixes

- Reproduction: a synthetic readable-only selection gave index preview **5** vs
  canonical output **6**. Index and canonical filtering now share one presentation
  function. Tests cover prefixed XML, empty text and unknown-but-readable content.
- Ordinary `Alice:\nmessage` was stripped as a sender envelope. It is now preserved
  unless the remainder is structured XML; canonical raw records remain unchanged.
- CDATA app-message titles now yield readable card titles.
- Explicit raw/analysis output modes: browser defaults to analysis. Analysis uses
  an allowlist, omits structured payload bodies/raw bytes/attachment URLs, and
  retains provenance. It is **not anonymization** and still contains personal text.
- Canonical source disappearance no longer silently falls back to the reduced
  viewer index. The manifest labels `record_source` explicitly.
- `slice` CLI now uses the same QuerySpec/writer as HTTP (range, type, mode, format).
- `verify --export-dir` previously returned success regardless of count mismatch
  and labelled partial status as decode-complete. It now returns failure for failed
  count/identifier/provenance checks, detects duplicate IDs, and never equates
  selected-source consistency with complete historical recovery.
- Old presentation indexes get an explicit versioned derived-index rebuild;
  canonical input bytes are verified unchanged in a regression.

## Initial audit evidence (historical; later delivery reports supersede test counts)

- Full suite: **133 tests passed**, 32.837 s.
- Focused regression suite: 29 tests passed after the initial 4 failures reproduced.
- Browser (isolated synthetic copy, port 8891): analysis JSONL / readable-only
  preview **6**, UI completion **6**, actual output **6**; manifest reports
  `mode=analysis` and `record_source=canonical`.
- JavaScript syntax and git whitespace checks passed. Browser CSP rejected eval;
  testing used normal controls instead of weakening CSP. Test tab/server cleaned up.
- No real keys or chat bodies inspected, no debug copy launched, no commits/pushes.

## Requirement-by-requirement status (completion is NOT proven)

| Requirement | Evidence / status |
|---|---|
| T01 explicit all/nonempty ID selections, duplicate-job isolation, CSRF/Origin/Host guards | Implemented; query, HTTP and job regression suites. |
| T01 raw/analysis filters and CLI/API semantics | Newly implemented and tested; canonical/view count regression fixed. |
| T01 DST ambiguity handling in browser date input | **Implemented/tested** in `timezone-install-acceptance.md`: unchanged ISO input, explicit timezone, gap/fold rejection and HTTP preview/export parity. |
| T02 empty-state launch, account discovery, writable installed storage | Implemented and covered by launch/discovery/runtime tests. |
| T02 first-run output-directory picker and graphical dependency installation guidance | **Implemented with synthetic/native-compiler evidence** in `output-location-delivery.md`: trusted native chooser bridge, persisted destination/job binding, capacity estimates and dependency guidance. Actual native dialog/TCC, physically distinct removable disk and independent-Mac UI acceptance remain unverified. |
| T02 exact module-fingerprint-bound compatibility evidence | **Registry/fingerprint implementation and policy tests complete**, `fingerprint-reader-delivery.md`. Entire bundle/modules and environment/driver IDs are bound; shipped reviewed entries remain empty pending real independently reviewed evidence. |
| T03 staged consent, private key file, source hash snapshot, scoped cleanup | Implemented; synthetic tests exist. New real coordinator not exercised. |
| T03 all relevant file-occupancy checks fail closed | **Implemented for all visible-process source-path inspection** before/after strict copy, with actual non-WeChat handle tests and bounded failure handling; see `fingerprint-reader-delivery.md`. Not a kernel exclusivity/privileged-process visibility guarantee. |
| T03 FTS exclusions proven not to contain message sources | **Implemented conservatively** in `source-ledger-delivery.md`: authentication exceptions block, successful authenticated output requires exclusively FTS virtual/shadow schema for exclusion; message tables are exported regardless of filename. |
| T03 whole state machine + every failure-stage fixture | **Synthetic coverage expanded**: 17 coordinator contracts plus 6 copy/signing identity tests, actual occupancy and existing real synthetic codec/KDF integration. Controlled real coordinator first-read remains unverified; see `fingerprint-reader-delivery.md`. |
| T03 new real-account controlled capture + independent environment | **Awaiting specific operator authorization/environment**. Do not reuse older manual success as proof. |
| T04 canonical schema_version and complete per-source processing ledger | **Selected-snapshot database ledger implemented** in `source-ledger-delivery.md`: schema version, per-file hashes, per-DB roles/status, source/output counts. Unknown schema coverage and detailed attachment accounting remain unverified/incomplete. |
| T04 snapshot hashes, excluded/skipped/failed/media counts in every slice manifest | **Incomplete**: current slice metadata is not the full required lineage/accounting contract. |
| T04 immutable preview/export source binding across tabs/account switches | **HTTP path implemented and tested** in `archive-binding-delivery.md`: source selection token, content hashes, private worker snapshot, and cross-account race tests. CLI verified-copy export and optional preview revision handshake implemented/tested in `cli-source-safety-delivery.md`. |
| T04 500k+ full normalization without list materialization | **Implemented and stress-tested** in `disk-normalization-delivery.md`: real parsing, disk sort, full outputs and indexing of 500,005 synthetic source rows; peak RSS 66.36 MiB. Encrypted DB streaming and 192 MiB main/WAL stress are now verified in `streaming-codec-delivery.md`; attachment serving, contact maps, and full-process-tree bounds remain separate limitations. |
| T04 UUID outputs, async work, cancel/interrupted status | **Implemented/tested**; `scratch-retention-delivery.md` adds lease/receipt-based orphan recovery, atomic slice/index publication and actual crash fixtures. Persistent retry materials/legacy unmarked directories intentionally retained; not a proof of all long-running cancellation boundaries. |
| T05 text/card/placeholder rendering, offline HTML, XML escaping | Implemented with synthetic regression evidence; full media recovery is explicitly outside the initial promise. |
| T05 dedicated quote/merged-forward/file renderers and rich-demo E2E | **Supported structural subset implemented/tested** in `message-card-delivery.md`; 12-record synthetic browser demo. Unsupported nested layouts remain partial; full version-specific and real-account visual matrix pending. |
| T05 drawer focus/error/small-screen comprehensive E2E | **Partial**: manual synthetic export smoke, not a full acceptance matrix. |
| T06 clean venv with no developer dependency reuse | **Verified on this Mac** in `timezone-install-acceptance.md`: new venv/dependencies, isolated outside-checkout startup and 9-record HTTP export. Does not replace independent-Mac acceptance. |
| T06 synthetic CI, exhaustive current-tree/history privacy audit | **Partial**: synthetic macOS CI workflow added but not executed on GitHub; fresh installed-wheel smoke passes locally (`cli-source-safety-delivery.md`). **Current-tree and two-commit reachable-history audit performed** in `privacy-release-audit-2026-09-09.md`; known historical identity metadata remains, so the history privacy gate **fails**. Current worktree fixes are not published; history remediation requires owner approval. |
| T06 fresh-user/independent Mac first-read acceptance | **Not verified**; developer preview only until evidence exists. |

## Next safe work (no real WeChat action required)

1. Finish first-run native-dialog/TCC, real removable-disk and responsive/keyboard
   acceptance. Destination selection, job binding, capacity guidance and
   destination-local atomic publication are now implemented; see
   `output-location-delivery.md`. Do not re-list them as unimplemented.
2. Obtain controlled real-machine and independently reviewed compatibility evidence
   without changing the empty published registry prematurely. Fingerprint binding
   and synthetic coordinator/signing failure coverage are now implemented; see
   `fingerprint-reader-delivery.md`. Installed build 269631 was observed on
   September 9, 2026 and is not an eligible candidate; earlier 269630 success is
   not evidence for it.
3. Finish detailed attachment/unknown-schema accounting and the full keyboard,
   focus, responsive and error UI acceptance matrix.
4. Execute hosted synthetic CI only after an owner-approved publication path;
   keep the historical privacy remediation decision separate from current-tree fixes.

This audit keeps the full goal active. It does not redefine completion to the
subset already implemented or turn missing verification into a success claim.

Latest local evidence: **184 tests passed**; see `cli-source-safety-delivery.md`. This does not close the outstanding acceptance rows.

Encrypted-file follow-up: see `streaming-codec-delivery.md` for bounded authentication/decryption, exclusive private publication and cancellation evidence. This is not real-account or independent-Mac acceptance.

Privacy follow-up: current-tree release guard and asset review added; the reachable-history audit found identity metadata, not passphrase/real-message leakage. See `privacy-release-audit-2026-09-09.md`; do not call the history gate complete merely because the audit ran.

Disposable-work follow-up: `scratch-retention-delivery.md` covers seven new managed temporary paths, protected recovery and atomic publication. It does not grant permission to purge persistent/legacy data or resolve the remaining real-environment/history gates.

Latest full local suite: **238 passed**; see `scratch-retention-delivery.md`. Previously completed source binding, disk normalization and disposable cleanup are no longer listed as unimplemented next steps.

Attachment streaming follow-up: `media-streaming-delivery.md` removes whole-file
HTTP attachment materialization and adds bounded reads, single-range seeking and
no-follow containment regression coverage. Full media recovery/accounting and the
remaining first-run/compatibility/release requirements are still outstanding.

Latest local suite: **260 passed**; see `output-location-delivery.md` for first-run destination, external registration, atomic full publication and the still-unverified native/removable-disk acceptance boundaries.

Latest local suite: **296 passed**; `fingerprint-reader-delivery.md` documents bundle/evidence binding, reader failure contracts, signing entitlement audits and non-WeChat source-handle checks. Real-build and independent-environment evidence remains missing.
