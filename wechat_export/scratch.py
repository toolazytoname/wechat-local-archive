"""Lease-protected, explicitly owned disposable directories. Never sweep legacy work.

Only this version's private namespace/receipts are recognized. Snapshots, key files,
completed exports, unknown directories and legacy tempfile prefixes are untouched.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import shutil
import stat
import time
import uuid
from pathlib import Path

NAMESPACE = '.wla-scratch-v1'
RETENTION_SECONDS = 24 * 60 * 60
PURPOSES = {'http-export-source', 'cli-export-source', 'sqlcipher-export', 'page-decrypt', 'slice-output', 'cli-normalization', 'archive-index', 'full-archive', 'offline-html'}
NAME = re.compile(r'^scratch-[a-f0-9]{32}$')
ROOT_RECEIPT = {'owner': 'wechat-local-archive', 'schema': 1, 'kind': 'disposable-only'}


class ScratchError(RuntimeError):
    pass


def _private(st, directory=False):
    return (st.st_uid == os.getuid() and not st.st_mode & 0o077 and
            (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode) and st.st_nlink == 1))


def _read_json_at(fd, name):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        if not _private(os.fstat(child)):
            raise ScratchError('scratch_receipt_not_private')
        data = os.read(child, 4097)
        if len(data) > 4096:
            raise ScratchError('scratch_receipt_oversize')
        try:
            return json.loads(data)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ScratchError("scratch_receipt_invalid") from None
    finally:
        os.close(child)


def _write_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())


def _open_root(parent, create=False):
    if Path(parent).is_symlink():
        raise ScratchError("scratch_parent_symlink")
    root = Path(parent) / NAMESPACE
    if create:
        # Serialize only namespace registration, not jobs. A second first-use
        # worker must not mistake the first worker's half-written receipt for an
        # unregistered directory. The lock is empty, private and never deleted.
        init_fd = os.open(Path(parent) / (NAMESPACE + '.init.lock'),
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not _private(os.fstat(init_fd)) or os.fstat(init_fd).st_size:
                raise ScratchError('scratch_initialization_lock_not_private')
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(init_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise ScratchError('scratch_initialization_busy')
                    time.sleep(0.01)
            try:
                root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            else:
                _write_json(root / 'namespace.json', ROOT_RECEIPT)
        finally:
            os.close(init_fd)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not _private(os.fstat(fd), directory=True) or _read_json_at(fd, 'namespace.json') != ROOT_RECEIPT:
            raise ScratchError('scratch_namespace_unregistered')
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ScratchError('safe_recursive_delete_unavailable')
    except BaseException:
        os.close(fd)
        raise
    return root, fd


def _identity(st):
    return st.st_dev, st.st_ino


def _remove(root_fd, name, expected):
    # Keep receipts/leases until sensitive payload removal completes. A crash or
    # I/O failure during recursive deletion must leave a recoverable receipt.
    entry_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        if _identity(os.fstat(entry_fd)) != expected:
            raise ScratchError('scratch_replaced')
        if set(os.listdir(entry_fd)) - {'lease', 'receipt.json', 'payload'}:
            raise ScratchError('scratch_unexpected_control_files')
        try:
            payload = os.stat('payload', dir_fd=entry_fd, follow_symlinks=False)
        except FileNotFoundError:
            payload = None
        if payload is not None:
            if stat.S_ISLNK(payload.st_mode):
                os.unlink('payload', dir_fd=entry_fd)
            elif stat.S_ISDIR(payload.st_mode) and payload.st_dev == expected[0]:
                shutil.rmtree('payload', dir_fd=entry_fd)
            else:
                raise ScratchError('scratch_payload_not_owned_directory')
        for control in ('receipt.json', 'lease'):
            try: os.unlink(control, dir_fd=entry_fd)
            except FileNotFoundError: pass
        if _identity(os.stat(name, dir_fd=root_fd, follow_symlinks=False)) != expected:
            raise ScratchError('scratch_replaced')
        os.rmdir(name, dir_fd=root_fd)  # never recursively delete a replacement
    finally:
        os.close(entry_fd)


def sweep(parent: Path, *, apply=False, older_than=RETENTION_SECONDS, now=None, limit=128):
    """Inspect only direct receipt-bearing entries. Default is a dry run.

    Age alone never authorizes deletion: an exclusive lease and matching inode,
    namespace, owner, permissions, purpose and receipt are all required.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError('scratch_limit_must_be_positive')
    if not math.isfinite(older_than) or older_than < 3600:
        raise ValueError('scratch_retention_minimum_one_hour')
    now = time.time() if now is None else now
    result = {'eligible': 0, 'removed': 0, 'busy': 0, 'recent': 0, 'unknown': 0,
              'errors': 0, 'inspected': 0, 'limit_reached': False, 'dry_run': not apply}
    try:
        _, root_fd = _open_root(parent)
    except FileNotFoundError:
        if (Path(parent) / NAMESPACE).exists() or (Path(parent) / NAMESPACE).is_symlink():
            result["errors"] += 1
        return result
    except (OSError, ValueError, ScratchError):
        result['errors'] += 1
        return result
    try:
        for name in os.listdir(root_fd):
            if name == 'namespace.json': continue
            if result['eligible'] >= limit:
                result['limit_reached'] = True
                break
            result['inspected'] += 1
            if not NAME.fullmatch(name):
                result['unknown'] += 1
                continue
            entry_fd = lease_fd = None
            validated = False
            try:
                entry_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                st = os.fstat(entry_fd)
                if not _private(st, directory=True): raise ScratchError('scratch_not_private')
                lease_fd = os.open('lease', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=entry_fd)
                if not _private(os.fstat(lease_fd)) or os.fstat(lease_fd).st_size: raise ScratchError('scratch_lease_not_private')
                try:
                    fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    result['busy'] += 1
                    continue
                receipt = _read_json_at(entry_fd, 'receipt.json')
                if (receipt.get('owner') != ROOT_RECEIPT['owner'] or receipt.get('schema') != 1 or
                    receipt.get('name') != name or receipt.get('purpose') not in PURPOSES or
                    receipt.get('layout') != 'payload-v1' or
                    receipt.get('identity') != list(_identity(st))):
                    raise ScratchError('scratch_receipt_mismatch')
                created = receipt.get('created_at')
                if type(created) not in (float, int) or not math.isfinite(created) or not 0 <= created <= now:
                    raise ScratchError('scratch_invalid_age')
                if now - created < older_than:
                    result['recent'] += 1
                    continue
                validated = True
                result['eligible'] += 1
                if apply:
                    _remove(root_fd, name, _identity(st))
                    result['removed'] += 1
            except (OSError, ValueError, TypeError, AttributeError, ScratchError):
                result['errors' if validated else 'unknown'] += 1
            finally:
                if lease_fd is not None: os.close(lease_fd)
                if entry_fd is not None: os.close(entry_fd)
    finally:
        os.close(root_fd)
    return result


