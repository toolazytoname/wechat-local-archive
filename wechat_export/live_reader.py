"""Explicitly authorized, copy-only experimental Mac reader coordinator.

No original signing, security-policy changes, UI clicks or automatic rollback.
All commands are argv arrays, bounded, with subprocess output kept out of HTTP.
"""
from __future__ import annotations
import json
import hashlib
import os
import plistlib
import subprocess
from pathlib import Path
from typing import Callable

from wechat_export.fsutil import ensure_dir, write_json
from wechat_export.build_fingerprint import fingerprint_bundle, MACHO, SCHEMA as FINGERPRINT_SCHEMA
from wechat_export.livedb_snapshot import wechat_pids
from wechat_export.key_capture import capture_kdf
from wechat_export.sqlcipher4 import derive_raw_key, verify_database_pages, read_prefix

class ReaderError(RuntimeError):
    pass


def run(args, timeout=60):
    proc = subprocess.run(args, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise ReaderError('reader_command_failed')
    return proc.stdout


def original_identity(app: Path) -> str:
    run(['codesign', '--verify', '--deep', '--strict', str(app)])
    proc = subprocess.run(['codesign', '-dv', '--verbose=4', str(app)], capture_output=True, timeout=15)
    text = proc.stderr.decode(errors='replace')
    if proc.returncode or 'Developer ID Application: Tencent' not in text or 'runtime' not in text:
        raise ReaderError('original_signature_unexpected')
    return fingerprint_bundle(app)['sha256']


def checked_cancel(cancelled):
    if cancelled():
        raise ReaderError('cancelled')


def read_entitlements(path: Path) -> dict:
    proc = subprocess.run(['codesign', '-d', '--entitlements', ':-', str(path)],
                          capture_output=True, timeout=15)
    if proc.returncode:
        raise ReaderError('entitlements_unreadable')
    blob = proc.stdout.strip()
    if not blob:
        return {}
    try:
        values = plistlib.loads(blob)
    except (ValueError, plistlib.InvalidFileException):
        raise ReaderError('entitlements_unreadable') from None
    if not isinstance(values, dict):
        raise ReaderError('entitlements_unreadable')
    return values


def library_exception_target(path: Path, copy: Path) -> bool:
    # Only the root application and its main executable, plus explicitly named
    # helper application bundles and their own main executables. A framework
    # binary merely named WeChat is not an exception target.
    if path in {copy, copy / 'Contents/MacOS/WeChat'}:
        return True
    helper = path if path.name == 'WeChatHelper.app' else (
        path.parent.parent.parent if path.name == 'WeChatHelper' and
        path.parent.name == 'MacOS' and path.parent.parent.name == 'Contents' else None)
    if not helper or helper.name != 'WeChatHelper.app' or not helper.is_relative_to(copy):
        return False
    try:
        with (helper / 'Contents/Info.plist').open('rb') as stream:
            blob = stream.read(1024 * 1024 + 1)
        if len(blob) > 1024 * 1024:
            raise ValueError()
        info = plistlib.loads(blob)
        return (info.get('CFBundleIdentifier') == 'com.tencent.xinWeChat.WeChatHelper' and
                info.get('CFBundleExecutable') == 'WeChatHelper')
    except (OSError, ValueError, TypeError, AttributeError):
        raise ReaderError('helper_identity_unverified') from None


def prepare_copy(*, app: Path, account: Path, work: Path,
                 cancelled: Callable[[], bool]) -> Path:
    """Caller must hold exclusive live-operation lock and a consumed job grant."""
    checked_cancel(cancelled)
    if wechat_pids():
        raise ReaderError('wechat_running')
    before = original_identity(app)
    ensure_dir(work)
    preserved = ensure_dir(work / 'preserved')
    # Preserve writable small account/global state; message media is untouched.
    candidates = [account / 'config', account.parent / 'all_users']
    container_library = account.parent.parent.parent / 'Library'
    candidates += [container_library / p for p in ('Preferences', 'HTTPStorages', 'Application Support')]
    for i, src in enumerate(candidates):
        checked_cancel(cancelled)
        if src.exists():
            run(['ditto', str(src), str(preserved / str(i))], timeout=600)
    copy = work / 'WeChat-debug.app'
    if copy.exists():
        raise ReaderError('debug_copy_exists')
    run(['ditto', str(app), str(copy)], timeout=600)
    # Sign actual Mach-O files inside-out, then their bundle resource seals.
    magic = MACHO
    binaries = []
    for path in copy.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as fh:
                if fh.read(4) in magic:
                    binaries.append(path)
    bundles = [p for p in copy.rglob('*') if p.is_dir() and not p.is_symlink()
               and p.suffix in {'.app', '.framework', '.xpc', '.appex'}]
    expected_entitlements = {}
    signing_audit = []
    for path in sorted(binaries, key=lambda p: len(p.parts), reverse=True) + sorted(bundles, key=lambda p: len(p.parts), reverse=True) + [copy]:
        checked_cancel(cancelled)
        args = ['codesign', '--force', '--sign', '-', '--timestamp=none']
        relative = path.relative_to(copy)
        # Read the original, not a copy already altered by an earlier sign pass.
        values = dict(read_entitlements(app / relative))
        exception = library_exception_target(path, copy)
        if exception:
            values['com.apple.security.cs.disable-library-validation'] = True
        expected_entitlements[str(path)] = values
        if values:
            entfile = work / 'sign-entitlements.plist'
            entfile.write_bytes(plistlib.dumps(values))
            os.chmod(entfile, 0o600)
            args += ['--entitlements', str(entfile)]
        run(args + [str(path)])
        signing_audit.append({'path': relative.as_posix(), 'library_exception_target': exception,
                              'expected_entitlements_sha256': hashlib.sha256(plistlib.dumps(values, sort_keys=True)).hexdigest()})
    for name, expected in expected_entitlements.items():
        checked_cancel(cancelled)
        if read_entitlements(Path(name)) != expected:
            raise ReaderError('signed_entitlements_mismatch')
    run(['codesign', '--verify', '--deep', str(copy)])
    if original_identity(app) != before:
        raise ReaderError('original_changed')
    write_json(work / 'signing-audit.json', {'schema': 'wechat-copy-signing/1', 'entitlements_verified': True,
                                           'copy_signature_verified': True, 'targets': signing_audit,
                                           'original_signing_attempted': False})
    write_json(work / 'reader-prepared.json', {'original_bundle_sha256': before,
               'debug_bundle_sha256': fingerprint_bundle(copy, cancelled=cancelled)['sha256'],
               'fingerprint_schema': FINGERPRINT_SCHEMA,
               'original_app': str(app), 'debug_copy': str(copy),
               'limitations': ['debug copy shares account data', 'keychain/TCC not backed up', 'media not cloned']})
    return copy


def acquire_key(*, app: Path, copy: Path, snapshot: Path, output: Path,
                cancelled: Callable[[], bool]) -> dict:
    checked_cancel(cancelled)
    if wechat_pids():
        raise ReaderError('wechat_running')
    identity = original_identity(app)
    prepared = json.loads((copy.parent / 'reader-prepared.json').read_text())
    if prepared.get('fingerprint_schema') != FINGERPRINT_SCHEMA or prepared.get('original_bundle_sha256') != identity:
        raise ReaderError('original_changed_since_prepare')
    run(['codesign', '--verify', '--deep', str(copy)])
    if fingerprint_bundle(copy, cancelled=cancelled)['sha256'] != prepared.get('debug_bundle_sha256'):
        raise ReaderError('debug_copy_changed_since_prepare')
    original_reopened = [False]
    def stop_capture():
        if cancelled():
            return True
        proc = subprocess.run(['pgrep', '-f', r'^/Applications/WeChat.app/Contents/MacOS/WeChat( |$)'],
                              capture_output=True, timeout=5)
        if proc.returncode != 1:
            original_reopened[0] = True  # inspection failure also stops the reader
            return True
        return False
    try:
        status, hits = capture_kdf(copy / 'Contents/MacOS/WeChat', timeout=120, max_hits=32,
                                   cancelled=stop_capture)
        if original_reopened[0]:
            raise ReaderError('original_reopened_or_inspection_failed')
        checked_cancel(cancelled)
        # A candidate must authenticate every page of contact and every message DB.
        targets = [snapshot / 'contact/contact.db'] + sorted((snapshot / 'message').glob('message_*.db'))
        targets = [p for p in targets if 'fts' not in p.name and 'resource' not in p.name]
        if len(targets) < 2 or not all(p.is_file() for p in targets):
            raise ReaderError('required_database_missing')
        seen = set()
        for hit in hits:
            if hit.length != 32 or hit.material in seen:
                continue
            seen.add(hit.material)
            try:
                for db in targets:
                    checked_cancel(cancelled)
                    verify_database_pages(db, derive_raw_key(hit.material, read_prefix(db)),
                                          check=lambda: checked_cancel(cancelled))
            except ReaderError:
                raise
            except Exception:
                continue
            ensure_dir(output.parent)
            # Exclusive private key creation, never overwrite another job's key.
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as fh:
                fh.write(hit.material)
            return {'hmac_verified': True, 'databases_verified': len(targets),
                    'breakpoint_hit': status.breakpoint_hit}
        raise ReaderError('no_authenticated_candidate')
    finally:
        if original_identity(app) != identity:
            raise ReaderError('original_changed')
