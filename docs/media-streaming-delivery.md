# Local attachment streaming — 2026-09-09

## Delivered

The local viewer no longer calls `read_bytes()` on resolved attachments. It opens
one regular file beneath the registered media root and streams at most 256 KiB per
read. Header sniffing reads 32 bytes. Single byte ranges support seeking (206),
including suffix/open-ended ranges; unsatisfied valid ranges return 416 with the
resource length. Malformed/multipart ranges are ignored and served as full 200.
If-Range cannot be validated for mutable local media, so it also gets a full 200.
Unknown bytes stay application/octet-stream with attachment disposition; no
extension-based HTML interpretation or remote fallback was added.

Containment uses descriptor-relative no-follow directory/file opens, nonblocking
open followed by regular-file validation (FIFO rejection), and the existing
archive identity guard. A disconnect/truncation after response headers closes the
connection rather than appending JSON to the attachment body. The original
Host/Origin/CSRF/CSP and loopback binding remain unchanged.

## Evidence

`tests/test_media_stream.py` uses synthetic files only: bounded read sizes,
truncated input, symlink/FIFO/outside-root rejection, and real loopback HTTP
full/range/suffix/416/If-Range responses. The HTTP test forbids `Path.read_bytes`
during requests. No real account or media was read.

## Boundaries

This is not media decryption, complete attachment availability accounting, a
cryptographically immutable attachment snapshot, or a same-UID attacker defense.
Media resolution still has its separate directory enumeration cost. It does not
complete first-run destination/dependency UX, compatibility acceptance, historical
privacy remediation, or independent-Mac validation. The earlier built wheel does
not include this follow-up until rebuilt.

Source remains `live-db`; backup 2 coverage remains `unverified`.

Verification: **242 tests passed**, 43.773 seconds (`/tmp/wla-media-stream-tests.log`); current-worktree privacy rule scan and `git diff --check` passed. This is a rule-based scan, not privacy certification or a clean-history claim.
