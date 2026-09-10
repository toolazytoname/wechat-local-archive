# Source and attachment accounting delivery — 2026-09-09

This is a developer-preview increment, not a new real first-read certification.
No WeChat process was launched, no retained source/key was modified, and no real
chat was printed or uploaded in this continuation.

## Implemented

- Full and selected exports publish `attachment-ledger.jsonl`, `coverage.json`,
  and human-readable `coverage.md` alongside messages and manifests. Configured
  target exports have their own selected counts and companion files.
- Snapshot-wide database inventory is distinguished from selected-message counts.
  Unknown tables, rows, views and triggers remain explicit gaps; authenticated
  pipeline exports with unknown schema are marked partial. Operator-supplied
  plaintext does not acquire authenticated-snapshot completeness claims.
- FTS exclusion checks schemas recreated in isolated in-memory SQLite. A
  contentless FTS table cannot hide a user-created `search_content` table merely
  because SQLite labels it a shadow table.
- Attachment references and base64 payload blobs are separate categories. Ledgers
  omit chat text, payload bytes and arbitrary attachment paths/URLs. Reference
  inspection has bounds; nested/unsupported/uninspected cases are explicit.
- Optional slice media inspection uses only registered local media roots and
  supported primary-media layouts. Header/stat observations distinguish local
  candidates, opaque candidates, video previews and missing candidates. They do
  **not** authenticate media contents, freeze media, copy binaries, fetch URLs or
  prove historical availability. Binary files exported remains zero.
- JSONL/CSV/Markdown full writers share one streaming implementation. Target
  folder name collisions are disambiguated. Existing same-run results are reused
  only after input/request and recorded artifact hashes match; tampering fails
  rather than overwriting an old archive.
- Coverage/attachment sidecars participate in archive revision binding and private
  source copying. Mutation invalidates an existing selection. Slice publication
  remains atomic for messages, manifest and all companion files.
- CLI coverage refresh now recomputes generated artifact hashes before publication.

## Evidence

Focused tests cover contentless FTS impostors, unknown schema objects, payload vs
media classification, bounded inspection, forged availability, local header
statuses, symlink candidates, sidecar mutation/private copying, export idempotency
and tampering. All inputs are synthetic. Final suite and scale measurements are
recorded below after completion.

Installed-wheel smoke ran in a fresh independent virtual environment, outside the
checkout with `python -I`: 12 synthetic messages, revision-bound export, registered
external output, and bundled empty compatibility registry passed. Package Python,
static assets and registry bytes matched the working tree; private roots were absent.

Wheel: `dist/wechat_export-0.2.0-py3-none-any.whl`
SHA256: `961f092fbcae8b6428210c4759d6482fa87bed6e5df9458e836530fb93611fa5`

`node --check wechat_export/static/app.js` and `git diff --check` passed.
Working-tree privacy rule scan passed with no findings; this is **not** a privacy
certification, a private-needle scan, or a clean-public-history claim.

## Remaining release gates

1. Actual UI-driven first-read on an eligible, exactly fingerprinted build, with
   fresh stage-specific authorization and human sample acceptance. The locally
   observed installed build 269631 is not automatically eligible because 269630
   previously worked. See [fingerprint delivery](fingerprint-reader-delivery.md).
2. Independent Mac/new-user acceptance, native chooser/TCC, real removable or
   physically distinct output volume checks.
3. Completed browser responsive, keyboard/focus and error-state acceptance.
4. Binary attachment recovery is not provided by this accounting increment;
   unknown/nested layouts need explicit adapters and further evidence.
5. Hosted CI execution and owner-reviewed publication/history remediation.

The compatibility registry stays empty. `source_kind=live-db` and
`backup2_coverage=unverified` remain unchanged. Do not present this increment as
“all versions supported”, “all attachments exported”, or “backup 2 decoded”.

## Final local results

- `.venv/bin/python -m unittest discover -s tests -v`: **305 passed**, 76.630 s.
  Local log: `/tmp/wla-accounting-final-full.log`.
- Synthetic disk-backed normalization → full outputs → index: **500,005 records**,
  **120.69 s**, peak process RSS **81.16 MiB** (baseline 31.03 MiB).
  Local log: `/tmp/wla-accounting-scale.log`. This is not a real-account or media
  recovery benchmark; target selection was disabled in this scale fixture.
- Independent installed-wheel smoke: `/tmp/wla-accounting-installed-smoke.log`.
- Working-tree rule audit: `/tmp/wla-accounting-final-privacy.json`; no findings,
  `privacy_certified=false`, no local private needles supplied.

The earlier full run failed an exact two-file slice expectation because companion
files were intentionally added. The test now requires all five files to exist
before atomic publication; the production safety assertion was not removed.
