# AGENTS.md

Instructions for coding agents working in this repository.

## What this repo is

A **local** macOS WeChat (xWeChat 4.x) archive toolkit:

1. Snapshot and decrypt the current live-db (SQLCipher 4 + WAL merge).
2. Export JSONL / CSV / monthly Markdown, tagged `source_kind=live-db`.
3. Serve a loopback-only viewer over a SQLite index of that export.

It is **not** a cloud product, not a WeChat client, and not a backup-2 RMFH decoder (that path is still `unverified`).

## Hard rules

- Never print, commit, or upload keys, passphrases, memory dumps, or chat bodies from `data/`.
- Bind the viewer to `127.0.0.1` only.
- Do not treat a live-db export as “backup 2 complete”. Keep `backup2_coverage=unverified` until RMFH is actually decoded.
- Do not disable SIP, re-sign `/Applications/WeChat.app`, or log the user out unless the human explicitly re-authorizes that step.
- Personal investigation notes (`wiki/`, `HANDOFF.md`, `BLOCKERS.md`, `log.md`) stay local and gitignored.

## Layout

| Path | Role |
| --- | --- |
| `wechat_export/` | CLI, SQLCipher codec, export writers, archive index/server |
| `viewer/` | Static UI served by `python -m wechat_export serve` |
| `tests/` | Codec, export pipeline, key-capture driver, preview/index |
| `examples/demo-export/` | Tiny synthetic archive for docs and smoke tests |
| `data/` | **gitignored** — the operator’s real export, keys, sqlite index |
| `docs/` | Human docs (AI ingestion, review brief) |

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m wechat_export index --export-dir examples/demo-export
.venv/bin/python -m wechat_export serve --export-dir examples/demo-export
```

Real archives live under `data/exports/<run-id>` after the operator copies them here.

## Tests

Drive shipped functions. Do not hard-code HMAC success. Key-capture tests must use the self-built `kdf_probe` / `kdf_idle` programs, distinguish `breakpoint_resolved` / `breakpoint_hit` / `candidate_captured` / `hmac_verified`, and never `pkill` debugserver by name.

## Secrets

`data/private/` is mode 0700. Passphrase files are 0600. If a command needs a key, read the file; do not echo it into logs, Git, or chat.

## Product language

- “Readable text” means a message preview that is not WeChat XML.
- Featured conversation names come from local `config.json` `target_names`, not from hardcoded Git history.
