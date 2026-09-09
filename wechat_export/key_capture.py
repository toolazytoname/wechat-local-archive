"""LLDB-driven KDF capture with explicit session states.

States are independent booleans:
  breakpoint_resolved  symbol has at least one resolved location
  breakpoint_hit       stop reason is the target breakpoint (not a generic pause)
  candidate_captured   password bytes were read using the length register from that hit
  hmac_verified        a candidate unlocks a provided SQLCipher page (separate step)

Cleanup kills only PIDs recorded for this session (LLDB, its children, the inferior).
It never runs pkill by process name.
"""

from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from wechat_export.fsutil import utc_now_iso
from wechat_export.sqlcipher4 import derive_raw_key, page1_hmac_ok

_HEX_LINE = re.compile(r"^\s*0x[0-9a-f]+:\s+((?:0x[0-9a-f]{2}\s*)+)$", re.I)
_LAUNCHED = re.compile(r"Process (\d+) launched")
_STOPPED = re.compile(r"Process (\d+) stopped")
_EXITED = re.compile(r"Process (\d+) exited")
_STOP_REASON = re.compile(r"stop reason = ([^\n]+)")
_BP_RESOLVED = re.compile(
    r"name = '([^']+)'.*?locations\s*=\s*(\d+).*?resolved\s*=\s*(\d+)",
    re.I | re.S,
)
_BP_WHERE = re.compile(r"where\s*=\s*(\S+)")
_REG_NAMED = re.compile(r"\b(x[0-9]+)\b\s*=\s*(0x[0-9a-f]+)", re.I)
_EXPR_INT = re.compile(r"\((?:unsigned\s+)?(?:long|int|size_t|unsigned long)\)\s*(-?\d+)")


@dataclass
class KdfHit:
    length: int
    stop_reason: str
    breakpoint_id: str | None
    # secret kept off public status dumps
    material: bytes = field(repr=False, default=b"")
    register_x2: int | None = None


@dataclass
class CaptureStatus:
    breakpoint_name: str
    breakpoint_resolved: bool = False
    breakpoint_locations: int = 0
    breakpoint_where: str | None = None
    breakpoint_hit: bool = False
    hit_count: int = 0
    lldb_hit_count: int | None = None
    candidate_captured: bool = False
    candidate_lengths: list[int] = field(default_factory=list)
    hmac_verified: bool = False
    hmac_verified_count: int = 0
    hmac_mode: str | None = None
    stop_reasons: list[str] = field(default_factory=list)
    launch_failed: bool = False
    attach_denied: bool = False
    inferior_exited: bool = False
    timed_out: bool = False
    kdf_attempted: bool = False
    session_pids: list[int] = field(default_factory=list)
    inferior_pid: int | None = None
    collected_at_utc: str = ""
    notes: list[str] = field(default_factory=list)

    def public_dict(self) -> dict:
        d = asdict(self)
        d["collected_at_utc"] = d["collected_at_utc"] or utc_now_iso()
        return d


