"""Observe a debug-copy launch: UI/login stage, denials, KDF breakpoint states.

Does not add entitlements, log in, scan a QR code, or touch the original app.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from wechat_export.fsutil import ensure_dir, write_json
from wechat_export.key_capture import (
    apply_hmac_result,
    capture_kdf,
    debug_copy_pids,
    kill_pids,
    verify_hits_against_db,
)


DB_STORAGE_HINT = "db_storage"


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout"
    return (proc.stdout or "") + (proc.stderr or "")


def click_enter_wechat() -> str:
    """Resume the already-logged-in session. Never click unnamed/close controls."""
    notes: list[str] = []
    _run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to set frontmost of process "WeChat" to true',
        ],
        timeout=5,
    )
    named = _run(
        [
            "osascript",
            "-e",
            """
tell application "System Events"
  tell process "WeChat"
    set frontmost to true
    set elems to entire contents of window 1
    repeat with e in elems
      set n to ""
      try
        set n to name of e as text
      end try
      if n is "进入微信" then
        perform action "AXPress" of e
        return "axpress_enter"
      end if
    end repeat
    return "name_not_found"
  end tell
end tell
""",
        ],
        timeout=10,
    )
    notes.append(named.strip() or "ax_empty")
    geom = _run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to tell process "WeChat" to get {position of window 1, size of window 1}',
        ],
        timeout=5,
    )
    import re

    nums = [int(x) for x in re.findall(r"-?\d+", geom)]
    if len(nums) >= 4:
        x, y, w, h = nums[:4]
        cx, cy = x + w // 2, y + int(h * 0.70)
        cliclick = "/opt/homebrew/bin/cliclick"
        if Path(cliclick).exists():
            # Human-like move then left click, then Return (default button).
            out = _run(
                [cliclick, "-e", "18", "-w", "80", f"m:{cx},{cy}", "w:120", f"c:{cx},{cy}", "w:80", "kp:return"],
                timeout=15,
            )
            notes.append(f"cliclick:{cx},{cy}:{out.strip()[:80]}")
        notes.append(_run(["osascript", "-e", 'tell application "System Events" to key code 36'], timeout=5).strip())
    return ",".join(n for n in notes if n)


def _window_dump() -> str:
    script = """
tell application "System Events"
  if not (exists process "WeChat") then
    return "no WeChat process"
  end if
  tell process "WeChat"
    set out to "frontmost=" & (frontmost as text) & linefeed
    try
      set out to out & "focused=" & (name of attribute "AXFocused" of it as text) & linefeed
    end try
    repeat with w in windows
      set wtitle to ""
      set wrole to ""
      set wdesc to ""
      try
        set wtitle to (title of w) as text
      end try
      try
        set wrole to (role of w) as text
      end try
      try
        set wdesc to (description of w) as text
      end try
      set out to out & "WINDOW title=" & wtitle & " role=" & wrole & " desc=" & wdesc & linefeed
      try
        repeat with e in UI elements of w
          set r to ""
          set n to ""
          set v to ""
          try
            set r to (role of e) as text
          end try
          try
            set n to (name of e) as text
          end try
          try
            set v to (value of e as text)
          end try
          set out to out & "  el role=" & r & " name=" & n & " value=" & v & linefeed
        end repeat
      end try
    end repeat
    return out
  end tell
