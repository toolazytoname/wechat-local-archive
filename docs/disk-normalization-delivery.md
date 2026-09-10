# Disk-backed normalization — 2026-09-09

Continues the original T04 requirement: more than 500,000 source messages must
normalize, sort and export without a Python list holding the entire archive.

## Implemented

- `RecordStore` persists canonical records in a private SQLite spool, uses a
  4 MiB SQLite cache and disk temporary storage, and yields one decoded record at
  a time. Sorting is an on-disk composite index matching previous sort semantics.
- Guided snapshot processing and the trusted decrypted-source `export` CLI now
  explicitly pass that spool to the real collector. The legacy list-returning
  helper remains for compatibility/tests; it is not the guided/CLI export path.
- Self-sender inference uses a disk-backed distinct sender/peer table, preserving
  the previous explicitly-labelled inference instead of building all peer sets.
- Full archive JSONL and CSV are written record by record. Monthly Markdown uses
  at most 24 open files and never groups messages into per-month lists. Only
  contacts, conversation summaries, type/status counters and created filenames
  scale in Python memory; message count does not require a whole-archive list.
- Canonical JSONL preserves all raw fields. New streamed CSV/Markdown are readable
  analysis views, avoiding media XML/internal tokens. CSV cells get formula-safe
  escaping. Manifest records this mode; callers needing raw content use JSONL.
- Conversation Markdown directory suffixes use a digest of the full conversation
  ID to avoid collisions from truncated labels. Existing exports are not renamed.
- Guided source-ledger validation still compares input message-table counts to
  disk-store count. Source/schema/hash checks and sample-acceptance gates remain.
- CLI export stages files privately and publishes after success; exceptions clean
  the temporary tree and cannot register a partial final archive. Existing output
  directories and missing requested conversations are rejected, not overwritten
  or silently expanded to all.

## Evidence

Opt-in actual parser -> RecordStore -> full outputs -> archive index stress:

| Synthetic source rows | Baseline RSS | Peak RSS | Elapsed |
|---:|---:|---:|---:|
| 50,005 | 30.86 MiB | 71.88 MiB | 7.83 s |
| 500,005 | 31.00 MiB | 66.36 MiB | 99.81 s |

These are separate processes on this Mac. They are observations, not throughput
promises. The larger fixture did not grow a message-count-sized Python heap.
Both verified JSONL line count and indexed message count. The test's 256 MiB RSS
budget was satisfied. The harness creates/deletes only synthetic temporary files:

```sh
.venv/bin/python -m tests.performance.normalization_scale --extra-records 500000
```

Focused regressions cover byte-equivalent canonical records versus legacy
collection on fixtures, inference, cancellation checks, exact selected scope,
CSV formula/media XML safety, CLI staging failure and no-overwrite behavior.
The existing official-encrypted fixture pipeline/integration tests also run
through the disk-backed implementation.

## Boundaries

- The 500k stress starts from **synthetic decrypted databases**, not real chats or
  encrypted WeChat pages. Decrypt/HMAC helpers still materialize database bytes;
  it does not establish low memory for the complete encrypted acquisition path.
- Contacts/conversation metadata and individual messages can still be large.
  SQLite sorting and contacts loading are not every-step instant-cancellable.
- Spooling trades disk space and extra passes for bounded message memory. No
  performance or compatibility claim for an independent Mac is inferred.
- No real key capture, original WeChat operation, login, system-policy change,
  commit or push. Remaining first-run UI/renderers/CI/real-account acceptance
  and other audit items retain their original scope.

## Rebuilt artifact

Final full suite: **168 tests passed, 36.990 s**; wheel source bytes and private path exclusions were checked. SHA-256: `c16b5716d2816f45f9891fd3436bc7607519003805ab3a41e8533be5294f9c85`.
