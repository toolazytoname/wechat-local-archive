# Crash-safe disposable data and publication — 2026-09-09

Status: implemented for newly registered disposable work. This does not complete
real-account/independent-Mac acceptance or authorize deletion of old investigation
materials. No real WeChat, key, chat archive, or backup was used in this delivery.

## What is now managed

`wechat_export.scratch.ScratchSpace` wraps these seven paths:

| Disposable work | Parent used by production flow |
| --- | --- |
| HTTP immutable export source copy | runtime `jobs/` |
| CLI immutable slice source copy | selected archive directory |
| SQLCipher private input trio + plaintext staging | guided flow: shared runtime `work/`; standalone API: output parent |
| Main-file page decrypt staging | guided flow: shared runtime `work/`; standalone API: output parent |
| CLI disk normalization + full-export staging | runtime `work/` |
| Filtered slice data + manifest staging | selected archive's `slices/` or explicit CLI output root |
| Derived archive index construction | index parent |

Ordinary exit removes only the context's own disposable entry. Server startup
checks registered namespaces in runtime `jobs/` and `work/`; archive activation
also checks the selected archive and its slices. New work checks its own parent.
Standalone API/custom output parents can be inspected explicitly with the CLI;
there is **no recursive scan of arbitrary filesystem locations**.

## Conditions for recovery

- A fixed private `.wla-scratch-v1/` namespace with an exact owner/schema receipt.
- Entry names are generated UUIDs; entry/receipt/lease ownership and permissions
  are checked. Receipt identity must match the actual directory device/inode.
- Purpose is explicitly allowlisted and the receipt declares `payload-v1` layout.
- **At least 24 hours old** for automatic cleanup; timestamps must be finite and
  not in the future. Manual CLI retention cannot be less than one hour.
- An exclusive nonblocking **kernel file lease** must be obtainable. A timestamp,
  PID file or job state alone never means a process is dead.
- SQLCipher inherits the lease FD. A surviving CLI child keeps cleanup blocked
  even after the parent loses its FD. Tested with the actual installed SQLCipher
  CLI waiting for input, not just a mocked child.
- Descriptor-relative, symlink-resistant deletion is required; no unsafe fallback.
  Payload symlinks are unlinked, not traversed. Unexpected control files or a
  different-device top-level payload are not recursively removed.
- Payload lives separately from receipts/leases. Payload deletion happens first;
  an I/O interruption leaves the receipt/lease available for a later retry. Control
  files are removed last. A cleanup error does not replace the original job error.
- Namespace initialization is briefly locked so simultaneous first-use jobs do not
  read half-written registration. Lock acquisition is bounded; jobs have separate
  leases and do not hold the initialization lock while exporting.

Each pass processes at most 128 eligible entries. Busy/recent/unknown entries do
not consume that eligibility quota. The bootstrap response exposes counts only;
the setup screen reports recovered entries and retained unresolved entries.

## What is deliberately NOT removed

- Original WeChat files, raw backups, authoritative snapshots and retained keys.
- Completed exports/slices and persistent `work/<run>/` retry/audit materials.
- Old `export-source-*`, `normalize-*`, `.building-*`, `.decrypt-*`, SQLCipher temp
  prefixes, or other directories without this version's matching receipts.
- Entries with missing/invalid receipts, changed identity, unknown layout, foreign
  ownership, unsafe permissions, or unavailable leases.
- Empty UUID output reservations left by a process dying before publication, plus
  empty namespace/init-lock bookkeeping. They contain no staged chat payload.

A receipt is a local ownership convention under the current user's private
permissions, not a cryptographic defense against malicious code already running as
that same user. Files placed inside the managed **payload** are disposable; never
store retained keys or unique source material there. Recovery is ordinary filesystem
unlinking, **not forensic secure erasure** of SSD blocks, snapshots or backups.

## Publication and source accounting fixes

Filtered output data and its manifest are written inside private leased staging.
The complete directory atomically replaces only the job's own empty UUID reservation.
Changed/occupied reservations abort; exported files are 0600 and directories 0700.
A killed writer cannot expose a half-written final JSONL/CSV/Markdown/HTML.

Index construction is also privately staged; a killed rebuild leaves the previous
index intact. Expected-count checks, canonical source hashes, source binding,
`source_kind=live-db` and `backup2_coverage=unverified` remain enforced.

Unreclaimed scratch DBs under a proposed decrypted input tree are **rejected**, not
silently exported as extra message databases or excluded by a guessed filename.
Snapshot source inventory rejects scratch databases too.

## Operator commands

```bash
# Counts only, dry-run; this is a PARENT of the fixed namespace, not a deletion target.
python -m wechat_export cleanup-scratch --parent /path/to/runtime/work

# Explicitly apply the same ownership/lease/age checks; no blanket directory removal.
python -m wechat_export cleanup-scratch --parent /path/to/runtime/work --apply
```

Use the actual parent from the table, not `data/` expecting a recursive purge.
Deletion errors produce a nonzero CLI status. Unknown/legacy entries stay in place;
inspect and separately authorize any manual handling instead of using `rm -rf` on
all similarly named directories.

## Evidence

- Actual child process killed during disposable work: lease releases and recovery
  succeeds. Live parent and surviving inherited-FD child both prevent recovery.
- Actual slice writer killed after staging data: no partial final file; stale payload
  recovered, canonical source retained. Completed output is never collected.
- Actual index builder killed before replacement: old index byte hash unchanged;
  stale staged index recovered and a subsequent rebuild succeeds.
- Simulated interruption partway through payload deletion: receipt/lease preserved,
  second recovery succeeds. FIFO/deeply nested receipts do not hang/crash the sweep.
- Symlink targets, unknown/legacy paths, forged identity, future dates, occupied
  output reservations and persistent retry materials preserved in regression tests.
- Browser: isolated synthetic archive, environment probe replaced with synthetic
  unsupported data, startup recovery notice for one expired simulated entry. UI
  preview/export both **12**, actual JSONL/manifest count **12**, all managed payload
  entries removed afterward. Console empty; browser resource entries only loopback.
  Owned browser tab/server closed. No real account discovery or key capture.

Remaining: full first-run destination/dependency UX, coordinator failure matrix,
attachment accounting, full UI acceptance, authorized real capture/independent Mac,
and owner-approved historical privacy remediation remain on the main acceptance
report. Cleanup does not turn those missing requirements into completed work.

## Final local verification

- **238 tests passed**, 43.034 s (`/tmp/wla-scratch-final-complete.log`).
- Repeated final 192 MiB official synthetic fixtures: main Python peak **30.00 MiB**,
  integrity OK; WAL Python peak **31.12 MiB**, child-process high-water **10.75 MiB**,
  committed payload count and integrity OK. The child metric is not the sum of the
  process tree and is not labelled as exclusively SQLCipher in non-WAL runs.
- Final wheel installed with dependencies in a new isolated venv, outside checkout:
  `python -I` smoke exported all **12** synthetic records with matching preview,
  source revision and provenance. No developer packages reused.
- Wheel SHA-256: `5f2bd9e3e4bc0829921aa7d6099f1f770556d0634c036121a88fac1d730e9b5c`.
- JavaScript syntax and Git whitespace checks passed. Current public-tree rule scan
  remains clean; the previously reported historical identity findings are unchanged.
- No actual WeChat/backup/key operation, no commit/push, no history rewrite.
