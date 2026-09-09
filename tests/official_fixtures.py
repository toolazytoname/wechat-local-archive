"""Build encrypted databases with the Homebrew SQLCipher CLI (Zetetic 4.x)."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from wechat_export.sqlcipher_cli import find_sqlcipher

PASSPHRASE = "fixture-passphrase"


def sqlcipher_bin() -> Path:
    found = find_sqlcipher()
    if found is None:
        raise RuntimeError("sqlcipher CLI not installed")
    return found


def require_sqlcipher() -> Path | None:
    return find_sqlcipher()


def _run_script(db: Path, script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [str(sqlcipher_bin()), str(db)],
        input=script if script.endswith("\n") else script + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    return proc


def create_closed_multipage(db: Path) -> None:
    _run_script(
        db,
        f"""
PRAGMA key = '{PASSPHRASE}';
PRAGMA cipher_compatibility = 4;
CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);
INSERT INTO t(v) VALUES ('committed-main');
WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<400)
INSERT INTO t(v) SELECT printf('row-%03d', x) FROM c;
.exit
""",
    )


def create_open_wal_snapshot(dest_dir: Path) -> dict[str, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    live = dest_dir / "live"
    live.mkdir()
    db = live / "enc.db"
    proc = subprocess.Popen(
        [str(sqlcipher_bin()), str(db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(
        f"""PRAGMA key = '{PASSPHRASE}';
PRAGMA cipher_compatibility = 4;
PRAGMA journal_mode = WAL;
PRAGMA wal_autocheckpoint = 0;
CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);
INSERT INTO t(v) VALUES ('in-main');
WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<400)
INSERT INTO t(v) SELECT printf('row-%03d', x) FROM c;
INSERT INTO t(v) VALUES ('in-wal-uncheckpointed');
SELECT count(*) FROM t;
"""
    )
    proc.stdin.flush()
    deadline = time.time() + 10
    wal = live / "enc.db-wal"
    while time.time() < deadline:
        if wal.exists() and wal.stat().st_size > 0 and db.stat().st_size > 0:
            break
        time.sleep(0.05)
    else:
        proc.kill()
        raise RuntimeError("WAL file did not appear")
    snap = dest_dir / "snap"
    snap.mkdir()
    for name in ("enc.db", "enc.db-wal", "enc.db-shm"):
        src = live / name
        if src.exists():
            shutil.copy2(src, snap / name)
    proc.stdin.write(".exit\n")
    proc.communicate(timeout=10)
    return {"live_db": db, "snap_db": snap / "enc.db", "snap_wal": snap / "enc.db-wal"}
