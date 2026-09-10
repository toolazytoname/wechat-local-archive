"""Negative/positive HMAC tests for obvious local passphrase candidates.

Candidates are derived from already-known account directory names, not
brute-forced. Results are pass/fail only; keys are not printed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from wechat_export.config import AppConfig
from wechat_export.fsutil import ensure_dir, utc_now_iso, write_json
from wechat_export.sqlcipher4 import derive_raw_key, page1_hmac_ok


def probe_account_passphrases(cfg: AppConfig, rel_db: str = "message/weclaw.db") -> dict:
    src = cfg.live_db_root / rel_db
    if not src.exists():
        return {"status": "missing_sample", "rel_db": rel_db}
    work = ensure_dir(cfg.work_root / "passphrase-probe")
    dst = work / src.name
    shutil.copy2(src, dst)
    with dst.open("rb") as stream:
        page = stream.read(4096)
    salt = page[:16]
    candidates = []
    account = cfg.live_account_root.name
    for label, secret in (
        ("live_account_dir", account.encode("utf-8")),
        ("backup_account_dir", cfg.account_backup_root.name.encode("utf-8")),
        ("live_account_dir_utf8_lower", account.lower().encode("utf-8")),
    ):
        raw = derive_raw_key(secret, salt)
        ok = page1_hmac_ok(page, raw)
        candidates.append({"label": label, "hmac_ok": ok})
        # do not retain raw key
        del raw
    any_ok = any(c["hmac_ok"] for c in candidates)
    result = {
        "collected_at_utc": utc_now_iso(),
        "rel_db": rel_db,
        "sample_size": dst.stat().st_size,
        "page_aligned_4096": dst.stat().st_size % 4096 == 0,
        "candidate_count": len(candidates),
        "any_hmac_ok": any_ok,
        "candidates": candidates,
        "status": "passphrase_matched" if any_ok else "no_builtin_passphrase_match",
    }
    write_json(cfg.reports_root / "inspect" / "passphrase-probe.json", result)
    return result
