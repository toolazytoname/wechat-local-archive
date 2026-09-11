"""Release guard tests use only fictional content in disposable Git repositories."""
import contextlib
import hashlib
import io
import json
import subprocess
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.privacy_audit import classify, main, scan


class PrivacyAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Reviewer')
        self.git('config', 'user.email', 'reviewer@example.invalid')
        (self.root/'.gitignore').write_text('data/\n')
        (self.root/'README.md').write_text('Fictional project.\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic baseline')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)

    def test_ignored_private_files_are_never_read(self):
        private = self.root/'data'; private.mkdir()
        (private/'secret.raw').write_bytes(b'do not read')
        original = Path.read_bytes
        def guarded(path):
            if path.is_relative_to(private): raise AssertionError('private read')
            return original(path)
        with patch.object(Path, 'read_bytes', guarded):
            result = scan(self.root)
        self.assertTrue(result['rule_scan_passed'])
        self.assertEqual(result['scanned_files'], 2)

    def test_forced_tracked_private_path_blocks_without_content_or_path_leak(self):
        private = self.root/'data'; private.mkdir()
        key = private/'fictional-identity.raw'; key.write_bytes(b'unread material')
        self.git('add', '-f', 'data/fictional-identity.raw')
        original = Path.read_bytes
        def guarded(path):
            if path == key: raise AssertionError('should classify without reading')
            return original(path)
        with patch.object(Path, 'read_bytes', guarded): result = scan(self.root)
        blob = json.dumps(result)
        self.assertFalse(result['rule_scan_passed'])
        self.assertIn('private-runtime-path', blob)
        self.assertNotIn('fictional-identity', blob)
        self.assertNotIn('unread material', blob)

    def test_current_fix_does_not_hide_history(self):
        needle = b'fictional-personal-identifier'
        (self.root/'README.md').write_bytes(needle)
        self.git('add', '.'); self.git('commit', '-qm', 'synthetic unsafe revision')
        (self.root/'README.md').write_text('Fictional safe text')
        self.git('add', '.'); self.git('commit', '-qm', 'synthetic repair')
        self.assertTrue(scan(self.root, scope='head', needles=(needle,))['rule_scan_passed'])
        old = scan(self.root, scope='history', needles=(needle,))
        self.assertEqual(old['revision_count'], 3)
        self.assertFalse(old['rule_scan_passed'])
        self.assertNotIn(needle.decode(), json.dumps(old))

    def test_opaque_asset_requires_exact_reviewed_hash(self):
        (self.root/'demo.png').write_bytes(b'\x89PNG\0synthetic')
        self.assertFalse(scan(self.root)['rule_scan_passed'])
        asset = {'demo.png': {'sha256': hashlib.sha256((self.root/'demo.png').read_bytes()).hexdigest(),
                              'review': 'Synthetic byte fixture'}}
        self.assertTrue(scan(self.root, assets=asset)['rule_scan_passed'])
        (self.root/'demo.png').write_bytes(b'changed\0')
        self.assertFalse(scan(self.root, assets=asset)['rule_scan_passed'])

    def test_symlink_does_not_read_target(self):
        (self.root/'alias.md').symlink_to(self.root/'README.md')
        result = scan(self.root)
        self.assertIn('symlink-not-reviewed', json.dumps(result))

    def test_secret_patterns_report_only_categories(self):
        token = b'ghp_' + b'A'*40
        pem = b'-----BEGIN ' + b'PRIVATE KEY-----'
        key = b'"enc_key_hex": "' + b'ab'*32 + b'"'
        data = b'\n'.join((token, pem, key))
        rules = classify('sample.py', data, (), {})
        self.assertEqual(set(rules), {'credential-token', 'private-key-block', 'literal-database-key'})
        (self.root/'sample.py').write_bytes(data)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = main(['--repo', str(self.root)])
        self.assertEqual(code, 1)
        for secret in (token, pem, key): self.assertNotIn(secret.decode(), out.getvalue())

    def test_untracked_release_files_are_scanned(self):
        needle = b'fictional-sensitive-string'
        (self.root/'new.md').write_bytes(needle)
        self.assertFalse(scan(self.root, needles=(needle,))['rule_scan_passed'])

    def test_invalid_needle_file_does_not_echo_input(self):
        path = self.root/'needles.json'; path.write_text('not-json fictional private content')
        path.chmod(0o600)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = main(['--repo', str(self.root), '--needles-file', str(path)])
        self.assertEqual(code, 2)
        self.assertNotIn('fictional', out.getvalue())
        self.assertNotIn(str(path), out.getvalue())

    def test_private_needle_file_used_without_copying_values_to_report(self):
        with tempfile.TemporaryDirectory() as private:
            path = Path(private)/'needles.json'
            path.write_text(json.dumps(['fictional-identity']))
            path.chmod(0o600)
            (self.root/'sample.md').write_text('fictional-identity')
            out = io.StringIO()
            with contextlib.redirect_stdout(out): code = main(['--repo', str(self.root), '--needles-file', str(path)])
            self.assertEqual(code, 1)
            self.assertNotIn('fictional-identity', out.getvalue())
            self.assertTrue(json.loads(out.getvalue())['local_needles_used'])

    def test_secret_in_filename_is_redacted(self):
        token = 'ghp_' + 'Z'*40
        (self.root/(token+'.md')).write_text('ordinary text')
        result = scan(self.root)
        # A credential-shaped name also fails the guard without leaking its text.
        self.assertFalse(result['rule_scan_passed'])
        self.assertNotIn(token, json.dumps(result))

    def test_chat_dataset_needs_review_even_without_known_identity_match(self):
        (self.root/'messages.jsonl').write_text('{"text":"fictional example"}\n')
        self.assertIn('unreviewed-chat-data', json.dumps(scan(self.root)))

    def test_public_demo_identity_and_counts_agree(self):
        root = Path(__file__).resolve().parents[1]
        examples = root/'examples/demo-export'
        messages = [json.loads(line) for line in (examples/'all/messages.jsonl').read_text().splitlines()]
        manifest = json.loads((examples/'manifest.json').read_text())
        self.assertEqual(len(messages), manifest['record_count'])
        conn = sqlite3.connect((examples/'archive.sqlite').resolve().as_uri()+'?mode=ro', uri=True)
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0], len(messages))
        finally:
            conn.close()
        self.assertEqual((root/'wechat_export/demo/all/messages.jsonl').read_bytes(),
                         (examples/'all/messages.jsonl').read_bytes())
        for row in messages:
            self.assertEqual(row['is_self'], row['sender_id'] == 'me')

    def test_installed_demo_upgrade_preserves_old_copy(self):
        from wechat_export.runtime import demo_export_dir
        with tempfile.TemporaryDirectory() as td:
            old = Path(td)/'demo-v3'; old.mkdir()
            marker = old/'keep.txt'; marker.write_text('previous synthetic version')
            with patch('wechat_export.runtime.source_checkout_root', return_value=None), patch.dict(
                    'os.environ', {'WECHAT_EXPORT_DATA_ROOT': td}):
                new = demo_export_dir()
            self.assertEqual(new.name, 'demo-v4')
            self.assertEqual(marker.read_text(), 'previous synthetic version')
            rows = [json.loads(line) for line in (new/'all/messages.jsonl').read_text().splitlines()]
            self.assertTrue(all(row['sender_id'] == 'wxid_alice' for row in rows if row['record_uid'].startswith('card-')))
            self.assertTrue((new/'media/msg/attach/29a6db07e8bbdb53f5d54cc3c309f3f1/2026-01/Img/ca6e9184bdd5d8dce96b4d2b37355164_t.dat').is_file())
            self.assertTrue(any('我想每周整理一次阅读笔记' in row.get('text','') for row in rows if row.get('is_self')))

    def test_forced_tracked_disposable_plaintext_is_always_private(self):
        path = self.root/'.wla-scratch-v1'
        path.mkdir(); (path/'plain.db').write_bytes(b'fictional plaintext')
        self.git('add', '.wla-scratch-v1/plain.db')
        result = scan(self.root)
        self.assertFalse(result['rule_scan_passed'])
        self.assertIn('private-runtime-path', json.dumps(result))
        self.assertNotIn('fictional plaintext', json.dumps(result))

class JavaScriptModuleAuditTests(unittest.TestCase):
    def test_commonjs_and_modules_are_scanned_not_opaque(self):
        from wechat_export.privacy_audit import classify
        for extension in ('cjs', 'mjs'):
            self.assertEqual(classify('tests/example.' + extension, b'const fixture = true;', (), {}), [])
            credential = b'gh' + b'p_' + b'A' * 36
            self.assertIn('credential-token', classify('tests/example.' + extension, credential, (), {}))
