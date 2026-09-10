"""Bounded authenticated database reading and exclusive plaintext publication."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_sqlcipher4 import _encrypt_page
from wechat_export.sqlcipher4 import (PAGE_SIZE, RESERVE_SIZE, SALT_SIZE, SQLITE_HEADER,
    SqlCipherError, PageHmacError, decrypt_database, verify_database_pages)
from wechat_export.sqlcipher_cli import export_plaintext
from wechat_export.scratch import NAMESPACE

KEY = bytes(range(32))


def fixture(path, count=3):
    with path.open('wb') as out:
        for pgno in range(1, count + 1):
            out.write(_encrypt_page(KEY, b's'*16, b'i'*16,
                bytes(PAGE_SIZE-RESERVE_SIZE-(SALT_SIZE if pgno == 1 else 0)), pgno))


class StreamCodecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root/'source.db'; self.dst = self.root/'output.db'
        fixture(self.src)

    def assert_no_plaintext(self):
        self.assertFalse(self.dst.exists())
        self.assertFalse(list(self.root.glob('.decrypt-*')))
        self.assertFalse(list(self.root.glob('.sqlcipher-plain-*')))
        self.assertFalse(list((self.root/NAMESPACE).glob('scratch-*')))

    def test_no_read_bytes_on_verification_or_decryption(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole file read')):
            self.assertEqual(verify_database_pages(self.src, KEY), 3)
            report = decrypt_database(self.src, self.dst, KEY)
        self.assertEqual(report['pages'], 3)
        self.assertEqual(report['bytes_in'], 3*PAGE_SIZE)
        self.assertEqual(self.dst.stat().st_mode & 0o777, 0o600)
        with self.dst.open('rb') as stream:
            self.assertEqual(stream.read(16), SQLITE_HEADER)

    def test_corrupt_final_page_removes_staging(self):
        with self.src.open('r+b') as out:
            out.seek(-1, os.SEEK_END); out.write(b'!')
        with self.assertRaises(PageHmacError) as caught:
            decrypt_database(self.src, self.dst, KEY)
        self.assertEqual(caught.exception.page, 3)
        self.assert_no_plaintext()

    def test_cancel_midstream_removes_staging(self):
        calls = 0
        class Cancelled(Exception): pass
        def check():
            nonlocal calls
            calls += 1
            if calls == 5:
                raise Cancelled()
        with self.assertRaises(Cancelled):
            decrypt_database(self.src, self.dst, KEY, check=check)
        self.assert_no_plaintext()

    def test_source_replacement_and_append_abort(self):
        for replacement in (False, True):
            fixture(self.src)
            calls = 0
            def check():
                nonlocal calls
                calls += 1
                if calls == 6:  # after the third page, before final stamp check
                    if replacement:
                        other = self.root/'replacement.db'; fixture(other)
                        other.replace(self.src)
                    else:
                        with self.src.open('ab') as out: out.write(b'x')
            with self.subTest(replacement=replacement), self.assertRaises(SqlCipherError):
                decrypt_database(self.src, self.dst, KEY, check=check)
            self.assert_no_plaintext()

    def test_existing_destination_untouched(self):
        self.dst.write_bytes(b'keep')
        with self.assertRaises(SqlCipherError):
            decrypt_database(self.src, self.dst, KEY)
        self.assertEqual(self.dst.read_bytes(), b'keep')

    def test_concurrent_destination_not_overwritten(self):
        original = os.link
        def race(source, target):
            target.write_bytes(b'other job')
            return original(source, target)
        with patch('wechat_export.sqlcipher4.os.link', side_effect=race):
            with self.assertRaises(FileExistsError):
                decrypt_database(self.src, self.dst, KEY)
        self.assertEqual(self.dst.read_bytes(), b'other job')
        self.assertFalse(list(self.root.glob('.decrypt-*')))

    def test_cli_timeout_removes_partial_staging(self):
        def timeout(db, script, **kwargs):
            staged = next((self.root/NAMESPACE).glob('scratch-*'))/'payload/plain.db'
            staged.write_bytes(SQLITE_HEADER + b'incomplete')
            raise subprocess.TimeoutExpired('synthetic-sqlcipher', 1)
        with patch('wechat_export.sqlcipher_cli.run_sqlcipher', side_effect=timeout):
            with self.assertRaises(subprocess.TimeoutExpired):
                export_plaintext(self.src, self.dst, raw_key=KEY)
        self.assert_no_plaintext()

    def test_cli_success_header_check_is_bounded_and_private(self):
        def success(db, script, **kwargs):
            self.assertTrue(kwargs.get('lease_fds'))
            self.assertEqual(os.fstat(kwargs['lease_fds'][0]).st_size, 0)
            staged = next((self.root/NAMESPACE).glob('scratch-*'))/'payload/plain.db'
            staged.write_bytes(SQLITE_HEADER + b'synthetic')
            return subprocess.CompletedProcess([], 0, '', '')
        with patch('wechat_export.sqlcipher_cli.run_sqlcipher', side_effect=success), patch.object(
                Path, 'read_bytes', side_effect=AssertionError('whole file read')):
            export_plaintext(self.src, self.dst, raw_key=KEY)
        self.assertEqual(self.dst.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob('.sqlcipher-plain-*')))
        self.assertFalse(list((self.root/NAMESPACE).glob('scratch-*')))

    def test_all_codec_reads_are_bounded_to_a_page(self):
        original = Path.open
        reads = []
        class Guard:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def __getattr__(self, name): return getattr(self.stream, name)
            def read(self, size=-1):
                if not 0 <= size <= PAGE_SIZE:
                    raise AssertionError('unbounded codec read')
                reads.append(size)
                return self.stream.read(size)
        def guarded(path, mode='r', *args, **kwargs):
            stream = original(path, mode, *args, **kwargs)
            return Guard(stream) if mode == 'rb' else stream
        with patch.object(Path, 'open', guarded):
            verify_database_pages(self.src, KEY)
            decrypt_database(self.src, self.dst, KEY)
        self.assertEqual(reads.count(PAGE_SIZE), 6)

    def test_cli_cancel_after_command_does_not_publish(self):
        stopped = False
        class Cancelled(Exception): pass
        def check():
            if stopped: raise Cancelled()
        def success(db, script, **kwargs):
            nonlocal stopped
            staged = next((self.root/NAMESPACE).glob('scratch-*'))/'payload/plain.db'
            staged.write_bytes(SQLITE_HEADER + b'synthetic')
            stopped = True
            return subprocess.CompletedProcess([], 0, '', '')
        with patch('wechat_export.sqlcipher_cli.run_sqlcipher', side_effect=success):
            with self.assertRaises(Cancelled):
                export_plaintext(self.src, self.dst, raw_key=KEY, check=check)
        self.assert_no_plaintext()
