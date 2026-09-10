> **Superseded implementation status:** See `guided-export-delivery.md` for the subsequent implementation and test evidence. The text below is historical review context, not the current list of code stubs. Real coordinator acceptance remains pending.

# Guided export implementation follow-up — 2026-09-09

## Scope and evidence

Code review and synthetic tests only. No real WeChat process launched, re-signed,
logged out, debugged, or decrypted in this follow-up. No private archive contents
or key files inspected. Existing working-tree work was preserved.

## Fixed

- Removed fabricated material-continuation state progression. Reuse now requires
  a matching snapshot manifest, live-db/unverified provenance on the manifest,
  matching provenance on every canonical record, a positive exact record count,
  and index construction. Missing/invalid exports block instead of reporting ready.
- Reuse stops at `awaiting_sample_check`; explicit user acceptance is required.
  It never claims new key acquisition or a new-user first read.
- Removed implicit UI preservation authorization (`checked || true`). Consent
  inputs must be JSON boolean true, not truthy strings. Snapshot selection is
  explicit rather than selecting the first listed snapshot.
- Restricted experimental environment candidates to 4.1.13 builds 269579 and
  269630. Neither is promoted to generally verified support.
- Missing LLDB blocks capture preflight. Key verification requires all three
  nominated contact/message databases; missing targets do not count as success.
- Cancellation is retained when a stale worker later saves ready state.
- Export source resolution rejects symlink escapes into prefix-sharing siblings.

## Verification

- Full suite: **105 passed**, 32.897 seconds.
- `node --check wechat_export/static/setup.js`: passed.
- Wheel built with isolated setuptools backend. Non-isolated build initially
  failed because the development environment lacks setuptools; not a code failure.
- Existing demo JSONL has an EOF blank-line diff warning; not changed here.
- No fresh-account or second-Mac acceptance is claimed.

## Release blockers (still not done)

1. Real first-read coordinator: `execute_key_capture` is still a stub. Connect
   snapshot, account/config preservation, debug-copy signing, bounded LLDB,
   user action, HMAC verification and owned-process cleanup with per-stage grants.
2. Snapshot-only continuation: connect authenticated decrypt/WAL merge,
   normalization, export and indexing as a genuine asynchronous job. Current
   continuation only validates/reuses an already produced canonical export.
3. Snapshot ownership: bind account, snapshot manifest and consent; opaque account
   selections alone must not establish ownership of historical snapshot material.
4. Snapshot consistency: fail closed on process/lsof errors; verify source before
   and after copying db/WAL/SHM and include helper processes.
5. Task lifecycle: persistent recovery after restart, duplicate-command exclusion,
   asynchronous material validation, bounded cancellation and progress reporting.
6. Installed demo and full installation: wheel contains static assets but demo
   still depends on a checkout; clean-machine dependency/install/HTTP smoke pending.
7. Message/media UI: real nontext type coverage, media availability accounting,
   pagination/large archive responsiveness and browser visual acceptance pending.
8. Explicitly supported build release acceptance on a fresh account/independent
   Mac, without loosening original-app/SIP/login boundaries.

The product is not ready to advertise a complete first-user guided export.
