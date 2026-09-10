"""Stable, bounded, read-only bundle fingerprints, including nested libraries.

Content inventory is stronger than a version string but is NOT signature/trust
verification. Callers still verify the original signature separately. No cache:
prepare and launch each observe the current files, not a remembered build label.
"""
from __future__ import annotations
import hashlib
import json
import os
import plistlib
import stat
import time
from pathlib import Path

SCHEMA = 'wechat-bundle-fingerprint/1'
CHUNK = 1024 * 1024
MACHO = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xfe\xed\xfa\xce',
         b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


class FingerprintError(ValueError):
    pass


def _stamp(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _open_file_under(root: Path, relative: str) -> int:
    parts = Path(relative).parts
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)


def fingerprint_bundle(app: Path, *, cancelled=lambda: False, max_files=100000,
                       max_bytes=8 * 1024 ** 3, timeout=90) -> dict:
    if app.is_symlink() or not app.is_dir():
        raise FingerprintError('bundle_missing_or_symlink')
    app = app.resolve(strict=True)
    deadline = time.monotonic() + timeout
    root_stamp = _stamp(app.stat())
    def check():
        if cancelled():
            raise FingerprintError('cancelled')
        if time.monotonic() > deadline:
            raise FingerprintError('bundle_fingerprint_timeout')
    def walk():
        entries = {}
        def fail(exc): raise exc
        for parent, dirs, files in os.walk(app, followlinks=False, onerror=fail):
            check()
            base = Path(parent)
            if len(base.relative_to(app).parts) > 64:
                raise FingerprintError('bundle_depth_limit')
            for name in sorted(dirs + files):
                check()
                path = base / name
                st = path.lstat()
                if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                    raise FingerprintError('bundle_special_file')
                if stat.S_ISLNK(st.st_mode):
                    try:
                        target = path.resolve(strict=True)
                    except (OSError, RuntimeError):
                        raise FingerprintError('bundle_invalid_symlink') from None
                    if not target.is_relative_to(app):
                        raise FingerprintError('bundle_external_symlink')
                entries[path.relative_to(app).as_posix()] = _stamp(st)
                if len(entries) > max_files:
                    raise FingerprintError('bundle_file_limit')
        return entries
    before = walk()
    records = []
    modules = []
    total = 0
    plist_data = None
    for name, stamp in sorted(before.items()):
        check()
        path = app / name
        mode = stamp[2]
        if stat.S_ISLNK(mode):
            records.append({'path': name, 'symlink': os.readlink(path)})
            continue
        if stat.S_ISDIR(mode):
            records.append({'path': name, 'directory': True})
            continue
        total += stamp[3]
        if total > max_bytes:
            raise FingerprintError('bundle_byte_limit')
        fd = _open_file_under(app, name)
        digest = hashlib.sha256()
        prefix = b''
        plist_chunks = [] if name == 'Contents/Info.plist' else None
        if plist_chunks is not None and stamp[3] > CHUNK:
            os.close(fd)
            raise FingerprintError('bundle_plist_oversize')
        with os.fdopen(fd, 'rb') as stream:
            if _stamp(os.fstat(stream.fileno())) != stamp:
                raise FingerprintError('bundle_changed')
            remaining = stamp[3]
            while remaining:
                check()
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    raise FingerprintError('bundle_truncated')
                if not prefix:
                    prefix = chunk[:4]
                digest.update(chunk)
                if plist_chunks is not None:
                    plist_chunks.append(chunk)
                remaining -= len(chunk)
            if stream.read(1) or _stamp(os.fstat(stream.fileno())) != stamp:
                raise FingerprintError('bundle_changed')
        entry = {'path': name, 'size': stamp[3], 'sha256': digest.hexdigest(), 'executable': bool(mode & 0o111)}
        records.append(entry)
        if prefix in MACHO:
            modules.append({'path': name, 'sha256': entry['sha256']})
        if plist_chunks is not None:
            plist_data = b''.join(plist_chunks)
    if _stamp(app.stat()) != root_stamp or walk() != before:
        raise FingerprintError('bundle_changed')
    try:
        info = plistlib.loads(plist_data or b'')
        executable = info['CFBundleExecutable']
        if not isinstance(executable, str) or Path(executable).name != executable:
            raise ValueError()
        if f'Contents/MacOS/{executable}' not in {m['path'] for m in modules}:
            raise ValueError()
        version, build, identifier = (str(info[k]) for k in ('CFBundleShortVersionString', 'CFBundleVersion', 'CFBundleIdentifier'))
    except (ValueError, TypeError, KeyError, plistlib.InvalidFileException):
        raise FingerprintError('bundle_metadata_invalid') from None
    encoded = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
    return {'schema': SCHEMA, 'complete': True, 'sha256': hashlib.sha256(encoded).hexdigest(),
            'file_count': len(records), 'total_bytes': total, 'modules': modules,
            'module_count': len(modules), 'wechat_version': version, 'wechat_build': build,
            'bundle_identifier': identifier}


def public_fingerprint(value: dict | None) -> dict:
    value = value or {}
    return {k: value.get(k) for k in ('schema', 'complete', 'sha256', 'module_count',
                                     'file_count', 'total_bytes', 'wechat_version', 'wechat_build', 'bundle_identifier', 'error')}
