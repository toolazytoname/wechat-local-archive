"""Only a complete same-destination tree becomes visible as a full archive."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wechat_export.archive_publication import ArchivePublication
from wechat_export.scratch import NAMESPACE, sweep


class ArchivePublicationTests(unittest.TestCase):
    def test_publish_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td).resolve()
            with ArchivePublication(parent, 'new-archive') as publication:
                stage = publication.staging_data_root / 'exports/new-archive'
                stage.mkdir(parents=True)
                (stage/'manifest.json').write_text('{}')
                self.assertEqual(list(publication.final.iterdir()), [])
                self.assertEqual(stage.stat().st_dev, parent.stat().st_dev)
                self.assertEqual(publication.publish(stage), parent/'new-archive')
            self.assertTrue((parent/'new-archive/manifest.json').exists())
            self.assertEqual(list((parent/NAMESPACE).glob('scratch-*')), [])
            with self.assertRaises(FileExistsError):
                with ArchivePublication(parent,'new-archive'): self.fail('overwrite')

    def test_cancel_and_occupied_reservation(self):
        with tempfile.TemporaryDirectory() as td:
            parent=Path(td).resolve()
            with self.assertRaises(InterruptedError):
                with ArchivePublication(parent,'cancelled') as publication:
                    stage=publication.staging_data_root/'stage';stage.mkdir()
                    def cancel(): raise InterruptedError('synthetic cancel')
                    publication.publish(stage,cancel)
            self.assertFalse((parent/'cancelled').exists())
            with self.assertRaises(FileExistsError):
                with ArchivePublication(parent,'occupied') as publication:
                    stage=publication.staging_data_root/'stage';stage.mkdir()
                    (publication.final/'foreign').write_text('retain')
                    publication.publish(stage)
            self.assertEqual((parent/'occupied/foreign').read_text(),'retain')

    def test_parent_replacement_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td).resolve();parent=base/'out';parent.mkdir()
            alias=base/'alias';alias.symlink_to(parent,target_is_directory=True)
            with self.assertRaises(ValueError): ArchivePublication(alias,'test')
            with self.assertRaises(ValueError):
                with ArchivePublication(parent,'test') as publication:
                    stage=publication.staging_data_root/'stage';stage.mkdir()
                    parent.rename(base/'old');parent.mkdir()
                    publication.publish(stage)
            self.assertFalse((parent/'test').exists())

    def test_killed_builder_leaves_no_partial_final_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            script='''import sys,time
from pathlib import Path
from wechat_export.archive_publication import ArchivePublication
with ArchivePublication(Path(sys.argv[1]),'crashed') as p:
 s=p.staging_data_root/'exports/crashed/all';s.mkdir(parents=True)
 (s/'messages.jsonl').write_text('synthetic incomplete')
 print('ready',flush=True)
 time.sleep(60)
'''
            child=subprocess.Popen([sys.executable,'-c',script,str(root)],stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'ready')
                child.kill();child.wait(timeout=5)
            finally:
                if child.poll() is None: child.kill();child.wait(timeout=5)
                child.stdout.close()
            self.assertEqual(list((root/'crashed').iterdir()),[])
            import time
            result=sweep(root,apply=True,now=time.time()+90000)
            self.assertEqual(result['removed'],1)
            self.assertTrue((root/'crashed').exists())  # Empty reservations are not broad purge targets.
