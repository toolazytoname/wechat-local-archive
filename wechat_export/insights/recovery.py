"""Explicit, account-checked recovery. Merge missing rows; never replace edits."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from .store import InsightsError, _USER_TABLES, _file_digest
from .identity import load_identity


def _candidates(data_root, current_root):
    parent=Path(data_root)/'insights'
    for root in sorted(parent.iterdir()):
        if root==current_root or root.is_symlink() or not root.is_dir() or root.name.endswith(('.staging','.migrate.lock')):
            continue
        path=root/'insights.sqlite'
        if path.is_file() and not path.is_symlink():
            yield root


def list_recovery(store,data_root):
    out=[]
    for root in _candidates(data_root,store.root):
        try:
            with sqlite3.connect((root/'insights.sqlite').as_uri()+'?mode=ro',uri=True) as conn:
                identity=conn.execute('SELECT self_sender_ids FROM identity WHERE id=1').fetchone()
                if not identity:continue
                counts={t:conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('notes','learning_items','profile_runs')}
                digest=_file_digest(root/'insights.sqlite')
                candidate=hashlib.sha256(str(root).encode()).hexdigest()
                out.append({'candidate_id':candidate,'fingerprint':digest,'self_sender_ids':json.loads(identity[0]),
                            'counts':counts,'label':root.name[:18], 'warning':'只导入缺失记录；冲突保留当前版本，不覆盖新笔记。'})
        except (sqlite3.DatabaseError,ValueError,OSError):continue
    return out


def recover_missing(store,data_root,candidate_id,fingerprint,confirmed_ids):
    identity=load_identity(store)
    if not identity or set(identity['self_sender_ids'])!=set(confirmed_ids):
        raise InsightsError('请先确认当前档案的本人身份。','identity_unresolved')
    roots=[r for r in _candidates(data_root,store.root) if hashlib.sha256(str(r).encode()).hexdigest()==candidate_id]
    if len(roots)!=1:raise InsightsError('恢复来源已失效。','not_found')
    root=roots[0];path=root/'insights.sqlite'
    if _file_digest(path)!=fingerprint:raise InsightsError('旧库已变化，请重新预览。','source_changed')
    src=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);src.row_factory=sqlite3.Row
    try:
        src.execute('BEGIN')
        row=src.execute('SELECT self_sender_ids FROM identity WHERE id=1').fetchone()
        if not row or set(json.loads(row[0]))!=set(confirmed_ids):
            raise InsightsError('旧库和当前账号的本人身份不一致，禁止合并。','wrong_account')
        # Validate every referenced body, never depend on a filename alone.
        contents=src.execute('SELECT body_hash,content_id FROM content_versions').fetchall()
        files=[]
        for row in contents:
            digest=row['body_hash']
            if not digest or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise InsightsError('旧正文缺少可信摘要，不能自动恢复。','invalid_content')
            source=root/'content'/f'{digest}.txt'
            if not source.is_file() or source.is_symlink() or _file_digest(source)!=digest:
                raise InsightsError('旧正文不完整，未导入记录。','invalid_content')
            files.append((source,digest))
        # SQLite write transaction serializes with current note edits. Conflicts
        # stay in the untouched legacy database; no INSERT OR REPLACE here.
        store.conn.execute('BEGIN IMMEDIATE');counts={};conflicts={}
        for source,digest in files:
            target=store.content_root/f'{digest}.txt'
            if target.is_file() and not target.is_symlink() and _file_digest(target)==digest:continue
            fd,name=tempfile.mkstemp(dir=store.content_root,prefix='.recover-')
            try:
                with os.fdopen(fd,'wb') as out,source.open('rb') as inp:
                    for chunk in iter(lambda:inp.read(1024*1024),b''):out.write(chunk)
                    out.flush();os.fsync(out.fileno())
                if _file_digest(Path(name))!=digest:raise InsightsError('正文发生变化。','source_changed')
                os.replace(name,target)
            finally:Path(name).unlink(missing_ok=True)
        for table in _USER_TABLES:
            if table in {'identity','consent_tickets'}:continue
            dest_cols={r[1] for r in store.conn.execute(f'PRAGMA table_info({table})')}
            cols=[r[1] for r in src.execute(f'PRAGMA table_info({table})') if r[1] in dest_cols]
            if not cols:continue
            added=skipped=0
            for row in src.execute(f'SELECT * FROM {table}'):
                cur=store.conn.execute(f"INSERT OR IGNORE INTO {table}({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",tuple(row[c] for c in cols))
                added+=cur.rowcount;skipped+=1-cur.rowcount
            counts[table]=added;conflicts[table]=skipped
        if _file_digest(path)!=fingerprint:raise InsightsError('旧库发生变化，请重新预览。','source_changed')
        store.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('recovery_last',?)",(json.dumps({'added':counts,'kept_current':conflicts}),))
        store.conn.commit()
        return {'added':counts,'kept_current':conflicts,'note':'当前记录未覆盖。冲突的旧版本仍保留在原库。'}
    except Exception:
        store.conn.rollback();raise
    finally:src.close()
