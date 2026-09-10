"""Recover mapped local attachments into a private, self-contained media store.

No network, keys, live DB writes or filename-only guessing. Associations require
chat+server-message IDs from a supplied decrypted resource DB or explicit MD5
hardlink records. Encrypted containers are retained at source, never served as
images. Source identifiers stay in local receipts, not public API paths.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import quote
from wechat_export.media_stream import open_local_media
from wechat_export.media_resolve import sniff_media, conversation_hash
from wechat_export.message_cards import parse_xml
from wechat_export.preview import record_presentation
from wechat_export.fsutil import sha256_file

SCHEMA = 'wechat-recovered-media/1'
HEX = re.compile(r'^[0-9a-fA-F]{32}$')
OBJECT = re.compile(r'^objects/[0-9a-f]{64}\.[a-z0-9]{1,12}$')
SQL = '''CREATE TABLE attachments (
record_uid TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, kind TEXT NOT NULL,
status TEXT NOT NULL, relative_path TEXT, mime TEXT, byte_size INTEGER,
sha256 TEXT, filename TEXT, representation TEXT, association TEXT, reason TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);'''


def sid(value):
    try: return str(int(value) & ((1 << 64)-1))
    except (ValueError,TypeError): return None


def resource_stem(blob):
    # Observed protobuf field 2 -> field 1 -> 32 ASCII hexadecimal characters.
    # Unknown layouts are rejected rather than searching arbitrary key bytes.
    if isinstance(blob, bytes) and len(blob)==36 and blob[:4]==b'\x12\x22\x0a\x20':
        try: value=blob[4:].decode('ascii')
        except UnicodeError: return None
        if HEX.fullmatch(value): return value.lower()
    return None


@contextmanager
def readonly(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError('mapping_not_regular')
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size: raise ValueError('mapping_has_pending_wal')
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    c.row_factory=sqlite3.Row
    try: yield c
    finally: c.close()


def clean_filename(value, fallback):
    value=str(value or fallback).replace('\\','/').split('/')[-1]
    value=''.join(c for c in value if ord(c)>=32 and ord(c)!=127).strip()
    return value[:180] or fallback


def declared_hashes(info, kind):
    root=parse_xml(info.get('body') or '')
    if root is None: return []
    if kind=='image':
        node=root if root.tag=='img' else root.find('.//img')
        values=[] if node is None else [node.get('md5'),node.get('originsourcemd5')]
    elif kind=='video':
        node=root if root.tag=='videomsg' else root.find('.//videomsg')
        values=[] if node is None else [node.get(k) for k in ('md5','rawmd5','originsourcemd5')]
    else:
        node=root if root.tag=='appmsg' else root.find('.//appmsg')
        values=[] if node is None else [node.findtext(p) for p in ('md5','appattach/md5','appattach/filemd5')]
    return list(dict.fromkeys(v.lower() for v in values if isinstance(v,str) and HEX.fullmatch(v)))


def _stamp(st): return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)


def media_mime(head, kind):
    mime=sniff_media(head)
    if head.startswith(b'RIFF') and head[8:12]==b'WEBP': mime='image/webp'
    if head.startswith(b'%PDF-'): mime='application/pdf'
    if kind=='file': return mime or 'application/octet-stream'
    if kind=='image': return mime if mime and mime.startswith('image/') else None
    if kind=='video': return mime if mime and (mime.startswith('video/') or mime.startswith('image/')) else None
    return None


class Recoverer:
    def __init__(self, account, hardlink, resource, stage):
        self.account=account.resolve(strict=True);self.stage=stage
        self.maps=defaultdict(list);self.resources=defaultdict(set);self.by_stem=defaultdict(list)
        self.stats=Counter();self.copies={}
        with readonly(hardlink) as c:
            dirs=dict(c.execute('select rowid,username from dir2id'))
            for kind in ('image','file','video'):
                for r in c.execute('select md5,dir1,dir2,file_name,file_size from '+kind+'_hardlink_info_v4'):
                    if not isinstance(r['md5'],str) or not HEX.fullmatch(r['md5']): continue
                    name=r['file_name']
                    if not isinstance(name,str) or Path(name).name!=name or '\\' in name: continue
                    first,second=dirs.get(r['dir1']),dirs.get(r['dir2'])
                    parts=[]
                    if kind=='image' and first and second: parts=[('msg','attach',first,second,'Img',name)]
                    elif kind in ('file','video'):
                        parts=[('msg',kind,d,name) for d in (first,second) if d and re.fullmatch(r'\d{4}-\d{2}',d)]
                    for names in parts:
                        if any(x in ('.','..') or '/' in x or '\\' in x for x in names):continue
                        self.maps[(kind,r['md5'].lower())].append(self.account.joinpath(*names))
        with readonly(resource) as c:
            chats=dict(c.execute('select rowid,user_name from ChatName2Id'))
            for r in c.execute('select chat_id,message_svr_id,message_local_type,packed_info from MessageResourceInfo'):
                stem=resource_stem(r['packed_info']);chat=chats.get(r['chat_id'])
                kind={3:'image',43:'video'}.get(r['message_local_type'])
                if stem and chat and kind and sid(r['message_svr_id']) not in (None,'0'):
                    self.resources[(chat,sid(r['message_svr_id']),kind)].add(stem)
        # Enumerate names/stats only; actual data is opened no-follow when matched.
        for folder in ('attach','video'):
            base=self.account/'msg'/folder
            for parent,dirs,files in os.walk(base,followlinks=False):
                dirs[:]=[d for d in dirs if not (Path(parent)/d).is_symlink()]
                for name in files:
                    m=re.match(r'^([0-9a-fA-F]{32})(?:_[A-Za-z0-9]+)*\.[A-Za-z0-9]+$',name)
                    if m:
                        path=Path(parent)/name
                        if not path.is_symlink():self.by_stem[m[1].lower()].append(path)

    def candidates(self,rec,info,kind):
        result=[];hashes=declared_hashes(info,kind)
        stems=self.resources.get((rec.get('conversation_id'),sid(rec.get('server_message_id')),kind),set())
        if len(stems)==1:
            stem=next(iter(stems));conv=conversation_hash(rec['conversation_id'])
            for p in self.by_stem.get(stem,[]):
                rel=p.relative_to(self.account).parts
                if kind=='image' and len(rel)==6 and rel[:3]==('msg','attach',conv) and rel[4]=='Img':result.append((p,'resource_chat_server_stem'))
                elif kind=='video' and len(rel)==4 and rel[:2]==('msg','video'):result.append((p,'resource_chat_server_stem'))
        for md5 in hashes:
            for p in self.maps.get((kind,md5),[]):result.append((p,'declared_md5_hardlink'))
        seen=set();found=[]
        for p,evidence in result:
            if p in seen:continue
            seen.add(p)
            try:
                with open_local_media(self.account,p) as (f,size):
                    head=f.read(32);mime=media_mime(head,kind)
                if not mime:
                    self.stats['opaque_candidates_skipped']+=1;continue
                # Prefer actual videos over poster images, then larger image variants.
                rank=(1 if kind=='video' and mime.startswith('video/') else 0,size)
                found.append((rank,p,evidence,mime,size))
            except OSError:continue
        return sorted(found,key=lambda x:x[0],reverse=True),hashes

    def copy(self,p,mime,kind,hashes):
        cached=self.copies.get(str(p))
        if cached and _stamp(p.stat())==cached['source_stamp']:return cached
        fd,name=tempfile.mkstemp(prefix='.copy-',dir=self.stage);h=hashlib.sha256();md5=hashlib.md5()
        try:
            with os.fdopen(fd,'wb') as dest,open_local_media(self.account,p) as (source,size):
                before=_stamp(os.fstat(source.fileno()))
                for chunk in iter(lambda:source.read(1024*1024),b''):
                    h.update(chunk);md5.update(chunk);dest.write(chunk)
                if before!=_stamp(os.fstat(source.fileno())) or before!=_stamp(p.stat()):raise ValueError('source_media_changed')
                dest.flush();os.fsync(dest.fileno())
            digest=h.hexdigest();ext={'image/jpeg':'jpg','image/png':'png','image/gif':'gif','image/webp':'webp','video/mp4':'mp4','application/pdf':'pdf'}.get(mime,'bin')
            target=self.stage/'objects'/f'{digest}.{ext}'
            if not target.exists():os.link(name,target);self.stats['unique_files']+=1;self.stats['copied_bytes']+=size
            result={'relative_path':target.relative_to(self.stage).as_posix(),'sha256':digest,'md5':md5.hexdigest(),'byte_size':size,'source_stamp':before}
            self.copies[str(p)]=result
            return result
        finally:Path(name).unlink(missing_ok=True)

    def recover(self,rec,info):
        kind=info['media_kind'];candidates,hashes=self.candidates(rec,info,kind)
        for _,p,evidence,mime,size in candidates:
            result=self.copy(p,mime,kind,hashes)
            # File attachments require exact content MD5, not merely a same-name map.
            if kind=='file' and result['md5'] not in hashes:
                self.stats['file_md5_mismatch']+=1;continue
            suffix=p.stem.lower()
            preview=('_t' in suffix or '_thumb' in suffix or (kind=='video' and mime.startswith('image/')))
            representation='preview' if preview else ('original_verified' if result['md5'] in hashes else 'local_copy')
            fallback={'image':'image','video':'video','file':'attachment'}[kind]+'.'+p.suffix.lstrip('.')
            filename=clean_filename((info.get('card') or {}).get('title') if kind=='file' else p.name,fallback)
            self.stats[kind+'_available']+=1;self.stats['preview_messages']+=representation=='preview'
            return (rec['record_uid'],rec['conversation_id'],kind,'available',result['relative_path'],mime,size,result['sha256'],filename,representation,evidence,None)
        self.stats[kind+'_missing']+=1
        return (rec['record_uid'],rec['conversation_id'],kind,'missing',None,None,None,None,None,None,None,'no_readable_mapped_local_file')


def recover_archive(archive,account,hardlink,resource,*,progress=None):
    archive=archive.resolve(strict=True);destination=archive/'media'
    if destination.exists() or destination.is_symlink():raise FileExistsError('media_store_already_exists')
    from wechat_export.archive_binding import BoundFile
    bound=[BoundFile.capture(archive/'all/messages.jsonl','canonical'),BoundFile.capture(hardlink,'hardlink'),BoundFile.capture(resource,'resource')]
    stage=Path(tempfile.mkdtemp(prefix='.media-recovery-',dir=archive));(stage/'objects').mkdir(mode=0o700)
    try:
        recovery=Recoverer(account,hardlink,resource,stage)
        index=sqlite3.connect(stage/'index.sqlite');index.executescript(SQL)
        try:
            count=0
            with (archive/'all/messages.jsonl').open() as lines:
                for line in lines:
                    if not line.strip():continue
                    rec=json.loads(line);count+=1
                    if rec.get('message_type_normalized') in {'image','video','file','app'}:
                        info=record_presentation(rec)
                        if info['media_kind'] in {'image','video','file'}:
                            row=recovery.recover(rec,info);index.execute('insert into attachments values (?,?,?,?,?,?,?,?,?,?,?,?)',row)
                    if count%5000==0:
                        index.commit()
                        if progress:progress({'records_examined':count,**dict(recovery.stats)})
            # Remove only unreferenced objects created by this task, e.g. rejected
            # MD5 candidates. Never remove anything from the account's media tree.
            referenced={r[0] for r in index.execute('select distinct relative_path from attachments where status="available"')}
            for p in (stage/'objects').iterdir():
                if p.relative_to(stage).as_posix() not in referenced:p.unlink()
            report={'schema':SCHEMA,'source_kind':'live-db','backup2_coverage':'unverified',
                    'source_canonical_sha256':bound[0].sha256,'hardlink_sha256':bound[1].sha256,'resource_db_sha256':bound[2].sha256,
                    'records_examined':count,'counts':dict(recovery.stats),'stored_files':len(referenced),
                    'stored_bytes':sum((stage/p).stat().st_size for p in referenced),
                    'attachment_extraction_complete':False,'remote_downloads':0,'encrypted_containers_decoded':0,
                    'limitations':['unavailable or opaque originals are not recovered','previews labelled separately','voice, stickers and nested forwarded attachments not recovered by this adapter']}
            index.execute('insert into meta values (?,?)',('report',json.dumps(report)))
            index.commit();assert index.execute('pragma integrity_check').fetchone()[0]=='ok'
        finally:index.close()
        for b in bound:b.verify(strong=True)
        (stage/'recovery-report.json').write_text(json.dumps(report,indent=2))
        for p in stage.rglob('*'):
            if p.is_file():p.chmod(0o600)
        destination.mkdir(mode=0o700)
        try:os.replace(stage,destination)
        except BaseException:
            try:destination.rmdir()
            except OSError:pass
            raise
        return report
    finally:
        if stage.exists():shutil.rmtree(stage)


def attachment(root,uid):
    path=Path(root)/'media/index.sqlite'
    if not path.is_file():return None
    with readonly(path) as c:
        r=c.execute('select * from attachments where record_uid=?',(uid,)).fetchone()
    if r is None:return None
    result=dict(r)
    if result['status']=='available' and (not OBJECT.fullmatch(result['relative_path'] or '') or result['sha256'] not in result['relative_path']):raise ValueError('invalid_media_object')
    return result


def public_attachment(row):
    if row is None:return {'status':'not_recovered'}
    return {k:row.get(k) for k in ('status','kind','mime','byte_size','filename','representation','association','reason')}


@contextmanager
def verified_object(root,row):
    if not row or row['status']!='available' or not OBJECT.fullmatch(row['relative_path'] or ''):raise FileNotFoundError('media_not_available')
    with open_local_media(Path(root)/'media',Path(root)/'media'/row['relative_path']) as (stream,size):
        if size!=row['byte_size']:raise ValueError('media_size_changed')
        h=hashlib.sha256();before=_stamp(os.fstat(stream.fileno()))
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=row['sha256'] or _stamp(os.fstat(stream.fileno()))!=before:raise ValueError('media_digest_changed')
        stream.seek(0);yield stream,size


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('archive','account','hardlink','resource'):p.add_argument('--'+flag,type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    print(json.dumps(recover_archive(a.archive,a.account,a.hardlink,a.resource,progress=lambda d:print(json.dumps(d),flush=True)),indent=2))

if __name__=='__main__':main()