class LldbSession:
    """Text-mode LLDB with a unique prompt so we never drain on a stale '(lldb)'."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["lldb", "--no-lldbinit"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=True,
            env={**os.environ, "TERM": "dumb"},
        )
        self.pgid = os.getpgid(self.proc.pid)
        self.buf = ""
        self._seq = 0
        self.cmd("settings set auto-confirm true", timeout=10)

    def _write(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _read_more(self, timeout: float) -> str:
        assert self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            return ""
        chunk = os.read(self.proc.stdout.fileno(), 65536)
        if not chunk:
            return ""
        text = chunk.decode("utf-8", errors="replace")
        self.buf += text
        return text

    def _idle_drain(self, since: int, idle: float = 0.35, timeout: float = 2.0) -> None:
        deadline = time.time() + timeout
        last = time.time()
        while time.time() < deadline:
            piece = self._read_more(0.05)
            if piece:
                last = time.time()
            elif time.time() - last >= idle:
                return

    def cmd(self, line: str, timeout: float = 30) -> str:
        """Run one LLDB command; completion is a queued script-print sentinel."""
        mark = len(self.buf)
        self._seq += 1
        token = f"CMD_DONE_{self._seq}_{time.time_ns()}"
        self._write(line)
        self._write(f'script print("{token}")')
        deadline = time.time() + timeout
        marker = f'script print("{token}")'
        while time.time() < deadline:
            new = self.buf[mark:]
            if marker in new:
                _head, after = new.split(marker, 1)
                # Wait until the sentinel is printed, not just echoed in the command.
                if token in after:
                    return _head
            if self.proc.poll() is not None:
                return self.buf[mark:]
            self._read_more(max(0.05, min(0.5, deadline - time.time())))
        return self.buf[mark:]

    def cmd_until_stop(self, line: str, timeout: float) -> str:
        mark = len(self.buf)
        self._write(line)
        deadline = time.time() + timeout
        while time.time() < deadline:
            new = self.buf[mark:]
            lowered = new.lower()
            if _STOPPED.search(new) or _EXITED.search(new):
                self._idle_drain(mark)
                return self.buf[mark:]
            if "error: process launch" in lowered or "failed to launch" in lowered or "not allowed to attach" in lowered:
                self._idle_drain(mark, idle=0.2)
                return self.buf[mark:]
            if self.proc.poll() is not None:
                return self.buf[mark:]
            self._read_more(min(0.5, max(0.05, deadline - time.time())))
        return self.buf[mark:]

    def descendant_pids(self) -> list[int]:
        found = {self.proc.pid}
        try:
            found.add(os.getpgid(self.proc.pid))
        except OSError:
            pass
        queue = [self.proc.pid]
        seen: set[int] = set()
        while queue:
            pid = queue.pop()
            if pid in seen:
                continue
            seen.add(pid)
            r = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
            for line in r.stdout.split():
                if line.isdigit():
                    c = int(line)
                    found.add(c)
                    queue.append(c)
        return sorted(p for p in found if p > 1)

    def close(self) -> None:
        pids = self.descendant_pids()
        if self.proc.poll() is None:
            try:
                self._write("process detach")
            except OSError:
                pass
            try:
                self._write("quit")
            except OSError:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        if self.proc.poll() is None:
            try:
                os.killpg(self.pgid, signal.SIGTERM)
            except OSError:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.pgid, signal.SIGKILL)
                except OSError:
                    self.proc.kill()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def _parse_hex_bytes(text: str) -> bytes:
    by: list[int] = []
    for line in text.splitlines():
        m = _HEX_LINE.match(line)
        if not m:
            continue
        for h in re.findall(r"0x([0-9a-f]{2})", m.group(1), re.I):
            by.append(int(h, 16))
    return bytes(by)


def _parse_named_reg(text: str, name: str) -> int | None:
    for m in _REG_NAMED.finditer(text):
        if m.group(1).lower() == name.lower():
            return int(m.group(2), 16)
    em = _EXPR_INT.search(text)
    if em:
        return int(em.group(1))
    return None


def _is_breakpoint_stop(reason: str) -> bool:
    return "breakpoint" in reason.lower()


def _parse_breakpoint_list(text: str, status: CaptureStatus) -> None:
    flat = re.sub(r"\s+", " ", text)
    rm = _BP_RESOLVED.search(flat)
    if rm:
        status.breakpoint_locations = int(rm.group(2))
        status.breakpoint_resolved = int(rm.group(3)) > 0
    where = _BP_WHERE.search(text)
    if where and not status.breakpoint_where:
        status.breakpoint_where = where.group(1)
    hm = re.search(r"hit count\s*=\s*(\d+)", text, re.I)
    if hm:
        status.lldb_hit_count = int(hm.group(1))
    if "pending" in text.lower() and not status.breakpoint_resolved:
        status.notes.append("breakpoint_pending")


def capture_kdf(
    exe: Path,
    *,
    symbol: str = "CCKeyDerivationPBKDF",
    timeout: float = 30,
    max_hits: int = 8,
    arch: str | None = "arm64",
    extra_args: list[str] | None = None,
    waitfor_name: str | None = None,
    on_waiting: object | None = None,
    extra_symbols: list[str] | None = None,
) -> tuple[CaptureStatus, list[KdfHit]]:
    """Run inferior under LLDB and collect KDF hits. Secrets stay in KdfHit.material."""
    names = [symbol, *(extra_symbols or [])]
    status = CaptureStatus(breakpoint_name="+".join(names), collected_at_utc=utc_now_iso(), kdf_attempted=True)
    hits: list[KdfHit] = []
    session: LldbSession | None = None
    try:
        session = LldbSession()
        status.session_pids = session.descendant_pids()
        session.cmd("settings set auto-confirm true")
        session.cmd("settings set interpreter.prompt-on-quit false")
        session.cmd("settings set stop-line-count-before 0")
        session.cmd("settings set stop-line-count-after 0")
        created = session.cmd(f'target create "{exe}"')
        if "error:" in created.lower() and "executable" in created.lower():
            status.notes.append("target_create_error")
            return status, hits
        bp_out = ""
        for name in names:
            bp_out += session.cmd(f"breakpoint set -n {name}")
        _parse_breakpoint_list(bp_out, status)
        if waitfor_name:
            import threading

            if on_waiting is not None:
                threading.Thread(target=on_waiting, daemon=True).start()
            launched = session.cmd_until_stop(
                f"process attach --name {waitfor_name} --waitfor",
                timeout,
            )
        else:
            launch_cmd = "process launch --stop-at-entry false"
            if arch:
                launch_cmd += f" --arch {arch}"
            if extra_args:
                launch_cmd += " -- " + " ".join(extra_args)
            launched = session.cmd_until_stop(launch_cmd, timeout)
        status.session_pids = session.descendant_pids()
        mpid = _LAUNCHED.search(launched) or _STOPPED.search(launched)
        if mpid:
            status.inferior_pid = int(mpid.group(1))
        if "not allowed to attach" in launched.lower() or "attach failed" in launched.lower():
            status.attach_denied = True
            status.notes.append("attach_denied")
            return status, hits
        if "error: process launch" in launched.lower() or "failed to launch" in launched.lower():
            status.launch_failed = True
            return status, hits

        list_out = session.cmd("breakpoint list")
        _parse_breakpoint_list(list_out, status)

        deadline = time.time() + timeout
        current = launched
        while time.time() < deadline and len(hits) < max_hits:
            if _EXITED.search(current):
                status.inferior_exited = True
                break
            reason_m = _STOP_REASON.search(current)
            reason = reason_m.group(1).strip() if reason_m else ""
            info = session.cmd("thread info")
            reason2 = _STOP_REASON.search(info)
            if reason2:
                reason = reason2.group(1).strip()
            if reason:
                status.stop_reasons.append(reason[:120])
            if _is_breakpoint_stop(reason):
                status.breakpoint_hit = True
                status.hit_count += 1
                x2_out = session.cmd("register read x2")
                length = _parse_named_reg(x2_out, "x2")
                if length is None:
                    x2_out += session.cmd("expression -- (unsigned long)$x2")
                    length = _parse_named_reg(x2_out, "x2")
                    if length is None:
                        em = _EXPR_INT.search(x2_out)
                        if em:
                            length = int(em.group(1))
                if length is None or length <= 0 or length > 256:
                    status.notes.append(f"rejected_length:{length}")
                else:
                    x1_out = session.cmd("register read x1")
                    addr = _parse_named_reg(x1_out, "x1")
                    if addr is None:
                        mem = session.cmd(f"memory read --size 1 --count {length} --format x $x1")
                    else:
                        mem = session.cmd(f"memory read --size 1 --count {length} --format x {addr:#x}")
                    material = _parse_hex_bytes(mem)[:length]
                    if len(material) == length:
                        hits.append(
                            KdfHit(
                                length=length,
                                stop_reason=reason,
                                breakpoint_id="1",
                                material=material,
                                register_x2=length,
                            )
                        )
                        status.candidate_captured = True
                        status.candidate_lengths.append(length)
                    else:
                        status.notes.append(f"short_memory_read:{len(material)}/{length}")
            elif reason:
                status.notes.append(f"ignored_stop:{reason[:80]}")
            if _EXITED.search(session.buf):
                status.inferior_exited = True
                break
            current = session.cmd_until_stop("continue", max(1.0, deadline - time.time()))
        else:
            if not status.inferior_exited and time.time() >= deadline:
                status.timed_out = True
        list_out = session.cmd("breakpoint list")
        _parse_breakpoint_list(list_out, status)
    finally:
        if session is not None:
            if status.breakpoint_hit and status.breakpoint_locations == 0:
                status.breakpoint_resolved = True
                status.breakpoint_locations = 1
            status.session_pids = sorted(set(status.session_pids) | set(session.descendant_pids()))
            session.close()
    return status, hits


def verify_hits_against_db(hits: list[KdfHit], db: Path) -> tuple[bool, int]:
    """HMAC-verify captured passphrase bytes against page 1. Does not mutate CaptureStatus."""
    page = db.read_bytes()[:4096]
    if len(page) < 4096:
        return False, 0
    salt = page[:16]
    n = 0
    for hit in hits:
        raw = derive_raw_key(hit.material, salt)
        if page1_hmac_ok(page, raw):
            n += 1
            continue
        if len(hit.material) == 32 and page1_hmac_ok(page, hit.material):
            n += 1
    return n > 0, n


def apply_hmac_result(status: CaptureStatus, ok: bool, count: int, mode: str | None = None) -> None:
    status.hmac_verified = bool(ok)
    status.hmac_verified_count = count
    status.hmac_mode = mode


def kill_pids(pids: list[int]) -> None:
    """Terminate only the given PIDs. Never pkill by name."""
    for pid in pids:
        if pid <= 1:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.2)
    for pid in pids:
        if pid <= 1:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def debug_copy_pids(debug_app: Path) -> list[int]:
    exe = str((debug_app / "Contents/MacOS/WeChat").resolve())
    proc = subprocess.run(["pgrep", "-f", exe], capture_output=True, text=True)
    return [int(p) for p in proc.stdout.split() if p.strip().isdigit()]
