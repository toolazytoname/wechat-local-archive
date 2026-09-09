from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from wechat_export import PARSER_VERSION
from wechat_export.fsutil import utc_now_iso


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    return (out.stdout or out.stderr or "").strip()


def _wechat_version() -> str | None:
    plist = Path("/Applications/WeChat.app/Contents/Info.plist")
    if not plist.exists():
        return None
    out = _run(["defaults", "read", str(plist.parent / "Info"), "CFBundleShortVersionString"])
    return out or None


def _codesign_summary() -> dict:
    app = "/Applications/WeChat.app"
    text = _run(["codesign", "-dv", "--verbose=2", app])
    flags = None
    authority = []
    identifier = None
    for line in text.splitlines():
        if line.startswith("Identifier="):
            identifier = line.split("=", 1)[1]
        elif "flags=" in line:
            flags = line
        elif line.startswith("Authority="):
            authority.append(line.split("=", 1)[1])
    return {
        "identifier": identifier,
        "flags_line": flags,
        "hardened_runtime": bool(flags and "runtime" in flags),
        "authority": authority,
    }


def collect_environment(extra: dict | None = None) -> dict:
    usage = shutil.disk_usage("/")
    sip = _run(["csrutil", "status"])
    wechat_pids = _run(["pgrep", "-x", "WeChat"]).split()
    payload = {
        "collected_at_utc": utc_now_iso(),
        "parser_version": PARSER_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "mac_ver": platform.mac_ver()[0],
        "sip_status": sip,
        "wechat_version": _wechat_version(),
        "wechat_codesign": _codesign_summary(),
        "wechat_pids": [int(p) for p in wechat_pids if p.isdigit()],
        "disk": {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        },
        "uid": os.getuid(),
    }
    if extra:
        payload.update(extra)
    return payload
