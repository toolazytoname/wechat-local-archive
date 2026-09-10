> **本次发布说明：** 用户已授权保存并推送当前代码与公共文档。当前树重新经过本机私有身份词检查；旧历史保留，不执行force push。本文中“未提交/未推送”描述此前审核时点，不代表本次交付状态。

# Public release privacy audit — 2026-09-09

## Decision: working tree fixed; published history is NOT clean

No real archive or key file was read. No matched sensitive strings are reproduced
here. This work did not rewrite history, commit, push, upload an archive or contact
a cloud scanner. Findings are about **identity metadata**, not a discovery of a
live-db passphrase or real chat bodies.

### Separate scopes, separate conclusions

| Scope | Evidence | Conclusion |
| --- | --- | --- |
| Proposed working tree | All tracked + untracked nonignored release files; rule and locally derived operator-identity checks; reviewed text datasets/binary assets | No current matches after the fixes below. This is bounded evidence, not mathematical proof of privacy. |
| Committed HEAD `2f3a5247da2d` | 67 files | One negative privacy test still contains an operator-account prefix. Removed from the working tree, **not yet committed/published**. |
| All locally reachable history | Two commits, 128 revision/file entries | Older commit `dccb8bd0ba75` retains identity metadata in five paths; current HEAD adds the negative-test prefix finding. **History privacy gate fails.** |
| Remote refs | Read-only `git ls-remote` advertised HEAD/main at `2f3a5247da2d`; no branch/tag ref pointing to a different revision was advertised | Matches the committed HEAD audited above. Not an audit of GitHub caches, forks, deleted objects, issues, releases or PR refs. |

Older affected paths (not the sensitive values):
- `examples/demo-export/archive.sqlite`: metadata contains a build-machine path;
  its message rows are the four-record fictional demo, not real chat history.
- `tests/test_export_pipeline.py` and `tests/test_preview.py`: operator-specific
  names used inside otherwise synthetic test fixtures.
- `wechat_export/config.py`: hardcoded account/backup/path defaults.
- `wechat_export/diagnose_copy.py`: operator-specific path defaults.

## Fixes and durable guard

- Removed the residual identity prefix from `tests/test_config_privacy.py`; assert
  empty account/target defaults without embedding an operator identity in the test.
- Rebuilt demo SQLite from its 12-record canonical data (it previously contained
  only 9 indexed records). Updated the stale four-record quality report to 12.
- Corrected three synthetic card records whose `is_self=false` conflicted with
  `sender_id=me`; the example and packaged datasets now match. Installed demo
  advances to `demo-v3`, preserving rather than overwriting old demo-v2 copies.
- Added `wechat_export.privacy_audit`: scopes `worktree`, `head`, `history`;
  ignored runtime trees are never traversed, forced-tracked private paths are
  rejected **without reading their contents**. Symlinks/submodules/oversize files
  fail closed. Changed source inventory is rejected.
- Scans credential/private-key patterns, operator home paths, literal DB keys, and
  optional private local needles. Reports rule names/path locations, never matched
  contents; credential-shaped/private paths are redacted too.
- Structured chat datasets and opaque assets require exact-hash review entries in
  `docs/public-asset-review.json`; unknown or changed files block the gate. Explicitly
  reviewed historical asset versions do **not** exempt their metadata from rules.
- Added current-tree guard to the synthetic CI workflow. Remote CI has not run.

## Asset review evidence

Reviewed the full 12-message synthetic JSONL (demo/Me/Alice/Studio), both conversation
files and duplicate packaged copies. Rebuilt current SQLite from those canonical
files. Inspected both current public screenshots and the distinct older screenshot:
all depict the fictional demo, with no operator identity visible. Screenshots are
historical UI illustrations, **not evidence of current UI appearance**.

The 120-byte media fixture exactly matches the JPEG constructed in `tests/test_media.py`.
The single-row hardlink DB exactly matches that same synthetic fixture. Historical
SQLite files each hold four rows, zero freelist pages; body values match historical
canonical text or null for the image placeholder. Their synthetic provenance does
not excuse the older metadata-path finding.

## Reproduce locally

```bash
.venv/bin/python -m wechat_export.privacy_audit --scope worktree
.venv/bin/python -m wechat_export.privacy_audit --scope head
.venv/bin/python -m wechat_export.privacy_audit --scope history
```

Use `--needles-file /absolute/private/file.json` for operator-specific checks. The
optional file must be 0600/private, a UTF-8 JSON list of strings, and must stay out
of Git. Do not paste its values into issue/CI logs. This audit derived identity
needles in memory from already committed historical configuration/test literals,
not by reading the user's `data/` or creating a new public identity list.

Exit 0 means **no selected rule/review-gate findings**, not “all possible secrets
proved absent”; reports explicitly retain `privacy_certified=false`. General rules
alone can miss a name/account prefix. Local identity checks and actual asset/text
review remain necessary. Hash review is an approval record, not a detector that can
prove a newly submitted asset safe just because someone edited its registry entry.

## Owner decision still required

Removing a value from the working tree or committing its removal does not erase
old public commits. Rewriting/publishing sanitized history is a separate destructive
operation requiring exact owner authorization and coordination with collaborators.
No force-push, repository deletion or history rewrite is authorized/executed here.
Until that decision and verification, do not advertise the **whole public history**
as privacy-clean. Other safe product work can continue while this gate stays open.

## Final local verification

- Full suite: **209 tests passed**, 42.093 s (`/tmp/wla-privacy-full-final.log`).
- Proposed worktree: 143 tracked/nonignored release files enumerated; current rule
  gate has no findings. Earlier in-memory operator-identity scan also had no current
  matches; HEAD/history findings above remain unchanged, not waived by this result.
- JavaScript syntax and `git diff --check`: passed.
- Rebuilt wheel and installed with dependencies in a new independent venv. Outside
  checkout with Python `-I`: 12 synthetic messages, revision-bound export/provenance
  passed. Demo upgrade regression preserves the old demo-v2 copy.
- Wheel SHA-256: `f7cd43cf022256c5aab782740033f13eadc8cf73d965058720237853ba9eb2ea`.
- CI privacy gate configured only for current proposed files. GitHub execution and
  any history remediation are **not** claimed complete.
