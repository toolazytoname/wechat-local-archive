# Consent-gated installer and local preview bundle — 2026-09-09

Original T06 requires a usable bootstrap/launcher, confirmed dependency installation,
independent installed execution and repeatable packaging. The old installer directly
ran pip and reused the fixed `venv` directory. This increment closes those specific
implementation gaps; it does not certify real WeChat first-read or a public release.

## Installer behavior

`scripts/bootstrap.py` uses only the standard library before installation.
`scripts/install-macos.command` and `scripts/launch-macos.command` remain the user
entry points; [install-guide.md](install-guide.md) is the operational guide.

- Describes local source, installation root and possible dependency downloads.
  Interactive `yes` or explicit `--yes` is required. Noninteractive default declines
  without creating the installation directory or starting subprocess installation.
- Requires Python 3.11+. No sudo, Homebrew/SQLCipher/Xcode installation, system
  protection changes, account operations or source archive access.
- Creates a private unique environment at its **final** `versions/<id>` path;
  venv directories are never relocated. Keeps old environments and legacy `venv/`.
- Installs with isolated Python/pip, runs dependency checking and a synthetic
  installed-package smoke (assets, registry, canonical/index, 12-record export).
  Smoke runtime data is private to that version, not a user's existing archives.
- Atomically activates a ready version via `current` only after successful checks.
  Failure/interrupt leaves a private inactive receipt/log and retains the old version.
  An interruption after the activation commit point does not downgrade a ready
  receipt to failed.
- Nonblocking installation lock serializes cooperating updates. Invalid pointers,
  occupied files, unsafe directory/receipt/lock permissions and changed wheel input
  fail closed without deleting or chmodding the conflicting objects.
- Confirmed rollback selects the previous ready installation or preserved legacy
  entry. It never removes versions or archive data. There is no automatic version
  garbage collection or automatic termination of an already running server.
- Offline mode accepts local wheels only, disables index and dependency resolution,
  explicitly installs local wheel paths, then checks dependencies. It does not turn
  a missing/incompatible dependency into an online retry or run source build hooks.

## Real offline installer evidence

`tests/manual/bootstrap_acceptance.py` is opt-in and uses only an owned temporary
installation root and pre-existing local compatible wheels. Its subprocess checks
are real, unlike the separate mocked state-contract tests.

```sh
.venv/bin/python -m tests.manual.bootstrap_acceptance \
  --wheel dist/wechat_export-0.2.0-py3-none-any.whl \
  --wheelhouse /path/to/compatible-wheels --run-isolated-install
```

Passed:

1. Default declined installation writes no installation directory.
2. First real installation reaches ready and exports 12 synthetic records in smoke.
3. A deliberately invalid wheel fails without changing the active version.
4. A second real installation activates at a new path, retaining the first.
5. The installed `bin/pip` shebang executes successfully and points to its actual
   environment; the launcher works from outside the checkout.
6. Rollback restores the first entry and preserves both environments and a
   synthetic private retention marker.

Log: `/tmp/wla-bootstrap-real.log`. Dependencies used were pycryptodome 3.23.0 and
zstandard 0.25.0, on this Python 3.14/arm64 environment. Their compatible wheel
archives were copied from existing local pip cache; **no dependency download was
requested in this acceptance run**. Other Python/macOS combinations remain separate
acceptance work.

## Local preview artifact

`scripts/build-preview.py` bundles an explicit allowlist: wheel, bootstrap/launcher
scripts, install guide, short preview README and license. It checks current-tree
privacy rules, package byte parity and an empty compatibility registry. It neither
recursively packages the checkout nor uploads files.

```sh
.venv/bin/python scripts/build-preview.py \
  --wheel dist/wechat_export-0.2.0-py3-none-any.whl
```

Artifact: `dist/wechat-local-archive-preview.zip` (gitignored, local only).
SHA256: `bb06100765291f79c646686c3456431acb45b5864edb92b4b518ad84faa2a93e`.

- Two independent zip constructions from the same input files were byte-identical.
  ZIP timestamps are deliberately fixed; this does not assert arbitrary upstream
  dependency wheels or every wheel build are reproducible.
- Bundle has a per-file manifest and SHA256SUMS, executable-mode metadata and no
  private/runtime roots. Hashes are not code signing, notarization or a trust root.
- Different existing artifacts are never silently overwritten.
- Unpacked outside the checkout, the shipped installer automatically selected the
  bundled wheel, installed into another private test root and passed its smoke.
- The **unpacked installed launcher** served a real Chromium session; 12 synthetic
  messages were previewed and exported with no browser page errors.
  Logs: `/tmp/wla-unpacked-preview-install.log`,
  `/tmp/wla-unpacked-preview-browser.log`.

## Final checks and limits

- Full suite: **323 passed, 66.412 s** (`/tmp/wla-bootstrap-full-tests.log`).
- Zsh syntax, current-tree privacy rules and `git diff --check` passed.
- README now starts with ordinary-user installation and guide flow, accurately
  describes 12 fictional demo messages, and retains links to historical evidence.
- Temporary installer test environments remain local for evidence; the owned
  browser and synthetic listener were stopped. No user processes were killed.

No commit, push, force-push, public upload, native signing/notarization, real WeChat
operation or new compatibility attestation occurred. `release_certified=false`.
Native selection/TCC, physical removable media, eligible-build new-user first-read,
independent Mac and owner-reviewed publication/history work remain open. The full
original objective is not complete merely because this preview can be installed.
