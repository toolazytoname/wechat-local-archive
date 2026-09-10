"""Regression coverage for fourth-review data isolation and publication bugs."""
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from wechat_export.insights import store as module
from wechat_export.insights.store import open_store, namespace_for_archive, InsightsError
from wechat_export.insights.identity import save_identity, load_identity
from wechat_export.learning.importer import _upsert_item, paste_body, get_item
from wechat_export.learning.notes import save_note
from wechat_export.insights.profile_validate import classify_statement_support


def seed(data):
    s = open_store(data, 'old')
    save_identity(s, {'self_sender_ids': ['account_a'], 'verification_state': 'consistent'})
    item, _ = _upsert_item(s, kind='link', title='Synthetic', canonical_key='synthetic', content_state='title_only')
    paste_body(s, item, 'SYNTHETIC FULL BODY')
    note = save_note(s, item, 'OLD NOTE')
    s.close()
    return item, note


class ReviewR4Tests(unittest.TestCase):
    def test_unbound_single_old_archive_not_adopted(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);seed(d/'data')
            s=open_store(d/'data','different',archive_root=d/'account_b')
            self.assertIsNone(load_identity(s));self.assertEqual(s.get_meta('migration_status'),'needs_recovery');s.close()

    def test_trusted_root_binding_survives_revision_change(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);seed(d/'data');root=d/'archive'
            s=open_store(d/'data','old');s.set_meta('archive_root',str(root.resolve()));s.close()
            s=open_store(d/'data','new',archive_root=root)
            self.assertIsNotNone(load_identity(s));self.assertEqual(s.get_meta('migration_status'),'migrated');s.close()

    def test_broken_existing_user_data_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);data=d/'data';root=d/'archive'
            current=open_store(data,'new',archive_root=root)
            item,_=_upsert_item(current,kind='link',title='Synthetic',canonical_key='synthetic',content_state='title_only')
            paste_body(current,item,'NEW BODY');save_note(current,item,'NEW NOTE')
            for path in current.content_root.glob('*.txt'):path.unlink()
            current.close()
            seed(data)
            s=open_store(data,'old',archive_root=root)
            self.assertEqual(s.get_meta('migration_status'),'conflict')
            self.assertEqual(get_item(s,item)['notes'][0]['user_text'],'NEW NOTE');s.close()

    def test_corrupt_source_is_not_published(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);data=d/'data';root=d/'archive';seed(data)
            for path in (data/'insights'/'old'/'content').glob('*.txt'):path.write_text('corrupt')
            with self.assertRaises(OSError):open_store(data,'old',archive_root=root)
            s=module.InsightStore(data/'insights'/namespace_for_archive(root))
            self.assertIsNone(load_identity(s));self.assertEqual(s.get_meta('migration_status'),'failed');s.close()

    def test_partial_destination_file_repaired_on_retry(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);data=d/'data';root=d/'archive';item,_=seed(data)
            target=data/'insights'/namespace_for_archive(root)/'content';copy=module._copy_content_files
            def fail(src,dest):
                if dest==target:
                    source=next(src.glob('*.txt'));dest.mkdir(exist_ok=True);(dest/source.name).write_bytes(b'SYN');raise OSError('synthetic failure')
                return copy(src,dest)
            with patch.object(module,'_copy_content_files',side_effect=fail):
                with self.assertRaises(OSError):open_store(data,'old',archive_root=root)
            s=open_store(data,'old',archive_root=root);content=get_item(s,item)['contents'][-1]
            self.assertEqual(content['body'],'SYNTHETIC FULL BODY');self.assertEqual(hashlib.sha256(content['body'].encode()).hexdigest(),content['body_hash']);s.close()

    def test_migration_in_progress_does_not_expose_writable_target(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);data=d/'data';root=d/'archive';item,note=seed(data)
            target=data/'insights'/namespace_for_archive(root)/'content';copy=module._copy_content_files
            ready=threading.Event();release=threading.Event()
            def pause(src,dest):
                if dest==target:
                    ready.set()
                    if not release.wait(5):raise RuntimeError('test timeout')
                return copy(src,dest)
            def worker():
                s=open_store(data,'old',archive_root=root);s.close()
            with patch.object(module,'_copy_content_files',side_effect=pause),ThreadPoolExecutor(1) as pool:
                future=pool.submit(worker)
                try:
                    self.assertTrue(ready.wait(5))
                    with self.assertRaises(InsightsError) as ctx:open_store(data,'old',archive_root=root)
                    self.assertEqual(ctx.exception.code,'migration_in_progress')
                finally:release.set()
                future.result(5)
            s=open_store(data,'old',archive_root=root);save_note(s,item,'NEW NOTE',note_id=note['note_id'],revision=1);s.close()
            s=open_store(data,'old',archive_root=root);self.assertEqual(get_item(s,item)['notes'][0]['user_text'],'NEW NOTE');s.close()

    def test_process_exit_releases_flock_and_old_directory_lock_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);data=d/'data';root=d/'archive';seed(data);ns=namespace_for_archive(root)
            (data/'insights'/f'{ns}.migrate.lock').mkdir()
            code="import os,fcntl,sys; f=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(f,fcntl.LOCK_EX);os._exit(0)"
            subprocess.run([sys.executable,'-c',code,str(data/'insights'/f'{ns}.open.lock')],check=True)
            s=open_store(data,'old',archive_root=root);self.assertEqual(s.get_meta('migration_status'),'migrated');s.close()

    def test_excerpt_preserves_pronouns_conditions_and_negation(self):
        for statement,source in [('我想去北京。','他想去北京。'),('我想辞职','如果我想辞职，就会先告诉你。')]:
            self.assertNotEqual(classify_statement_support(statement,source),'excerpt')
        for text in ['我不想辞职。','如果我想辞职，就会先告诉你。','他想去北京。']:
            self.assertEqual(classify_statement_support(text,text),'excerpt')
