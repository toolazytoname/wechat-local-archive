import json,tempfile,unittest,zipfile
from pathlib import Path
from tests.test_export_service import _demo_tree
from wechat_export.ai_bundle import export_bundles,validate_scopes

class AIBundleTests(unittest.TestCase):
    def test_three_scopes_and_raw_fidelity(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=_demo_tree(root/'archive')
            scopes=[{'name':'private','kind':'conversations','conversation_ids':['wxid_alice']},
                    {'name':'group','kind':'conversations','conversation_ids':['room@chatroom']},
                    {'name':'all','kind':'all'}]
            before=(src/'all/messages.jsonl').read_bytes()
            result=export_bundles(src,root/'out',scopes)
            self.assertEqual([s['count'] for s in result['scopes']],[3,1,4])
            self.assertEqual((root/'out/all/raw-local-only/messages.jsonl').read_bytes(),before)
            self.assertEqual((src/'all/messages.jsonl').read_bytes(),before)
            for scope in scopes:
                out=root/'out'/scope['name'];m=json.loads((out/'manifest.json').read_text())
                self.assertEqual(m['backup2_coverage'],'unverified');self.assertEqual(m['attachment_binary_files_included'],0)
                self.assertEqual(sum(c['count'] for c in m['chunks']),m['record_count'])
                with zipfile.ZipFile(root/'out'/(scope['name']+'.zip')) as z:
                    self.assertIsNone(z.testzip())
                    self.assertFalse(any(n.startswith('raw-local-only/') for n in z.namelist()))
                    import hashlib
                    for line in z.read('SHA256SUMS').decode().splitlines():
                        digest,name=line.split('  ',1)
                        self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
            with self.assertRaises(FileExistsError):export_bundles(src,root/'out',scopes)
    def test_invalid_scope_does_not_become_all(self):
        for scopes in [[{'name':'x','kind':'conversations','conversation_ids':[]}],[{'name':'../bad','kind':'all'}],
                       [{'name':'x','kind':'all','conversation_ids':[]}],[{'name':'x','kind':'conversations','conversation_ids':['unknown']}]]:
            with self.assertRaises(ValueError):validate_scopes(scopes,{'known'})
