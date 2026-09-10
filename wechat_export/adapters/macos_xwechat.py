"""macOS xWeChat 4.x Apple Silicon adapter candidate.

verified_builds is empty on purpose. Do not fill a build as verified just because
the local machine currently shows 4.1.13 / 269630.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

ADAPTER_ID = "macos-xwechat-4-arm64"
EXPERIMENTAL_TARGETS = {("4.1.13", "269579"), ("4.1.13", "269630")}
CANDIDATE_MACHINES = {"arm64", "aarch64"}
VERIFIED_BUILDS: tuple[str, ...] = ()


def evaluate(env: dict[str, Any]) -> dict[str, Any]:
    platform = str(env.get("platform") or "")
    machine = str(env.get("machine") or "").lower()
    version = str(env.get("wechat_version") or "")
    build = str(env.get("wechat_build") or "")
    present = bool(env.get("wechat_present"))
    darwin = platform.lower().startswith("darwin") or "macos" in platform.lower() or bool(env.get("mac_ver"))
    arch_ok = machine in CANDIDATE_MACHINES
    version_ok = (version, build) in EXPERIMENTAL_TARGETS
    candidate = bool(present and darwin and arch_ok and version_ok)
    from wechat_export.compatibility_registry import reviewed_support
    support = reviewed_support(env)
    build_verified = bool(candidate and support["verified"])
    reason = None
    if not present:
        reason = "wechat_not_installed"
    elif not darwin:
        reason = "not_macos"
    elif not arch_ok:
        reason = "unsupported_arch"
    elif not version_ok:
        reason = "unsupported_version"
    elif not build_verified:
        reason = "build_unverified"
    return {
        "id": ADAPTER_ID,
        "candidate": candidate,
        "build_verified": build_verified,
        "fingerprint_support": support,
        "verified_builds": list(VERIFIED_BUILDS),
        "wechat_present": present,
        "wechat_version": version or None,
        "wechat_build": build or None,
        "machine": machine or None,
        "reason": reason,
        "key_capture_allowed": False,
        "live_operations_eligible": candidate,
        "notes": "Adapter candidate only. key_capture_allowed stays false; live steps need a this-job grant plus preflight.",
    }


class AdapterError(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def preflight() -> dict:
    """Fail-closed checks before any live WeChat copy or debugger."""
    from wechat_export.livedb_snapshot import wechat_pids

    reasons: list[str] = []
    pids = wechat_pids()
    if pids:
        reasons.append("wechat_running")
    app = Path("/Applications/WeChat.app")
    original_ok = False
    cdhash = None
    if not app.exists():
        reasons.append("wechat_not_installed")
    else:
        text = ""
        try:
            proc = subprocess.run(
                ["codesign", "-dv", "--verbose=4", str(app)],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            text = (proc.stderr or "") + (proc.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            reasons.append("codesign_unavailable")
        original_ok = "Developer ID Application: Tencent" in text and "flags=0x10000(runtime)" in text
        for line in text.splitlines():
            if line.startswith("CDHash="):
                cdhash = line.split("=", 1)[1]
        if not original_ok:
            reasons.append("original_signature_unexpected")
    tools = {name: bool(shutil.which(name)) for name in ("sqlcipher", "lldb")}
    if not tools["lldb"]:
        reasons.append("lldb_missing")
    if not tools["sqlcipher"]:
        reasons.append("sqlcipher_missing")
    return {
        "ok": not reasons,
        "reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "wechat_running": bool(pids),
        "original_signature_ok": original_ok,
        "original_cdhash": cdhash,
        "tools": tools,
    }


def snapshot_idle(src_root: Path, dest_root: Path) -> dict:
    from wechat_export.livedb_snapshot import snapshot_livedb_tree

    summary = snapshot_livedb_tree(src_root, dest_root)
    if summary.get("hot_copies"):
        raise AdapterError("hot snapshot rejected", "hot_snapshot_rejected")
    if summary.get("wechat_pids"):
        raise AdapterError("wechat still running", "wechat_running")
    return summary


def verify_passphrase_file(snapshot_root: Path, passphrase_path: Path) -> dict:
    """HMAC-check passphrase against snapshot DBs. Never returns key bytes."""
    from wechat_export.sqlcipher4 import PageHmacError, TruncatedDatabaseError, derive_raw_key, page1_hmac_ok, verify_database_pages, read_prefix

    if not passphrase_path.is_file():
        raise AdapterError("passphrase file missing", "passphrase_missing")
    material = read_prefix(passphrase_path, 33)
    if len(material) != 32:
        raise AdapterError("passphrase length rejected", "passphrase_invalid")
    targets = [
        snapshot_root / "contact" / "contact.db",
        snapshot_root / "message" / "message_0.db",
        snapshot_root / "message" / "message_1.db",
    ]
    checked = []
    for db in targets:
        rec = {"path": db.name, "ok": False}
        if not db.is_file():
            rec["status"] = "missing"
            checked.append(rec)
            continue
        page = read_prefix(db, 4096)
        raw = derive_raw_key(material, page[:16])
        rec["page1"] = page1_hmac_ok(page, raw)
        try:
            verify_database_pages(db, raw)
            rec["all_pages"] = True
            rec["ok"] = bool(rec["page1"])
        except (TruncatedDatabaseError, PageHmacError):
            rec["all_pages"] = False
            rec["ok"] = False
        checked.append(rec)
    ok = all(item.get("ok") for item in checked)
    return {"ok": ok, "databases": checked}


def execute_key_capture(*, job=None, **kwargs) -> dict:
    """Internal executor. HTTP access must go through workflow's staged grants."""
    if (job is None or job.state != "acquiring_key" or
            not job.payload.get("prepared_snapshot") or
            not (job.payload.get("live_grant") or {}).get("consumed")):
        raise AdapterError("Task-bound prepared reader required", "live_grant_required")
    from wechat_export.live_reader import acquire_key
    return acquire_key(**kwargs)