end tell
"""
    return _run(["osascript", "-e", script], timeout=10)


def _classify_ui(dump: str) -> dict[str, Any]:
    text = dump or ""
    low = text.lower()
    flags = {
        "has_process": "no WeChat process" not in text,
        "mentions_enter_wechat": "进入微信" in text,
        "mentions_qr": any(s in text for s in ("二维码", "扫码")),
        "mentions_login": any(s in text for s in ("登录", "登入", "确认登录")),
        "mentions_proxy": "网络代理设置" in text,
        "mentions_chat_list": any(s in text for s in ("通讯录", "聊天列表")),
        "accessibility_denied": any(
            s in low for s in ("not allowed", "1002", "(-1719)", "osascript is not allowed", "timeout")
        ),
        "window_count": text.count("WINDOW title="),
    }
    stage = "unknown"
    if not flags["has_process"]:
        stage = "no_process"
    elif flags["accessibility_denied"] and flags["window_count"] == 0:
        stage = "ax_denied_or_empty"
    elif flags["mentions_enter_wechat"] or flags["mentions_proxy"]:
        stage = "session_resume"
    elif flags["mentions_qr"] or flags["mentions_login"]:
        stage = "login_or_qr"
    elif flags["window_count"] > 0:
        stage = "has_window"
    flags["stage"] = stage
    return flags


def _lsof_db(pid: int | None) -> dict[str, Any]:
    if not pid:
        return {"pid": None, "db_storage": 0, "account_hits": []}
    out = _run(["lsof", "-p", str(pid), "-Fn"], timeout=8)
    db = 0
    hits = []
    for line in out.splitlines():
        if not line.startswith("n"):
            continue
        path = line[1:]
        if "db_storage" in path:
            db += 1
            if len(hits) < 8:
                hits.append(path)
    return {"pid": pid, "db_storage": db, "account_hits": hits}


def _log_denials(since: str) -> str:
    pred = (
        '(process == "WeChat" AND (eventMessage CONTAINS[c] "deny" OR '
        'eventMessage CONTAINS[c] "Sandbox" OR eventMessage CONTAINS[c] "Keychain" OR '
        'eventMessage CONTAINS[c] "SecItem")) OR '
        '(eventMessage CONTAINS "WeChat-debug" AND eventMessage CONTAINS[c] "deny")'
    )
    return _run(
        ["log", "show", "--style", "compact", "--last", since, "--predicate", pred],
        timeout=25,
    )


def diagnose_debug_copy(
    *,
    debug_app: Path,
    snapshot_contact: Path,
    report_dir: Path,
    timeout: float = 70,
) -> dict[str, Any]:
    ensure_dir(report_dir)
    exe = debug_app / "Contents/MacOS/WeChat"
    samples: list[dict[str, Any]] = []
    stop = threading.Event()
    clicked = {"value": None}
    resume_seen = {"n": 0}

    def observe() -> None:
        while not stop.is_set():
            pids = debug_copy_pids(debug_app)
            pid = pids[0] if pids else None
            dump = _window_dump()
            rec = {
                "t": time.time(),
                "pids": pids,
                "lsof": _lsof_db(pid),
                "ui": _classify_ui(dump),
                "ui_dump": dump[:4000],
            }
            if "进入微信" in dump or "网络代理设置" in dump:
                resume_seen["n"] += 1
                # Wait until the process is running after attach SIGSTOP/continue.
                if clicked["value"] is None and resume_seen["n"] >= 2:
                    clicked["value"] = click_enter_wechat()
                    rec["resume_click"] = clicked["value"]
            samples.append(rec)
            (report_dir / "ui-latest.txt").write_text(dump, encoding="utf-8")
            if stop.wait(4):
                break

    def opener() -> None:
        time.sleep(1.0)
        subprocess.Popen(["open", str(debug_app)])

    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    status, hits = capture_kdf(
        exe,
        timeout=timeout,
        max_hits=16,
        waitfor_name="WeChat",
        on_waiting=opener,
        extra_symbols=["sqlite3_key", "sqlite3_key_v2"],
    )
    stop.set()
    observer.join(timeout=5)

    hmac_ok = False
    hmac_n = 0
    if hits and snapshot_contact.exists():
        hmac_ok, hmac_n = verify_hits_against_db(hits, snapshot_contact)
        apply_hmac_result(status, hmac_ok, hmac_n, mode="passphrase_or_raw")

    public = status.public_dict()
    # never persist passphrase bytes in the report
    result = {
        "kdf": public,
        "hmac_ok": hmac_ok,
        "hmac_n": hmac_n,
        "candidate_count": len(hits),
        "candidate_lengths": [h.length for h in hits],
        "samples": samples,
        "final_ui": samples[-1]["ui"] if samples else None,
        "db_storage_seen": any(s.get("lsof", {}).get("db_storage", 0) > 0 for s in samples),
        "denials_log": _log_denials("3m")[-8000:],
    }
    write_json(report_dir / "diagnose.json", result)
    leftover = debug_copy_pids(debug_app)
    if leftover:
        kill_pids(leftover)
    if status.session_pids:
        kill_pids(status.session_pids)
    result["leftover_copy_pids"] = debug_copy_pids(debug_app)
    write_json(report_dir / "diagnose.json", result)
    return {"result": result, "hits": hits, "status": status}
