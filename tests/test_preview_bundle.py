import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('wla_preview_builder',Path(__file__).resolve().parents[1]/'scripts/build-preview.py')
builder=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(builder)


class PreviewBundleTests(unittest.TestCase):
    def test_deterministic_contents_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);files={'README.md':b'synthetic preview','scripts/launch.command':b'echo synthetic'}
            digest=builder.pack(files,root/'a.zip')
            self.assertEqual(digest,builder.pack(files,root/'b.zip'))
            self.assertEqual((root/'a.zip').read_bytes(),(root/'b.zip').read_bytes())
            self.assertEqual(digest,builder.pack(files,root/'a.zip'))
            with zipfile.ZipFile(root/'a.zip') as archive:
                manifest=json.loads(archive.read('wechat-local-archive-preview/bundle-manifest.json'))
                self.assertFalse(manifest['release_certified'])
                self.assertEqual(manifest['backup2_coverage'],'unverified')
                self.assertEqual(set(manifest['files']),set(files))
                self.assertTrue(archive.getinfo('wechat-local-archive-preview/scripts/launch.command').external_attr>>16&0o111)
            with self.assertRaises(FileExistsError):builder.pack({'README.md':b'different'},root/'a.zip')

    def test_path_escape_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'out.zip'
            for name in ('../secret','/absolute'):
                with self.assertRaises(ValueError):builder.pack({name:b'synthetic'},out)
            self.assertFalse(out.exists())
