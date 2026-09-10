"""Disk-backed canonical records with bounded SQLite caches and ordered iteration."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from wechat_export.models import MessageRecord

class RecordStore:
    def __init__(self, path: Path, check=lambda: None):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(path)
        self.conn=sqlite3.connect(path)
        path.chmod(0o600)
        self.conn.executescript('''PRAGMA journal_mode=DELETE;
            PRAGMA temp_store=FILE; PRAGMA cache_size=-4096;
            CREATE TABLE records(uid TEXT PRIMARY KEY,ts TEXT,source TEXT,tab TEXT,local TEXT,
                                 sender TEXT,peer TEXT,kind TEXT,payload TEXT);
            CREATE TABLE peers(sender TEXT,peer TEXT,PRIMARY KEY(sender,peer));''')
        self.n=0
        self.check=check
        self.self_sender=None
        self.closed=False

    def extend(self, records):
        for rec in records:
            self.check()
            payload=json.dumps(rec.to_dict(),ensure_ascii=False,separators=(',',':'))
            self.conn.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)',
                (rec.record_uid,rec.timestamp_utc or '',rec.source_relative_path or '',rec.source_table or '',
                 rec.local_message_id or '',rec.sender_id,rec.conversation_id,rec.conversation_type,payload))
            if rec.conversation_type=='private' and rec.sender_id and rec.sender_id!=rec.conversation_id:
                self.conn.execute('INSERT OR IGNORE INTO peers VALUES(?,?)',(rec.sender_id,rec.conversation_id))
            self.n+=1
            if self.n%1000==0:self.conn.commit()
        self.conn.commit()

    def infer_self(self):
        rows=self.conn.execute('SELECT sender FROM peers GROUP BY sender HAVING count(*)>=2 LIMIT 2').fetchall()
        if len(rows)==1:
            self.self_sender=rows[0][0]
            return 'unique_sender_across_multiple_private_peers; inferred, not authenticated identity'
        return None

    def select_conversation(self, conversation_id):
        self.check()
        count=self.conn.execute('SELECT count(*) FROM records WHERE peer=?',(conversation_id,)).fetchone()[0]
        if count == 0:
            raise ValueError('Selected conversation has no records; refusing implicit all')
        self.conn.execute('DELETE FROM records WHERE peer<>?',(conversation_id,))
        self.conn.commit()
        self.n=count

    def sort(self, key=None):
        self.check()
        self.conn.execute('CREATE INDEX IF NOT EXISTS record_order ON records(ts,source,tab,local,uid)')
        self.conn.commit()
        self.check()

    def __len__(self):return self.n

    def __iter__(self):
        for (payload,) in self.conn.execute('SELECT payload FROM records ORDER BY ts,source,tab,local,uid'):
            self.check()
            raw=json.loads(payload)
            raw.pop('schema_version',None)
            rec=MessageRecord(**raw)
            if self.self_sender:
                rec.is_self=(rec.sender_id==self.self_sender) if rec.sender_id else None
            yield rec

    def close(self):
        if not self.closed:
            self.conn.close();self.closed=True
