# Destination and readable-filter acceptance — 2026-09-09

Continuation of the original guided-export scope, especially T02/T04/T06. This
work does not certify a real WeChat first-read or add a supported build.

## Actual mounted-filesystem evidence

`tests/manual/macos_volume_acceptance.py` is an **opt-in**, macOS-only script:

```sh
.venv/bin/python -m tests.manual.macos_volume_acceptance --run-synthetic-mounts
```

It creates only its own sparse APFS disk images and synthetic SQLCipher fixtures.
It never accepts a user disk/device argument. Each detach rechecks the OS image
association against the exact self-created image path and mount point. Cleanup
is attempted for all owned images, including a partial attach timeout. Temporary
synthetic evidence is retained; no real keys, databases or media are involved.

Seven checks passed on actual mounted APFS image volumes:

1. A 256 MiB volume has insufficient actual estimated free space; the pipeline
   refuses before creating decryption work or a final archive.
2. Detaching the selected volume makes its registered location unavailable.
3. Mounting a different image at the same path does not authorize the old token.
4. A 4 GiB sparse volume has a **different `st_dev`** from the local source/work
   directory. Real synthetic SQLCipher authentication → normalization → all/target
   outputs → index/coverage completes there (5 records), without copying the final
   archive back into the default output directory. External source registration
   resolves the produced archive.
5. Forced detach during the indexing stage causes failure rather than publication.
6. The detached path is not recreated as a fallback export on the host filesystem;
   failure ledger says failed/incomplete; encrypted fixture hashes remain unchanged.
7. Reattaching the image preserves the previous completed export and does not show
   an incomplete export as a completed manifest.

Final log: `/tmp/wla-volume-final.log`. All owned images were detached afterward.
This is **actual cross-filesystem/image-unmount testing**, not a USB cable/power-loss
or physically distinct external-disk test. `physical_external_disk_tested=false`.

## Native chooser: attempted, not passed

Called the real fixed `choose_native_folder()` bridge with an intended private
synthetic destination. The native-control surfaces did not expose an operable
chooser; the real subprocess reached its bounded timeout and returned
`picker_timeout`. No location was registered, no TCC permission was changed, and
no selector success/cancel was fabricated. Successful native selection, actual
user cancellation, and the TCC permission matrix remain unverified.

## Missing T04 requirement implemented

The original plan explicitly requires showing how many messages a readable-only
export excludes. Previously the UI/API gave only the selected count.

- New `selection_accounting` records candidate count **within the same conversation,
  time and message-type restrictions**, selected count, unreadable-exclusion count,
  readability toggle and schema/scope. It does not compare a one-chat slice with
  every message in the source archive.
- HTTP and CLI preview report these counts. The browser shows both selected and
  excluded totals. CLI/server exports use the same canonical presentation predicate.
- Every slice format, root full export and configured-target export gets accounting
  in its manifest and coverage sidecars. Legacy archives can still have unknown
  coverage rather than a forged zero.
- Canonical slice accounting is streamed during export; it does not assemble an
  extra message list or make an extra full canonical read. Attachment accounting
  describes selected messages, not records that were excluded.
- Cancellation is checked while scanning records that produce **no output**, both
  because all candidates are unreadable and because no scope/time/type matches.
  Such an export no longer depends on eventually yielding a selected record before
  checking cancellation.

## Verification

- Full suite: **311 tests passed, 66.411 s**
  (`/tmp/wla-volume-selection-full.log`).
- Focused tests cover both canonical/index sources, four formats, readable on/off,
  conversation/date/type restrictions, zero matches and cancellation without yields.
- Real HTTP preview and asynchronous slice export agree on 4 candidates / 3 selected
  / 1 excluded for the four-record fixture.
- Real Chromium test, four viewport sizes: readable-only preview shows **12 candidates,
  6 selected, 6 excluded**; clearing it restores 12. Four-format UI export still passes
  (`/tmp/wla-selection-browser.log`). Only fictional demo data was used.

## Remaining original-goal gates

- Eligible-build real UI-driven first-read with fresh exact stage authorization;
  then independent Mac/new-user evidence. No registry entry was added for 269631.
- Successful native chooser/TCC and physical removable-volume acceptance.
- Hosted CI execution and owner-reviewed public history/publication remediation.
- Platform/accessibility and media recovery limitations remain as documented in
  [browser delivery](browser-ui-delivery.md) and
  [attachment accounting](attachment-accounting-delivery.md).

No commit, push, history rewrite, account login/logout, App re-signing, new key
capture, source restoration or data upload occurred. The original goal remains
active: tests alone are not completion. Source stays `live-db`; backup 2 stays
`unverified`.

## Final package and cleanup evidence

- Fresh independent virtual environment, outside-checkout installed `python -I`
  smoke passed: 12 fictional records, bound export and registered external output
  (`/tmp/wla-volume-selection-installed-smoke.log`).
- Wheel SHA256: `71257f4e1461dad5bb774f2fdc28de5c1ebe2c118dd84eeb0333cc013d467a54`.
  All packaged Python/static/registry bytes matched the working tree; no private
  roots or tests packaged. Artifact: `dist/wechat_export-0.2.0-py3-none-any.whl`.
- Current working-tree privacy rules passed without findings. This is not public
  history certification and used no private identity-needle file.
- OS image inventory confirms no owned test images remain mounted. Browser-test
  servers/browsers exited; the real chooser subprocess terminated on timeout.
- All three frontend script syntax checks and `git diff --check` passed.
