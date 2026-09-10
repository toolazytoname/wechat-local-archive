"""Immutable archive identity for HTTP reads and queued export source snapshots."""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class ArchiveBindingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def signature(path: Path):
    try:
        st = path.stat()
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    except FileNotFoundError:
        return None


def digest_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


@dataclass(frozen=True)
class BoundFile:
    path: Path
    relative_name: str
    stamp: tuple | None
    sha256: str | None

    @classmethod
    def capture(cls, path: Path, relative_name: str):
        if path.is_symlink():
            raise ArchiveBindingError('archive_source_changed', 'Archive files must not be symlinks.')
        before = signature(path)
        digest = digest_file(path) if before is not None else None
        if before != signature(path):
            raise ArchiveBindingError('archive_source_changed', 'Archive changed while selecting it. Reopen it.')
        return cls(path, relative_name, before, digest)

    def verify(self, strong=False):
        if self.path.is_symlink() or signature(self.path) != self.stamp:
            raise ArchiveBindingError('archive_source_changed', 'Archive files changed. Reopen the archive and preview again.')
        if strong and self.stamp is not None and digest_file(self.path) != self.sha256:
            raise ArchiveBindingError('archive_source_changed', 'Archive content changed. Reopen it.')


@dataclass(frozen=True)
class ArchiveBinding:
    archive_id: str
    root: Path
    index_path: Path
    files: tuple[BoundFile, ...]
    revision: str
    media_root: Path | None = None
    hardlink_db: Path | None = None

    @classmethod
    def capture(cls, root: Path, index_path: Path, *, media_root=None, hardlink_db=None):
        root, index_path = root.resolve(), index_path.resolve()
        if not index_path.is_relative_to(root):
            raise ArchiveBindingError('archive_source_changed', 'Index is outside the registered archive.')
        wal = Path(str(index_path) + '-wal')
        if wal.exists() and wal.stat().st_size:
            raise ArchiveBindingError('archive_source_changed', 'Archive index has pending writes. Close its writer and rebuild it.')
        from wechat_export.archive_files import SOURCE_FILES
        paths = ((index_path, 'archive.sqlite'), *[(root / name, name) for name in SOURCE_FILES.values()])
        files = tuple(BoundFile.capture(path, name) for path, name in paths)
        if files[0].stamp is None or files[1].stamp is None:
            raise ArchiveBindingError('archive_source_changed', 'Canonical archive or index missing.')
        with sqlite3.connect(index_path.as_uri()+'?mode=ro&immutable=1', uri=True) as conn:
            meta = dict(conn.execute('SELECT key,value FROM meta'))
        for file, key in zip(files[1:], SOURCE_FILES):
            if meta.get(key) != (file.sha256 or 'absent'):
                raise ArchiveBindingError('archive_source_changed', 'Index does not match the canonical source. Reopen to rebuild it.')
        revision = hashlib.sha256(json.dumps([(f.relative_name,f.sha256) for f in files]).encode()).hexdigest()
        result = cls(secrets.token_urlsafe(24), root, index_path, files, revision, media_root, hardlink_db)
        result.verify()
        return result

    def verify(self, strong=False):
        wal = Path(str(self.index_path) + '-wal')
        if wal.exists() and wal.stat().st_size:
            raise ArchiveBindingError('archive_source_changed', 'Index was modified. Reopen the archive.')
        for file in self.files:
            file.verify(strong)

    def public(self):
        return {'archive_id': self.archive_id, 'source_revision': self.revision}

    def snapshot_to(self, destination: Path, cancelled=lambda: False):
        """Private byte-for-byte copy whose hashes must equal the selected revision.

        Source changes while copying abort; changes after a verified copy do not
        alter the queued export, which reads only its private snapshot.
        """
        from wechat_export.export_service import ExportCancelled
        self.verify()
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        for file in self.files:
            if cancelled():
                raise ExportCancelled('export cancelled')
            if file.stamp is None:
                continue
            target = destination / file.relative_name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            sha = hashlib.sha256()
            fd = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(fd,'wb') as output, file.path.open('rb') as source:
                for block in iter(lambda: source.read(1024*1024), b''):
                    if cancelled():
                        raise ExportCancelled('export cancelled')
                    output.write(block);sha.update(block)
            if sha.hexdigest() != file.sha256:
                raise ArchiveBindingError('archive_source_changed', 'Source changed while making the export snapshot.')
        self.verify()
        return destination
