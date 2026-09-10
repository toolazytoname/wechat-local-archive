# Bounded encrypted-database reading — 2026-09-09

Developer preview; no real WeChat process, account, key or archive used. This closes
whole-database byte materialization in the live-db authentication/decryption paths,
not the outstanding independent-machine or real coordinator acceptance gates.

## Changes

- Added `authenticated_pages` / `verify_database_pages`: fixed-page reads, one
  derived MAC key per database, every page authenticated with its page number,
  truncated pages rejected, source descriptor/path stamps verified before success.
  The existing bytes API remains for compatibility and fixture tests.
- Main-file decrypt authenticates each page before decrypting it, writes to a
  unique private 0600 temporary file, and exclusively publishes only after all
  authentication/source checks. Errors/cancellation remove owned staging. Existing
  or concurrently created destinations are never overwritten.
- Salt/header probes in capture, adapter, inspector and passphrase probe use bounded
  reads. Guided key validation streams every target DB. Secret length checks read
  at most 33 bytes rather than materializing an arbitrarily large supplied file.
- Nonempty WAL still requires the official SQLCipher CLI. Its input is a private
  trio copy and its plaintext output is now staged privately too. Timeout/error/
  cancellation before publication removes staging, not the caller's source.
  Header validation reads only 16 bytes; successful output is 0600 and exclusive.
- Guided pipeline passes a cancellation check into the codec. Cancellation during
  a page loop remains `PipelineCancelled` / ledger `cancelled`, not generic failure.
- Media *header sniffing* is bounded; HTTP media serving still reads the complete
  selected attachment. This is a remaining memory limitation, not hidden by the
  database stress results.

## Scope of cancellation and consistency

Per-page Python checks are responsive between pages. SQLCipher subprocess work is
still bounded by its existing timeout, not immediately interruptible mid-command;
checks run before and after the command. SQLite integrity scans and copy operations
also have their own cancellation latency. Process-kill cleanup/retention is not
implemented by a `finally` block and remains an open requirement.

Authenticated streaming rejects observable mutation/replacement of the selected
main file. It does not make arbitrary live source copying transactionally safe:
callers must continue using the strict encrypted snapshot/ledger flow. WAL frames
are interpreted by SQLCipher, not a newly invented Python WAL decoder.

## Actual synthetic large-file evidence

Commands (opt-in, not part of default unit discovery):

```bash
.venv/bin/python -m tests.performance.codec_scale --megabytes 192
.venv/bin/python -m tests.performance.codec_scale --megabytes 192 --wal
```

Fixtures were generated with the installed official SQLCipher CLI; measured worker
processes were separate from fixture generation. Both authenticated/decrypted outputs
passed `integrity_check` and contained 201,326,592 logical payload bytes.

| Fixture | Physical input | Python peak RSS | CLI child peak RSS | Worker time |
| --- | ---: | ---: | ---: | ---: |
| Main-file | 205,660,160 bytes / 50,210 pages | 29.77 MiB | none | 2.07 s |
| WAL | main 4,096 bytes + WAL 206,873,472 bytes | 30.44 MiB | 10.70 MiB | 1.64 s |

The harness asserts <96 MiB **per process**, not a combined process-tree memory
budget, and validates committed WAL payload counts. These two fixtures do not
prove all real schema/media combinations or large contact maps have bounded memory.

Regression tests additionally prohibit unbounded codec reads, exercise final-page
corruption, in-loop cancellation, source replacement/append, concurrent destination
creation, SQLCipher timeout/partial-output cleanup and pipeline cancellation ledger.

## Final local verification

- Complete suite: **196 tests passed**, 37.571 s (`/tmp/wla-stream-codec-complete-tests.log`).
- Focused codec/pipeline suite: 16 passed, including actual official encryption fixtures.
- JS syntax and `git diff --check`: passed.
- Rebuilt wheel, installed with dependencies in a new separate venv, outside-checkout
  isolated (`-I`) smoke: 12 synthetic messages, revision-bound export, provenance OK.
- Wheel core codec/adapter/pipeline bytes match current sources; no `data/`, `wiki/`
  or tests packaged. This is an artifact check, not the pending exhaustive Git audit.
- Wheel SHA-256: `8860edde66d194726a0eef77b470abcfb4baa0cec3fcd3da8eecdcb3539c1e6d`.

No commit, push, original-app signing, real key acquisition, or archive upload.

Subsequent delivery: `scratch-retention-delivery.md` supersedes the temporary-file/crash-retention gap listed above for newly managed disposable work. Persistent retry data and unmarked legacy directories remain intentionally retained.
