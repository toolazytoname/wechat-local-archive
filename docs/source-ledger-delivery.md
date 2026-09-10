# Authenticated source ledger — 2026-09-09

Implementation continues the original T03/T04 completeness requirements. It does
not establish whole-history recovery, backup-2 coverage, or new real-reader success.

## Changes

- Added `wechat-canonical/1` schema version to newly serialized canonical records
  and manifest, and `wechat-source-ledger/1` to the new processing ledger.
- Inventory includes all snapshot `.db`, `.db-wal` and `.db-shm` paths/sizes/SHA-256,
  plus an aggregate hash. The inventory is rechecked before export publication.
  Changes during processing abort; original snapshot files are never modified.
- A ledger entry is allocated for every database. Success, failure, authenticated
  derived-index exclusion, and not-processed-after-abort remain distinguishable.
  Each decoded database has schema-role evidence; message tables carry source-row
  counts. Diagnostic messages omit chat bodies/key bytes.
- Removed filename-only FTS failure bypass. A decrypt/authentication exception now
  blocks publication regardless of filename. Only a successfully authenticated
  output with schema proven to contain exclusively SQLite FTS virtual/shadow
  tables can be classified as a derived-only exclusion after integrity failure.
  Unknown tables prevent that classification. This may reject older snapshots
  that were previously accepted using an unsafe filename assumption.
- Message discovery scans all decrypted DBs for actual `Msg_*` tables, including
  unfamiliar filenames and subdirectories. Full relative paths distinguish source
  identity and record UIDs; two same-content DBs in different paths don't silently
  collide. This changes newly generated UIDs compared with basename-only exports;
  existing archives are untouched and no cross-run deduplication is claimed.
- Output counts must equal recognized source message-table counts. Any discovered
  message source not processed or incompatible message table blocks the pipeline.
- Archive manifest links `source-ledger.json`, source snapshot aggregate hash and
  counts, and separates `records_complete` (recognized tables in this selected
  snapshot only), `attachments_complete=false`, `coverage_verified=false`.
- Slice manifests preserve available source lineage; missing older lineage remains
  null rather than being manufactured. Analysis projection preserves schema version.
- Removed per-message-table `fetchall()`; iteration uses the SQLite cursor. The
  overall collector still builds a record list: **bounded-memory end-to-end
  normalization remains unfinished**, and this change is not presented as its proof.

## Tests

Synthetic official-SQLCipher fixtures exercise successful ledger/schema/count
publication, bad-key behavior, corrupted FTS-named input rejection, real messages
under an FTS-like name, discovered extra message DBs, source mutation rejection,
and virtual/shadow-only schema classification. No real archive, key file, debug
copy, live capture, login or system protection change was involved.

## Remaining scope

Unknown auxiliary schemas are recorded, not certified as fully understood. The
recognized-table scope cannot prove that every future proprietary schema is
covered. Full incremental processing, memory bounds, detailed attachment availability
ledger, first-run UI/renderer/CI requirements and operator-confirmed real-reader
acceptance remain on the original goal.

## Verification results

Full suite: **161 tests passed, 36.529 s**. After ledger abort-status refinement, 12 focused source-ledger/pipeline/integration tests passed. Rebuilt wheel source bytes and private-path exclusion checked. SHA-256: `24ffd67edd42e82a8f721d711d4dbd097f7d736afdf16b7a3b444ce55e39d1ac`.
