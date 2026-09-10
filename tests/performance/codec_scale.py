"""Opt-in official SQLCipher large-file -> authenticated plaintext RSS check.
No real WeChat input. Run: python -m tests.performance.codec_scale --megabytes 192
"""
import argparse
import json
import resource
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from tests.official_fixtures import PASSPHRASE, _run_script, sqlcipher_bin
from wechat_export.decrypt_livedb import decrypt_one


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--megabytes', type=int, default=192)
    p.add_argument('--worker', type=Path)
    p.add_argument('--wal', action='store_true')
    args = p.parse_args()
    if args.worker:
        start = time.monotonic()
        source = args.worker
        report = decrypt_one(source, source.with_name('plain.db'), passphrase=PASSPHRASE.encode())
        assert report['main_pages_hmac'] == 'ok' and report['integrity'] == 'ok'
        with sqlite3.connect(source.with_name('plain.db')) as conn:
            size = conn.execute('SELECT sum(length(v)) FROM t').fetchone()[0]
        factor = 1 if sys.platform == 'darwin' else 1024
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * factor / 1048576
        cli_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * factor / 1048576
        print(json.dumps({'synthetic': True, 'encrypted_bytes': source.stat().st_size,
                          'logical_payload_bytes': size, 'pages': report['main_pages'],
                          'peak_rss_mib': round(rss, 2), 'child_peak_rss_mib': round(cli_rss, 2),
                          'wal_bytes': report['wal_size'], 'wal_applied': report['wal_applied'],
                          'integrity': report['integrity'],
                          'elapsed_seconds': round(time.monotonic()-start, 2)}))
        assert rss < 96 and cli_rss < 96, 'codec or CLI exceeded 96 MiB per-process RSS budget'
        return
    if not 1 <= args.megabytes <= 1024:
        p.error('megabytes must be 1..1024')
    with tempfile.TemporaryDirectory(prefix='wla-codec-scale-') as td:
        source = Path(td)/'encrypted.db'
        script = f"""
PRAGMA key = '{PASSPHRASE}';
PRAGMA cipher_compatibility = 4;
{'PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;' if args.wal else ''}
CREATE TABLE t(v BLOB);
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<{args.megabytes})
INSERT INTO t SELECT zeroblob(1048576) FROM n;
"""
        if args.wal:
            ready = Path(td)/'ready.txt'
            proc = subprocess.Popen([str(sqlcipher_bin()), str(source)], stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
            try:
                proc.stdin.write(script + f"\n.once '{ready}'\nSELECT 'ready';\n")
                proc.stdin.flush()
                deadline = time.monotonic() + 120
                while not ready.exists() or ready.read_text().strip() != 'ready':
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('synthetic WAL generation failed or timed out')
                    time.sleep(0.05)
                # SQL command stream is now idle; only this owned process can write.
                snap = Path(td)/'snapshot'; snap.mkdir()
                for suffix in ('', '-wal', '-shm'):
                    shutil.copy2(Path(str(source)+suffix), snap/('encrypted.db'+suffix))
                source = snap/'encrypted.db'
                assert Path(str(source)+'-wal').stat().st_size >= args.megabytes*1048576
            finally:
                try:
                    proc.communicate('.exit\n', timeout=10)
                except (subprocess.TimeoutExpired, BrokenPipeError):
                    proc.kill(); proc.communicate(timeout=10)
        else:
            _run_script(source, script + '.exit\n')
            assert source.stat().st_size >= args.megabytes*1048576
        subprocess.run([sys.executable, '-m', 'tests.performance.codec_scale', '--worker', str(source)],
                       check=True, timeout=300)


if __name__ == '__main__':
    main()
