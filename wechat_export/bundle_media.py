"""Portable attachment companions. Local-only, independent inodes, no remote IO."""
from __future__ import annotations
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import sys
from wechat_export.recovered_media import readonly, public_attachment, verified_object


def independent_copy(source, target):
    """APFS copy-on-write when available; never hardlink to the source archive."""
    if sys.platform == 'darwin':
        lib=ctypes.CDLL(None,use_errno=True)
        clone=lib.clonefile;clone.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int];clone.restype=ctypes.c_int
        if clone(os.fsencode(source),os.fsencode(target),0)==0:return
    with target.open('xb') as out, source.open('rb') as inp:shutil.copyfileobj(inp,out,1024*1024)


class BundleMedia:
    def __init__(self, archive):
        self.archive=archive;self.rows={}
        if (archive/'media/index.sqlite').is_file():
            with readonly(archive/'media/index.sqlite') as c:
                self.rows={r['record_uid']:dict(r) for r in c.execute('select * from attachments')}

    def include(self, uid, destination):
        row=self.rows.get(uid)
        if row is None:return None
        result=public_attachment(row)
        if row['status']!='available':return result
        # Validate object path and contents through the same boundary as HTTP.
        rel=Path('media')/row['relative_path'];target=destination/rel
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with verified_object(self.archive,row):
                independent_copy(self.archive/rel,target)
            h=hashlib.sha256()
            with target.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            if h.hexdigest()!=row['sha256']:raise ValueError('attachment_copy_digest_mismatch')
            if target.stat().st_ino==(self.archive/rel).stat().st_ino:raise ValueError('attachment_must_be_independent')
            target.chmod(0o600)
        result.update(relative_path=rel.as_posix(),sha256=row['sha256'])
        return result
