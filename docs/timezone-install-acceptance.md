# Local-time query and clean-install acceptance — 2026-09-09

This follow-up closes two specific gaps from `acceptance-audit-2026-09-09.md`.
It does not mark the full product or independent real-account capture complete.

## Local time contract

- Browser sends the entered ISO text unchanged instead of using JavaScript Date
  normalization. A visible note names its IANA timezone; text inputs permit an
  explicit offset such as `2026-11-01T01:30:00-07:00`.
- QuerySpec interprets a naive query bound in `display_timezone`. An explicit
  numeric offset/Z describes an absolute instant and does not get reinterpreted.
- For a naive wall time, both fold choices are round-tripped through UTC. Zero
  valid instants returns `nonexistent_local_time`; two distinct instants returns
  `ambiguous_local_time`. The user must select a valid time or explicit offset.
- Canonical timestamp parsing retains its UTC-oriented contract; only human query
  bounds use the declared timezone. Existing explicit-Z/offset callers are stable.
- HTTP preview, POST export and CLI slice use this same QuerySpec. Preview no
  longer drops the timezone and silently interprets the bounds as UTC.
- This is a deliberate change for CLI/API naive bounds with a non-UTC timezone:
  they now mean wall time in that timezone. Send Z/offset for absolute instants.

## Regression evidence

The initial local-time tests had **5 failures out of 7**. Added coverage checks
Los Angeles spring gap and autumn fold, half-hour Lord Howe fold, invalid calendar
date, Shanghai no-DST, explicit offset disambiguation and mixed absolute/local
intervals. HTTP tests confirm the same selected records in preview and export and
the same fold rejection on both endpoints.

## Truly separate Python install on this Mac

- Created a new venv without system-site-packages and installed the built wheel
  with dependencies, not `--no-deps`. Package files for the tool, Crypto and
  zstandard were verified under the new prefix.
- Ran Python `-I` outside the checkout, with PYTHONPATH/PYTHONHOME removed. Checked
  that no repository path was in sys.path and source_checkout_root() returned None.
- Installed versions recorded: wechat-export 0.2.0, pycryptodome 3.23.0,
  zstandard 0.25.0. These are observed installed versions, not newly pinned global
  compatibility promises. Installer used cached package distributions when present.
- Bundled synthetic demo was copied into a separate writable runtime, indexed and
  verified: 9 records. The installed server listened on 127.0.0.1:8893.
- HTTP smoke: bootstrap/meta, preview 9, async analysis JSONL export 9, output file
  parsed and analysis mode checked, invalid DST fold rejected.
- Stopped only the test server PID after verification. No real WeChat account
  files, key capture, debug copy, system-policy changes, commit or push.

## Still not proven

This verifies Python packaging on this machine, not a fresh Mac without Python,
Homebrew or command-line tools. Graphical prerequisite installation guidance,
first-run output directory selection, independent user acceptance, synthetic CI,
full lineage/memory work and richer message rendering remain in the original goal.

## Final verification

Full suite: **154 tests passed, 34.644 s**. JS syntax and git diff whitespace checks passed.
Wheel source-byte match and private-file exclusions checked.
Artifact SHA-256: `837b7d2c98812d20acde70b2212828ed778eb894e834a0e40213a12e2298cb98`.
