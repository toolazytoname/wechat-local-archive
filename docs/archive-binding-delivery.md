# Source-bound HTTP export — 2026-09-09

This closes the cross-tab/account source-binding gap recorded in
`acceptance-audit-2026-09-09.md`. It does not close the other full-goal requirements.

## Reproduced before the fix

Four synthetic HTTP regressions failed: a missing binding was accepted, a stale
account-A tab could export account B, changing the canonical file did not
invalidate preview, and switching accounts while counting changed the worker's
source. Fixtures intentionally share conversation IDs, so ID validation alone
cannot protect account identity.

## Implemented contract

- Bootstrap/open-archive returns an opaque per-selection `archive_id` and a
  SHA-256 revision derived from the canonical messages, conversation metadata,
  manifest and completed SQLite index.
- Scoped HTTP requests require `X-Archive-ID`; media URLs include `archive_id`.
  Missing binding returns 409 `archive_binding_required`, another selection
  returns `archive_changed`, changed files return `archive_source_changed`.
- A request captures the immutable binding object once. It does not obtain
  `export_dir` again from the mutable active-archive setting in its worker.
- Index metadata records input SHA-256 hashes. Reopening a modified canonical
  archive rebuilds the derived index; partially changed inputs and nonempty index
  WAL are rejected. Build temporary filenames are unique.
- Workers copy the exact selected revision to a private 0700 temporary directory
  with 0600 files, verify each file hash and read only that private source. A change
  before/during copy fails; a change after the verified copy cannot change output.
  Temporary snapshots are cleaned up after ordinary success/failure/cancellation.
- The writer checks accepted preview count before publishing. Slice manifests
  retain `source_binding` identity/revision. These hashes identify the input
  archive; they are not proof of original WeChat history or backup-2 coverage.
- Viewer catches stale-source conflicts, returns to selection, and discards old
  in-flight API results. Switching scope during export cannot enable a second
  export button click. Captured jobs can reveal their own authorized output root
  even after another archive is selected.

## Evidence

- Full suite: **144 tests passed, 33.372 s**.
- Eleven focused binding tests, including HTTP two-account races, media/read
  rejection, pre-copy failure, post-copy source mutation, rebuild and count guard.
- Real browser with a synthetic archive: selected conversation preview/export,
  output file exists, count 1, manifest contains selected ID and 64-digit revision.
- JS syntax checked; no real account archive, live key capture, signing, login or
  original WeChat operations. Test server/tab cleaned up. No commit/push.

## Operational costs and remaining boundaries

- Opening/verifying hashes canonical data; exporting temporarily copies the
  index/canonical archive. This trades disk/time for a stable, verifiable input.
  Insufficient disk results in failed task, not a different source fallback.
- Cross-tab selection invalidates other tabs intentionally; it does not provide
  independently browsable per-tab server sessions yet.
- A process killed during copy can leave a private temporary directory. Explicit
  startup cleanup/retention policy is still to be implemented; no blanket deletion
  of pre-existing work directories is performed.
- Local external modification is detected by file identity/stat checks on reads
  and SHA-256 on capture/copy. Local hostile concurrent filesystem manipulation is
  outside this HTTP boundary's threat model.
- The CLI does not yet use the HTTP source snapshot contract; its same-QuerySpec
  behavior alone must not be called an immutable CLI preview/export handshake.
- Full lineage ledger, streaming normalization, first-run UI, richer renderers,
  independent clean install/CI and authorized real coordinator acceptance remain
  on the original goal. Keep developer-preview status until that evidence exists.

## Rebuilt wheel

`dist/wechat_export-0.2.0-py3-none-any.whl`; source bytes checked. SHA-256: `573c0f2e462ab19bbd6b862994b79fceffb485ab0a3351db74a85e9b97690e73`.