class ScratchSpace:
    """Context-managed disposable payload with a lease inheritable by owned children."""
    def __init__(self, parent: Path, purpose: str):
        if purpose not in PURPOSES: raise ValueError('invalid_scratch_purpose')
        self.parent, self.purpose = Path(parent), purpose
        self.root_fd = self.lease_fd = None
        self.path = None

    def __enter__(self):
        # Automatic recovery is conservative: >=24 hours and no surviving lease.
        sweep(self.parent, apply=True)
        root, self.root_fd = _open_root(self.parent, create=True)
        self.name = 'scratch-' + uuid.uuid4().hex
        try:
            os.mkdir(self.name, mode=0o700, dir_fd=self.root_fd)
            self.path = root / self.name
            self.identity = _identity(os.stat(self.name, dir_fd=self.root_fd, follow_symlinks=False))
            self.lease_fd = os.open(self.path / 'lease', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.payload = self.path / 'payload'
            self.payload.mkdir(mode=0o700)
            _write_json(self.path / 'receipt.json', {'owner': ROOT_RECEIPT['owner'], 'schema': 1,
                        'layout': 'payload-v1',
                        'name': self.name, 'purpose': self.purpose, 'identity': list(self.identity),
                        'created_at': time.time()})
            return self
        except BaseException:
            if self.lease_fd is not None: os.close(self.lease_fd)
            os.close(self.root_fd)
            # No valid receipt => no future automatic deletion. Do not guess ownership.
            raise

    @property
    def inherit_fds(self):
        return (self.lease_fd,)

    def __exit__(self, exc_type, exc, tb):
        try:
            _remove(self.root_fd, self.name, self.identity)
        except (OSError, ScratchError):
            if exc is None:
                raise
            exc.add_note("Private scratch cleanup deferred; retryable receipt retained.")
        finally:
            os.close(self.lease_fd)
            os.close(self.root_fd)
