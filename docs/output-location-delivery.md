> **2026-09-09 destination/filter update:** [Actual APFS cross-filesystem tests and readable-exclusion accounting](destination-and-filter-delivery.md) passed; native chooser timed out and is not certified. Physical external-disk and real first-read gates remain open.

# First-run output locations and destination-local publication — 2026-09-09

## What changed

The setup screen now offers a saved-location selector and a macOS native folder
chooser. The chooser runs a **fixed** AppleScript from a CSRF-protected POST; the
request cannot supply a filesystem path or script. Cancelling preserves the
selection. The dialog has a 180-second bound and only one chooser runs at a time.
No dependency installation or WeChat operation is triggered by choosing a folder.

The chosen folder receives a private `WeChat Local Archives` child. Existing
incompatible permissions/symlinks are rejected rather than chmodding the user's
folder. It also receives an ignore-all `.gitignore`: exported data is ignored even
inside a different Git checkout. The repository privacy guard rejects this named
private root if force-tracked. This is not protection against `git add -f` or a
cloud-sync application; the UI warns against cloud/shared folders.

Locations are registered by opaque IDs in private 0600 state under the runtime's
private directory. Device/inode identity is checked on use. An offline, replaced,
or unknown location is not recreated or silently redirected. A read job stores
its destination ID **and** identity when created. Later UI selection affects a
new job only. Live preparation checks this binding before environment/reader
operations; continuing a snapshot checks it before output processing.

Completed external archives are enumerated/opened through `external:<token>:<run>`
source IDs. Browser paths are still rejected. The same archive-binding/revision
rules apply once opened; slices stay inside the selected archive's `slices/`.

## Full archive publication

`ArchivePublication` creates an exclusive empty final reservation and uses managed
`full-archive` scratch **on the selected destination filesystem**. Canonical files,
manifest, source ledger and index are built there, then the complete archive tree
is renamed into the checked empty reservation. It does not rename a working tree
across volumes or fall back to a partially visible recursive move. Existing or
occupied targets are not overwritten/deleted. A killed builder leaves an empty
final reservation, not a partial final archive; the owned scratch remains eligible
for the existing lease/age cleanup. Final archives and persistent retry work are
not cleanup targets.

Encrypted snapshots, keys, decrypted retry databases and normalization work remain
on the local runtime disk. Selecting external storage **does not relocate them**.
The UI states this explicitly. Destination staging replaces the previous
`work/<run>/staging` export path; source snapshots are unchanged.

## Capacity and prerequisites

- Metadata-only account/database size estimates distinguish work and output
  budgets, and combine them when the filesystem identity matches.
- Current heuristic: six times source size plus 1 GiB for each budget. This is a
  planning estimate, **not an expansion upper bound or a free-space reservation**.
- The authenticated pipeline checks the estimate before decryption and refuses
  insufficient estimated capacity. The first-run UI separately warns that App
  copies/configuration preservation require additional space.
- Different APFS volumes may share underlying container capacity. Device/inode
  checks do not prove independent physical capacity; disk-full failures remain
  possible from expansion, concurrent writes, shared storage or unplugging.
- The environment panel explains the Apple command-line tools and SQLCipher
  installation commands, links to official sources, and offers re-detection.
  Commands are displayed, not executed. Existing-archive viewing needs neither
  LLDB nor SQLCipher. No sudo/SIP or container-permission change was added.

## Runtime isolation correction

A synthetic browser check exposed an old implicit scan of a neighboring
`ops/wechat/work` directory. The scan only surfaced snapshot metadata here; no
message body/key was read, exported or uploaded. The implicit fallback is now
removed and regression-tested. Old investigation materials are available only
when the operator explicitly sets `WECHAT_EXPORT_OPS_ROOT`; no originals were
moved or deleted. The second browser run showed no unrelated snapshot entries.

## Evidence

- Full current suite: **260 passed**, 46.084 s
  (`/tmp/wla-output-final-full.log`).
- New suites: `test_output_locations.py`, `test_archive_publication.py`,
  `test_output_http.py`; extensions to real-codec pipeline, materials and installed
  smoke tests.
- Tests cover private registration/persistence, duplicate selection, raw browser
  path/CSRF rejection, offline/replaced destinations, per-job binding, external
  archive opening, Git ignore behavior, shared-filesystem budget accounting,
  cancellation, occupied targets, and **actual child-process kill** before publish.
- Real synthetic SQLCipher-to-export fixture passes into a selected external
  directory; a rename guard emulates EXDEV if staging is outside that destination.
  This is **not** a physically distinct/removable-disk test.
- macOS `osacompile` compiled the fixed chooser script without opening/executing
  a chooser. UI tests stub the native result; a real native selection/TCC matrix
  remains unverified.
- Browser: synthetic selection → account selection → visible capacity plan →
  job-bound destination. A native-cancel stub also worked. After resizing to
  390×844, the browser automation host timed out, so narrow-screen screenshot,
  overflow and complete keyboard acceptance are **not claimed**. Alternate CUA
  reported no available browser. Both owned browser tabs and synthetic servers
  were closed; no real WeChat process was launched.
- Fresh venv, independent dependencies, outside-checkout `python -I` smoke:
  **12 synthetic records**, revision-bound export and registered external output.
- Wheel source-byte verification checks every Python/static member against the
  worktree and excludes private runtime/test/wiki roots.
- Current worktree privacy rule scan, both JS syntax checks and git whitespace
  checks passed. Reachable public-history identity findings are **not resolved**.

Wheel: `dist/wechat_export-0.2.0-py3-none-any.whl`

SHA256: `772e3d8c5161dd75bd7ba95b7d673e9199addf7834e07aa702ba2f9706ee556e`

No commit, push, historical rewrite, real key acquisition or official-App change.
`source_kind=live-db`; `backup2_coverage=unverified`.

## Still required for the original goal

Exact module-fingerprint compatibility evidence and full coordinator failure
coverage; controlled real first-read and independent-Mac acceptance; attachment
and unknown-schema accounting; complete keyboard/responsive/error acceptance;
owner-approved release/history remediation. This delivery does not redefine the
product goal as passing synthetic tests.
