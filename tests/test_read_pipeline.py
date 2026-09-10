import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_export_pipeline import _build_dbs, _cfg
from wechat_export.read_pipeline import process_snapshot, PipelineError, PipelineCancelled
from wechat_export.sqlcipher_cli import find_sqlcipher
from wechat_export.fsutil import sha256_file

class ReadPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = _cfg(self.root)
        self.plain = _build_dbs(self.root)
        self.snapshot = self.root / 'encrypted'
        self.key = self.root / 'fixture.key'
        self.key.write_bytes(b'0123456789abcdef0123456789abcdef')
        self.key.chmod(0o600)
        cli = find_sqlcipher()
        if cli is None:
            self.skipTest('official SQLCipher CLI required')
        for src in self.plain.rglob('*.db'):
            dst = self.snapshot / src.relative_to(self.plain)
            dst.parent.mkdir(parents=True, exist_ok=True)
            script = f"ATTACH DATABASE '{dst}' AS encrypted KEY '0123456789abcdef0123456789abcdef';\nSELECT sqlcipher_export('encrypted');\nDETACH DATABASE encrypted;\n"
            p = subprocess.run([str(cli), str(src)], input=script, text=True, capture_output=True)
            self.assertEqual(p.returncode, 0, 'fixture encryption failed')

    def execute(self, **kwargs):
        return process_snapshot(snapshot=self.snapshot, passphrase_file=self.key, cfg=self.cfg,
                                run_id='test-run', snapshot_id='fixture', **kwargs)

    def test_real_codec_to_published_archive(self):
        before = {p: sha256_file(p) for p in self.snapshot.rglob('*.db')}
        states = []
        out = self.execute(progress=lambda state, *_: states.append(state))
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertEqual(manifest['source_kind'], 'live-db')
        self.assertEqual(manifest['backup2_coverage'], 'unverified')
        self.assertEqual(manifest['record_count'], 5)
        self.assertEqual(manifest['source_snapshot_id'], 'fixture')
        conn = sqlite3.connect(out / 'archive.sqlite')
        self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        conn.close()
        self.assertEqual(before, {p: sha256_file(p) for p in before})
        self.assertEqual(states[-1], 'awaiting_sample_check')
        from wechat_export.scratch import NAMESPACE
        self.assertTrue((self.cfg.work_root/NAMESPACE).is_dir())
        self.assertEqual(list((self.cfg.work_root/NAMESPACE).glob('scratch-*')), [])
        self.assertEqual(list((self.cfg.work_root/'test-run/decrypted').rglob(NAMESPACE)), [])

    def test_bad_key_never_publishes(self):
        self.key.write_bytes(b'x'*32)
        with self.assertRaises(PipelineError):
            self.execute()
        self.assertFalse((self.cfg.exports_root / 'test-run').exists())

    def test_cancel_never_publishes(self):
        with self.assertRaises(PipelineCancelled):
            self.execute(cancelled=lambda: True)
        self.assertFalse((self.cfg.exports_root / 'test-run').exists())

    def test_no_overwrite(self):
        self.execute()
        with self.assertRaises(PipelineError):
            self.execute()

    def test_cancel_inside_codec_preserves_cancelled_status(self):
        from unittest.mock import patch
        from wechat_export.sqlcipher4 import decrypt_page
        state = {'page_done': False}
        def one_page(*args, **kwargs):
            result = decrypt_page(*args, **kwargs)
            state['page_done'] = True
            return result
        with patch('wechat_export.sqlcipher4.decrypt_page', side_effect=one_page):
            with self.assertRaises(PipelineCancelled):
                self.execute(cancelled=lambda: state['page_done'])
        self.assertFalse((self.cfg.exports_root/'test-run').exists())
        work = self.cfg.work_root/'test-run'
        ledger = json.loads((work/'source-ledger.json').read_text())
        self.assertEqual(ledger['state'], 'cancelled')
        self.assertEqual(ledger['databases'][0]['status'], 'cancelled')
        self.assertFalse(list((work/'decrypted').rglob('*.db')))
        self.assertFalse(list((work/'decrypted').rglob('.decrypt-*')))

    def test_pipeline_never_materializes_database_with_read_bytes(self):
        from unittest.mock import patch
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole file read')):
            out = self.execute()
        self.assertTrue((out/'manifest.json').is_file())

    def test_external_output_stages_on_destination_not_work_volume(self):
        from unittest.mock import patch
        from wechat_export.runtime import resolve_runtime, resolve_source_id
        from wechat_export.output_locations import OutputLocations
        runtime = resolve_runtime(self.cfg.data_root)
        selected = self.root / 'separate-output'; selected.mkdir()
        locations = OutputLocations(runtime)
        item = locations.register_native_selection(selected)
        parent = locations.resolve(item['destination_id'])
        original_rename = os.rename
        final_renames = []
        def enforce_destination_staging(src, dst, *args, **kwargs):
            # Emulate the EXDEV failure the previous work-root staging incurred.
            if Path(dst) == parent / 'test-run':
                if not Path(src).is_relative_to(parent):
                    import errno
                    raise OSError(errno.EXDEV, 'synthetic separate destination filesystem')
                final_renames.append((src, dst))
            return original_rename(src, dst, *args, **kwargs)
        with patch('os.rename', side_effect=enforce_destination_staging):
            out = self.execute(output_parent=parent)
        self.assertEqual(out, parent / 'test-run')
        self.assertEqual(len(final_renames), 1)
        self.assertFalse((self.cfg.exports_root / 'test-run').exists())
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertEqual(manifest['record_count'], 5)
        self.assertEqual(manifest['source_kind'], 'live-db')
        self.assertEqual(manifest['backup2_coverage'], 'unverified')
        sid = locations.source_id(item['destination_id'], out.name)
        self.assertEqual(resolve_source_id(sid, runtime), out)

    def test_insufficient_estimate_blocks_before_decryption(self):
        from unittest.mock import patch
        with patch('wechat_export.storage_plan.estimate_storage', return_value={'estimate_satisfied': False}), \
             patch('wechat_export.read_pipeline.decrypt_one') as decrypt:
            with self.assertRaisesRegex(PipelineError, 'insufficient_estimated_space'):
                self.execute()
        decrypt.assert_not_called()
        self.assertFalse((self.cfg.exports_root / 'test-run').exists())
