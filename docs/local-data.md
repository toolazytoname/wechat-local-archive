# Where local files live

Everything private stays under this repository’s `data/` directory (gitignored).

```
data/
  exports/<run-id>/     # JSONL, CSV, Markdown, archive.sqlite
  private/config.json   # target names, paths
  private/passphrase.raw
```

Copy or clone an export here, then:

```bash
python -m wechat_export index --export-dir data/exports/<run-id>
python -m wechat_export serve --export-dir data/exports/<run-id>
```

The viewer listens on `http://127.0.0.1:8765` only. `--host 0.0.0.0` is rejected. Use **导出** in the UI to write JSONL/CSV/Markdown slices under `slices/` next to the archive.
