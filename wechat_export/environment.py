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
        out = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.stdout or out.stderr or "").strip()


def _plist_value(key: str) -> str | None:
    plist = Path("/Applications/WeChat.app/Contents/Info.plist")
    if not plist.exists():
        return None
    out = _run(["defaults", "read", str(plist.parent / "Info"), key])
    return out or None


def _wechat_version() -> str | None:
    return _plist_value("CFBundleShortVersionString")


def _wechat_build() -> str | None:
    return _plist_value("CFBundleVersion")


def _wechat_arch() -> str | None:
    binary = Path("/Applications/WeChat.app/Contents/MacOS/WeChat")
    if not binary.exists():
        return None
    out = _run(["lipo", "-archs", str(binary)]) or _run(["file", "-b", str(binary)])
    return out or None


def _tool_present(name: str) -> bool:
    return bool(shutil.which(name))


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
    try:
        verified = subprocess.run(['codesign', '--verify', '--deep', '--strict', app],
                                  capture_output=True, timeout=45).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        verified = False
    return {
        "verified": verified,
        "identifier": identifier,
        "flags_line": flags,
        "hardened_runtime": bool(flags and "runtime" in flags),
        "authority": authority,
    }


def collect_environment(extra: dict | None = None) -> dict:
    usage = shutil.disk_usage("/")
    sip = _run(["csrutil", "status"])
    wechat_pids = _run(["pgrep", "-x", "WeChat"]).split()
    wechat_app = Path("/Applications/WeChat.app")
    xwechat = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    from wechat_export.build_fingerprint import fingerprint_bundle, public_fingerprint
    fingerprint = None
    if wechat_app.is_dir():
        try:
            fingerprint = fingerprint_bundle(wechat_app)
        except (OSError, ValueError) as exc:
            fingerprint = {'complete': False, 'error': type(exc).__name__}
    payload = {
        "collected_at_utc": utc_now_iso(),
        "parser_version": PARSER_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "mac_ver": platform.mac_ver()[0],
        "sip_status": sip,
        "wechat_present": wechat_app.exists(),
        "wechat_version": _wechat_version(),
        "wechat_build": _wechat_build(),
        "wechat_arch": _wechat_arch(),
        "wechat_fingerprint": public_fingerprint(fingerprint),
        "wechat_codesign": _codesign_summary() if wechat_app.exists() else {},
        "wechat_running": bool(wechat_pids),
        "wechat_pids": [int(p) for p in wechat_pids if p.isdigit()],
        "xwechat_root_exists": xwechat.exists(),
        "tools": {
            "sqlcipher": _tool_present("sqlcipher"),
            "lldb": _tool_present("lldb"),
            "codesign": _tool_present("codesign"),
        },
        "disk": {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "low_space": usage.free < 2 * 1024 * 1024 * 1024,
        },
        "uid": os.getuid(),
    }
    if extra:
        payload.update(extra)
    return payload
