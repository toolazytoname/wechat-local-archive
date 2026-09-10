# Fingerprint-bound compatibility and reader gates — 2026-09-09

## Observed current installation (read-only)

On **September 9, 2026**, the installed `/Applications/WeChat.app` reported
**4.1.13 / build 269631**, not the earlier 269630 mentioned in historical capture
reports. Deep/strict signature verification succeeded. The new inventory completed
in **2.81 seconds**, covering **177 Mach-O modules and 922 total entries**.
This was filesystem/signature inspection only: no debug copy, LLDB launch, login,
key capture or actual chat export was performed by this continuation.

The build was not added to the experimental allowlist. Current candidates remain
4.1.13 builds 269579/269630 on arm64; the shipped reviewed-support registry remains
**empty**. Existing-archive viewing/export is independent of installed-build
eligibility. Do not infer when/how the installed app changed from this observation.

## Bundle identity

`build_fingerprint.py` fingerprints the complete bundle, including nested Mach-O
modules, other regular resource files, directory names, executable flags and
internal symlink targets. Paths in the inventory are bundle-relative; relocation
of an otherwise identical tree preserves identity. The summary has its own schema,
aggregate SHA-256, module/file counts and Info.plist version/build/bundle ID.

- 1 MiB streaming reads; limits on entry count, total bytes, traversal depth and
  elapsed time, with a cancellation callback.
- File descriptors open regular files through no-follow directory components.
- External/broken symlinks and special files fail closed.
- Before/after file stamps and a repeated tree inventory reject changes during
  inspection. New/removed files and non-code resource changes affect identity.
- No fingerprint cache is reused across prepare/launch observations.
- A content hash is **not** code-signature trust. Original signature verification
  remains separate, and exact app observations are not locks against an updater.

## Reviewed support registry

`compatibility-registry.json` is included in the wheel. `compatibility_registry.py`
requires matching version, build, architecture, macOS version, fingerprint schema,
full bundle SHA-256, and declared reader/codec/parser version IDs. A build string
or legacy `VERIFIED_BUILDS` entry alone cannot enable reviewed support.

A release-reviewed entry needs at least two distinct environment IDs, evidence
IDs and report hashes, with explicit non-synthetic first-read and human-sample
attestations and all three key/codec/export stages true. These are **maintainer-
reviewed declarations**, not cryptographic proof of an actual person or independent
machine. Before populating the registry, a release reviewer must inspect the
underlying real reports and verify their hashes/independence. Changing reader or
codec implementation requires a version-ID review and new evidence; IDs do not
hash every installed dependency executable.

The tests use hypothetical attestation shapes **only as fixtures**. No hypothetical
entry is in the shipped registry. A matching local report cannot populate published
support stages. Local reports are read only from an explicitly supplied runtime
private root; old unbound reports and implicit source-checkout paths are ignored.
The UI now labels these as public real-machine acceptance, not this job's progress.

## Prepare → wait → capture → export coordinator

- Preparation binds the observed code/system/reader identity to the job. After
  preparing the copy, its report must still refer to that observed original hash.
- The original and debug copy each receive full-bundle identity checks. Old
  main-executable-only preparation receipts cannot authorize a launch.
- Launch requires the same prepared environment binding, preflight, complete
  consents and fresh stage confirmation. A dispatch-attempt marker prevents
  restarting a previous capture as if it were unattempted.
- Snapshot contracts are checked before entering copy preparation. Cancellation
  between snapshot/capture boundaries prevents later operations/registration.
- Captured output requires explicit HMAC/breakpoint/database-count success and
  an owned regular 32-byte private key file. Missing or permissive files are not
  registered. The real codec pipeline still authenticates the snapshot again.
- Completing capture sets `live_key_acquisition_completed`, **not** an inferred
  new-user-first-read certification. Human sample acceptance is still separate.
- Interrupted-job recovery revokes the live grant. Persistent snapshots and
  candidate keys are retained, not silently purged or restored over originals.
- Known failure codes have actionable UI descriptions. Unknown exception text is
  excluded from public errors and local diagnostic JSON, which stores a type/code.

## Signing entitlement audit

`prepare_copy` reads entitlements from original bundle objects, not from an
already modified copy. Unreadable/invalid declarations are errors, not empty
permission sets; XML and binary plists are supported.

Only the root copy/main executable and the named WeChatHelper app/main executable
with the expected helper bundle ID can receive the approved library-validation
exception. An unrelated framework binary merely named WeChat is not eligible.
All other original entitlement values are preserved. After signing, every signed
target's effective entitlements must match the expected values. Deep copy signature
verification and original identity comparison are prerequisites for the final
prepared receipt. `signing-audit.json` records relative targets and entitlement
hashes, not key bytes or chat contents.

These command-scope/entitlement tests mock signing; they are not a real AMFI/TCC
acceptance of the newly written coordinator on 269631 or any other build.

## Source file occupancy

Strict snapshots now run bounded `lsof` checks against the actual source trio
paths across **all visible processes**, before and after copying. A non-WeChat
process holding the DB also blocks. Failure/timeout/stderr/ambiguous output is not
classified as idle. Source/destination hashes and WeChat-exit checks remain.

This is not a kernel-wide exclusivity lock or a claim that every privileged
process is observable. Another writer can open a file between observations;
source-before/after plus destination hash equality remains necessary. No sudo,
permission changes, original checkpoint or process-name-based kill was added.

## Verification

- **296 tests passed**, 73.783 s (`/tmp/wla-reader-fingerprint-final-full.log`).
- New suites: bundle fingerprints (including nested changes and ancestor-symlink
  swaps); hypothetical registry policy; **17 coordinator contract tests**;
  **6 copy/signing identity tests**; **4 occupancy tests**.
- Real `lsof` tests use synthetic files held by a non-WeChat process and a handle
  opened during copying; they verify that no successful snapshot report appears.
- The coordinator fixtures deliberately simulate signer/capture boundaries and
  do not substitute fake HMAC success for the real primitive suites. Existing
  actual synthetic SQLCipher/KDF and encrypted-snapshot-to-export suites also ran.
- JS syntax, git whitespace and current-worktree privacy-rule checks passed.
- New wheel compared byte-for-byte against every Python/static/registry member;
  private runtime/wiki/test roots were absent from the wheel.
- Fresh venv, independently installed dependencies, outside-checkout `python -I`:
  bundled registry was found/valid/empty; **12 synthetic records**, revision-bound
  export and registered external output passed.
- No new browser visual/keyboard acceptance is claimed this turn.

Wheel: `dist/wechat_export-0.2.0-py3-none-any.whl`

SHA256: `44016baf093a7d84236d0815ae829c99320bc4b09b13033ab20d59e5880c7ad1`

No commit, push, history rewrite, real WeChat signing/launch or account operation.
`source_kind=live-db`; `backup2_coverage=unverified`.

## Remaining original-goal work

- Detailed attachment and unknown-schema coverage/accounting in every export.
- Full responsive/focus/keyboard/error acceptance; real native-picker/TCC and
  physically distinct removable-volume acceptance.
- Controlled real coordinator first-read on a specifically eligible build and
  independent-Mac/new-user acceptance, with fresh exact authorization. The
  installed 269631 is currently **not** an eligible candidate.
- Owner-approved release/hosted CI and remediation of known historical personal
  metadata. Current worktree rule success is not a clean-history claim.
- Long-running operations still have bounded/cooperative cancellation, not an
  instantaneous cancellation guarantee at every OS/SQLite/copy boundary.
