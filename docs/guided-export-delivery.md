# Guided Mac export — implementation delivery, 2026-09-09

## Status

**Experimental implementation, not independently verified general support.**
Explicit candidates: Apple Silicon, WeChat 4.1.13 builds 269579 / 269630.
`verified_builds` remains empty. New real-account capture with this coordinator
has not been executed in this delivery; previous manual-machine success does not
prove the new coordinator. No original WeChat operations were performed here.

## Implemented paths

1. Registered archive reuse: snapshot/account confirmation, canonical identifiers,
   row provenance, record counts, index build, explicit human sample acceptance.
2. Encrypted snapshot processing: private local passphrase file, official codec,
   authenticated decryption and WAL merge, required DB failures block publication;
   derived FTS failures are recorded separately; normalization, JSONL/CSV/monthly
   Markdown and SQLite index are published only after success into unique paths.
3. Experimental live reader: task-bound expiring authorization, exclusive live
   operation lock, environment/signature/tool/free-space gates, source/copy hashes,
   db/WAL/SHM idle snapshot, small account-state preservation, copy-only inside-out
   ad-hoc signing, explicitly authorized library-validation exception. A **second
   button press** launches capture. The user clicks 进入微信 manually if needed.
   Capture waits at most 120 seconds for the KDF stage, authenticates candidate
   bytes on contact and message DBs, stores a per-job 0600 key, then uses path 2.
4. Jobs: asynchronous workers, cross-process job leases, duplicate rejection,
   cancellation retained over stale saves, interrupted-running detection. Recovery
   marks an interrupted job blocked; it does **not** automatically resume debugging
   or roll back account files. Retry creates a new job.
5. Viewer/export: full/current/group/private/multiselected conversations, date
   interval [start,end), message-type filter, JSONL/CSV/Markdown/offline HTML,
   counts, progress, cancel and Finder reveal. Nontext messages use cards and
   unavailable-media placeholders; no promise of complete media recovery.
6. Distribution: bundled synthetic demo copied to writable runtime storage,
   wheel static assets, install/launch `.command` scripts, runtime data outside
   site-packages. macOS dependency installation needs Python 3.11+ and network.

## Running

From this checkout:

```sh
.venv/bin/python -m wechat_export launch
```

Or execute `scripts/install-macos.command`, then on subsequent launches
`scripts/launch-macos.command`. These scripts do not use sudo or change system
security policy. If the preferred port is occupied, use the printed loopback URL.
An already running old server does not reload newly changed Python code.

## Operator flow

- For an existing archive, open it and export directly; do not recapture keys.
- For a snapshot, select account **and** snapshot, explicitly confirm ownership
  and preservation, then continue. Legacy unbound snapshots need human ownership
  confirmation. Files do not authenticate the human's identity.
- For experimental fresh reading, select the account, read every consent, quit
  WeChat manually, and press prepare. Only once preparation succeeds, separately
  press launch/capture. If QR/login is requested, cancel; no automatic scan/login.
- After export, review sender names, times, text and nontext placeholders; return
  to the task using “读取任务 / 切换档案” and explicitly accept the sample.

## Evidence collected in this delivery

- Full unittest suite: 121 passed (59.530 s in the final recorded full run).
- Official SQLCipher-encrypted fixture -> actual workflow -> published archive ->
  sample acceptance; wrong-key, cancel, source hash equality and no-overwrite tests.
- Native LLDB probe/idle regressions in the full suite are synthetic programs,
  **not** a real WeChat launch.
- Installed wheel outside checkout: demo, static assets, launch and browser UI.
- Browser smoke: preview 9 synthetic messages, export 9 to offline HTML, navigate
  back to setup. No real chat content was displayed or uploaded for verification.
- Synthetic streaming writer: 500,000 records verified in 3.09 s; cancellation left no published or temporary output. This is a writer-only stress test, not full normalization.
- JS and shell syntax checks passed. Wheel builds with isolated setuptools backend.

## Boundaries that remain

- Real end-to-end capture/signing behavior of the new coordinator still needs
  explicit operator execution. Independent Mac/fresh-account acceptance is pending.
- Full extraction of encrypted/missing media is not implemented. Source remains
  `live-db`, `backup2_coverage=unverified`, media coverage explicitly incomplete.
- A debug copy shares account storage. Keychain, TCC, memory and the whole media
  tree are not a rollback image. No automated restoration is attempted.
- Cancellation is checked between DBs/record phases and during KDF waits. A bounded
  subprocess already doing signing/copy/decrypt can finish before cancellation.
- Normalization currently collects selected-source records in memory; the streaming
  slice writer's capacity does not prove million-record normalization memory bounds.
- No macOS app notarization/DMG, arbitrary builds, Intel/Windows or RMFH recovery.
- Self direction may be inferred only from a unique sender across multiple private
  peers when no explicit identity is known. The manifest records that inference;
  unknown identity is not silently equated to someone else.

Diagnostics stay local in job/work directories and omit chat bodies and key bytes.
Nothing was committed or pushed in this delivery.

## Build artifact

`dist/wechat_export-0.2.0-py3-none-any.whl` (106,150 bytes).
SHA-256: `feaa83bb5dbf4861e54a7f2d63443e5ce727ade7c2b8a34d4977fa6d2242c548`.
Final wheel source bytes were checked against the working tree; no data/wiki/tests or raw secret files were packaged.

## Updated artifact after analysis/export audit

The wheel was rebuilt after the fixes in `acceptance-audit-2026-09-09.md`.
Current SHA-256: `58df50c6cc2355d1df87e1adf315973bac80033a712bde668711e938b1c74208`. Source bytes and exclusion of data/wiki/raw-secret files were checked. Earlier artifact hashes above describe earlier builds.
