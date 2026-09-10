# CLI source safety and synthetic CI — 2026-09-09

Status: developer preview, not full-goal completion. No real WeChat, keys, or
private archives were operated on in this delivery. No commit/push performed.

## Implemented and locally verified

- `slice` captures the canonical/index/manifest SHA-256 binding, checks it after
  counting, and exports from a private verified temporary copy. Output remains
  outside the temporary copy; normal success/failure cleans the copy.
- Preview exposes `source_binding.source_revision`; passing `--source-revision`
  to a later slice rejects a changed source. Without that flag a new invocation
  selects the current source, rather than claiming to reuse an earlier preview.
- Copy-time source mutation aborts. Mutation after verified copying cannot alter
  output. Publication checks expected count. Regression tests exercise both races.
- Legacy WeChat-specific lsof inspection has a timeout and rejects failed,
  warned, empty, or wrong-PID responses. Malformed pgrep output is not idle proof.
  This does not establish absence of every possible non-WeChat writer.
- Added `.github/workflows/synthetic.yml`: read-only permissions, pinned action
  commits, macOS synthetic tests, wheel build, independent venv install smoke.
  No artifact upload or repository secrets. **GitHub execution has not happened**;
  hosted-runner LLDB behavior still needs evidence after owner-approved publication.
- `scripts/installed-smoke.py`: isolated Python, no checkout imports, temporary
  bundled demo, revision-bound preview/export and provenance assertions.

## Evidence

- Full suite: **184 passed**, 39.274 seconds (`/tmp/wla-source-safety-full.log`).
- Focused slice/inspection suite: 12 passed.
- JavaScript syntax and `git diff --check`: passed.
- Fresh separate venv installed the rebuilt wheel and dependencies. Outside the
  checkout, `python -I scripts/installed-smoke.py` passed with 12 synthetic records.
  Initial smoke path assertion encountered macOS `/tmp` symlink canonicalization;
  fixed by comparing resolved paths and reran successfully.
- Wheel `dist/wechat_export-0.2.0-py3-none-any.whl` SHA-256:
  `7ec1458908aea3bb862bc75fa71f639ff8e3291660e835ff3474aa31f5f37abb`.

## Not completed by this change

Full first-run destination picker/dependency UX, fingerprint-bound support,
coordinator failure-stage matrix and new authorized real-account acceptance,
independent Mac acceptance, detailed attachment accounting, encrypted DB streaming,
crash retention cleanup, comprehensive UI E2E, and exhaustive public-tree/history
privacy audit remain on the full acceptance table. Backup-2 remains unverified.

Subsequent delivery: `scratch-retention-delivery.md` supersedes the temporary-file/crash-retention gap listed above for newly managed disposable work. Persistent retry data and unmarked legacy directories remain intentionally retained.
