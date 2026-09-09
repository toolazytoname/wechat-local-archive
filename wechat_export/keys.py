"""Key access diagnostics. Never prints or logs key material."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from wechat_export.config import AppConfig
from wechat_export.fsutil import utc_now_iso, write_json


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def probe_attach(pid: int | None) -> dict[str, Any]:
    if pid is None:
        return {"status": "no_wechat_process"}
    proc = _run(["lldb", "-p", str(pid), "-b", "-o", "process detach", "-o", "quit"])
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    denied = "Not allowed to attach" in text or "attach failed" in text
    return {
        "pid": pid,
        "lldb_returncode": proc.returncode,
        "attach_denied": denied,
        "error_excerpt": "attach failed" if denied else "see local log",
    }


def load_keys_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "enc_key" not in raw:
        n = len(raw)
        sample_kind = "map"
    elif isinstance(raw, list):
        n = len(raw)
        sample_kind = "list"
    else:
        n = 1
        sample_kind = type(raw).__name__
    return {"path": str(path), "entry_count": n, "kind": sample_kind}


def key_access_status(cfg: AppConfig, wechat_pids: list[int]) -> dict[str, Any]:
    keys_present = bool(cfg.keys_path and cfg.keys_path.exists())
    attach = probe_attach(wechat_pids[0] if wechat_pids else None)
    status = {
        "collected_at_utc": utc_now_iso(),
        "keys_file_present": keys_present,
        "keys_file_meta": load_keys_file(cfg.keys_path) if keys_present else None,
        "attach_probe": attach,
        "sip_note": "SIP left enabled; not modified",
        "codesign_note": "WeChat original signature not modified",
        "result": "blocked" if not keys_present else "keys_file_present",
        "blocker": None,
    }
    if not keys_present:
        status["blocker"] = {
            "id": "key_access_blocked",
            "stage": "P1-L",
            "why": (
                "WeChat 4.1.x live DB keys are not on disk or in the login keychain under "
                "identifiable names. LLDB attach to the running WeChat process is denied by "
                "Hardened Runtime. Public 4.1+ method requires a temporary ad-hoc codesign of "
                "WeChat plus logout/login while a debugger is attached, which is outside the "
                "default authorization of this task."
            ),
            "not_tried": [
                "sudo codesign --force --deep --sign - /Applications/WeChat.app",
                "csrutil disable / SIP changes",
                "installing unsigned third-party wechat decrypt binaries",
                "uploading databases or memory dumps",
            ],
            "user_action_needed": [
                "Authorize a temporary ad-hoc re-sign of WeChat, a logout/login, then restore the official signature; or",
                "Provide a locally obtained SQLCipher raw-key map (never paste keys into chat); or",
                "Provide the phone-side RMFH backup key if backup-2 parse is the goal.",
            ],
        }
        status["result"] = "key_access_blocked"
    dest = cfg.reports_root
    dest.mkdir(parents=True, exist_ok=True)
    latest = dest / "key-access-status.json"
    write_json(latest, status)
    os.chmod(latest, 0o600)
    return status
