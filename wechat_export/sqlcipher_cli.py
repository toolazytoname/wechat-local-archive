"""Drive the official SQLCipher CLI. Keys go via stdin, never argv."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from wechat_export.sqlcipher4 import SqlCipherError

DEFAULT_SQLCIPHER = "sqlcipher"


def find_sqlcipher() -> Path | None:
    found = shutil.which(DEFAULT_SQLCIPHER)
    if found:
        return Path(found)
    for candidate in (Path("/opt/homebrew/bin/sqlcipher"), Path("/usr/local/bin/sqlcipher")):
        if candidate.exists():
            return candidate
    return None


def sqlcipher_version(binary: Path | None = None) -> str | None:
    exe = binary or find_sqlcipher()
    if exe is None:
        return None
    proc = subprocess.run([str(exe), ":memory:", "PRAGMA cipher_version;"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _key_pragma(*, raw_key: bytes | None, passphrase: bytes | None) -> str:
    if raw_key is not None and passphrase is not None:
        raise SqlCipherError("pass exactly one of raw_key or passphrase")
    if raw_key is not None:
        if len(raw_key) != 32:
            raise SqlCipherError(f"raw_key must be 32 bytes, got {len(raw_key)}")
        return f'PRAGMA key = "x\'{raw_key.hex()}\'";'
    if passphrase is not None:
        # Passphrase as SQL string. Avoid non-utf8 secrets on this path.
        text = passphrase.decode("utf-8")
        return f"PRAGMA key = {_sql_quote(text)};"
    raise SqlCipherError("missing key material")


def run_sqlcipher(db: Path, script: str, *, binary: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    exe = binary or find_sqlcipher()
    if exe is None:
        raise SqlCipherError("official sqlcipher CLI not found")
    return subprocess.run(
        [str(exe), str(db)],
        input=script if script.endswith("\n") else script + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def export_plaintext(
    src_db: Path,
    dest_db: Path,
    *,
    raw_key: bytes | None = None,
    passphrase: bytes | None = None,
    binary: Path | None = None,
) -> dict:
    """Open src_db with its sidecar -wal and write a merged plaintext SQLite file.

    SQLCipher applies WAL on open. sqlcipher_export copies the logical database,
    including committed WAL frames, into dest_db.
    """
    if dest_db.exists():
        raise SqlCipherError(f"refusing to overwrite {dest_db}")
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    wal = Path(str(src_db) + "-wal")
    shm = Path(str(src_db) + "-shm")
    # Copy the trio into a temp dir so SQLCipher close/checkpoint cannot
    # mutate the caller's encrypted snapshot.
    with tempfile.TemporaryDirectory(prefix="sqlcipher-export-") as td:
        work = Path(td) / src_db.name
        shutil.copy2(src_db, work)
        if wal.exists():
            shutil.copy2(wal, Path(str(work) + "-wal"))
        if shm.exists():
            shutil.copy2(shm, Path(str(work) + "-shm"))
        script = "\n".join(
            [
                _key_pragma(raw_key=raw_key, passphrase=passphrase),
                "PRAGMA cipher_compatibility = 4;",
                f"ATTACH DATABASE {_sql_quote(str(dest_db))} AS plain KEY '';",
                "SELECT sqlcipher_export('plain');",
                "DETACH DATABASE plain;",
                ".exit",
            ]
        )
        proc = run_sqlcipher(work, script, binary=binary)
    combined = (proc.stdout or "") + (proc.stderr or "")
    failed = proc.returncode != 0 or "Error" in combined or not dest_db.exists()
    if not failed:
        try:
            if dest_db.read_bytes()[:16] != b"SQLite format 3\x00":
                failed = True
        except OSError:
            failed = True
    if failed:
        if dest_db.exists():
            dest_db.unlink()
        raise SqlCipherError(f"sqlcipher_export failed for {src_db.name} (exit {proc.returncode})")
    return {
        "src": str(src_db),
        "dst": str(dest_db),
        "wal_present": wal.exists(),
        "wal_size": wal.stat().st_size if wal.exists() else 0,
        "shm_present": shm.exists(),
        "cli": str(binary or find_sqlcipher()),
        "stdout_len": len(combined),
    }
