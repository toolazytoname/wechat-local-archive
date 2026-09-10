> **当前交付入口：** [普通用户界面与本地AI资料交付](consumer-delivery.md)、[使用说明](consumer-guide.md)、[工程经验](engineering-notes.md)。以下分日期报告是历史证据，不应将旧测试数量或包哈希当作最新状态。

> **2026-09-09 final content/package update:** [Structured-content safety and refreshed offline preview](content-safety-delivery.md): 329 Python tests, real offline HTML browser acceptance, four-viewport regression and fresh installed-wheel smoke passed. Use the local `preview-r2.zip`; real first-read and release gates remain open.

> **2026-09-09 installer update:** [Consent, isolated update/rollback and local preview bundle](bootstrap-delivery.md) now have real offline installation and unpacked-browser evidence. This is still a developer preview, not real first-read or public release certification.

> **2026-09-09 destination/filter update:** [Actual APFS cross-filesystem tests and readable-exclusion accounting](destination-and-filter-delivery.md) passed; native chooser timed out and is not certified. Physical external-disk and real first-read gates remain open.

> **2026-09-09 browser update:** [Reader interaction and four-viewport synthetic acceptance](browser-ui-delivery.md) now covers focus, responsive layouts, recoverable errors and four-format UI exports. This does not certify real first-read or independent-machine compatibility.

> **2026-09-09 accounting update:** [Source/attachment coverage delivery](attachment-accounting-delivery.md) adds explicit schema gaps, companion ledgers, local-media candidate observations and revision binding. This remains a developer preview; real first-read and release acceptance are not complete.

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
- Treat synthetic `examples/demo-export` counts (12 messages) as the operator’s 510k-message archive.

## Operator-only artifacts (local, gitignored)

These exist on the machine that ran the export, not in the GitHub repo:

- `data/exports/<run>/` — JSONL/CSV/Markdown + `archive.sqlite`
- `data/private/passphrase.raw` — 0600
- `wiki/`, `HANDOFF.md`, `BLOCKERS.md` — investigation log with machine paths

A review that only clones GitHub will not see the real chats; that is intended. To review the archive itself, the operator must grant a local checkout that already contains `data/`.

## Latest continuation

See `cli-source-safety-delivery.md` for CLI immutable export, optional preview revision guard, fail-closed legacy occupancy inspection, and the unexecuted synthetic CI workflow. Latest local full suite: **184 passed**; isolated installed-wheel smoke: **12 synthetic records**. Outstanding acceptance rows remain open.

### Encrypted-file memory follow-up

`streaming-codec-delivery.md`: final **196 tests passed**, 192 MiB official synthetic
main/WAL fixture stress, and fresh installed-wheel smoke (12 messages). The codec
uses bounded authenticated page reads and private exclusive publication. Review
also the explicitly remaining cancellation/media/crash limitations in that report.

### Public-tree and history privacy findings

Read `privacy-release-audit-2026-09-09.md` before declaring privacy approval. Proposed worktree has no current rule/operator-identity matches after fixes, but committed HEAD retains a negative-test identity prefix and older reachable history retains personal metadata. No history rewrite/push performed. `docs/public-asset-review.json` records exact reviewed synthetic dataset/media versions; it is not a substitute for reviewing new assets.

### Crash recovery and publication

See `scratch-retention-delivery.md` for receipt/lease-based cleanup of newly managed disposable copies, private slice/index staging, real process-crash tests, inherited SQLCipher leases, and intentional preservation of legacy/persistent materials. Review deletion boundaries as well as happy-path cleanup; no original backups, retained keys or completed exports are purge targets.

Local follow-up: see `media-streaming-delivery.md` and
`tests/test_media_stream.py` for bounded attachment HTTP reads and range tests.
This does not establish media completeness or independent-machine readiness.

First-run destination follow-up: `output-location-delivery.md` covers trusted native folder selection, job-bound external destinations, capacity/dependency guidance, destination-local full publication and explicit legacy-root registration. Native-dialog and real removable-volume acceptance remain pending.


### Fingerprint-bound support and coordinated reader guards

Read `fingerprint-reader-delivery.md`: **296 tests passed**, fresh installed wheel
smoke passed, registry packaged but empty. Whole bundles/nested modules are bound
across preparation and launch, copy entitlements are audited, and strict snapshots
check non-WeChat holders too. Coordinator/signing tests are synthetic contracts,
not proof of a newly successful real capture. On September 9, 2026, read-only
inspection found installed build **269631**, not the historical 269630; it remains
outside the candidate list. Do not treat the observation as authorization to try it.

### 原链接恢复

[链接交付说明](link-restoration-delivery.md)：网页链接可手动确认后打开，导出保留网址；新版包为 `preview-r3.zip`。附件恢复不因此变成已完成。
