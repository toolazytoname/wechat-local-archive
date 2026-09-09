# Brief for a second AI reviewing this work

Paste this file plus the repository (without `data/`) to a reviewer.

## Ask them to check

1. **Privacy of the public tree.** `data/`, keys, and live chat bodies must not be in Git. `examples/demo-export/` must use fictional names only.
2. **Honesty of coverage.** Exports are `source_kind=live-db`. `backup2_coverage` must remain `unverified` unless they find an RMFH decoder that actually ran.
3. **Codec claims.** SQLCipher 4 defaults were HMAC-verified against this operator’s live-db snapshot; that does not prove every WeChat version. Truncated pages must error, not pad.
4. **Viewer.** `python -m wechat_export serve` must bind loopback. “只看可读文字” should hide XML payloads. Featured chats come from the local manifest, not from committed secrets.
5. **Tests.** `python -m unittest discover -s tests -v` should pass. Key-capture tests must distinguish resolved / hit / captured / HMAC and must not `pkill debugserver` by name.
6. **UI/docs drift.** README commands should match the CLI that actually ships.

## What they should not do

- Re-print any key material if they find it on disk.
- Re-sign WeChat, disable SIP, or upload the archive “to try the cloud”.
- Treat synthetic `examples/demo-export` counts (4 messages) as the operator’s 510k-message archive.

## Operator-only artifacts (local, gitignored)

These exist on the machine that ran the export, not in the GitHub repo:

- `data/exports/<run>/` — JSONL/CSV/Markdown + `archive.sqlite`
- `data/private/passphrase.raw` — 0600
- `wiki/`, `HANDOFF.md`, `BLOCKERS.md` — investigation log with machine paths

A review that only clones GitHub will not see the real chats; that is intended. To review the archive itself, the operator must grant a local checkout that already contains `data/`.
