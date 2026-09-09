from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import zstandard

from wechat_export.config import AppConfig
from wechat_export.export_run import collect_records, export_records
from wechat_export.schema import msg_table_name


def _cfg(root: Path) -> AppConfig:
    return AppConfig(
        project_root=root,
        data_root=root,
        xwechat_root=root,
        account_backup_root=root / "demo_account",
        backup_set="set",
        source_backup2=root / "2",
        live_account_root=root / "demo_account_0403",
        live_db_root=root / "db",
        display_timezone="America/Los_Angeles",
        keys_path=None,
        config_path=root / "config.json",
        target_names=("Studio", "Alice"),
    )


def _build_dbs(root: Path) -> Path:
    dec = root / "decrypted"
    (dec / "contact").mkdir(parents=True)
    (dec / "message").mkdir()
    conn = sqlite3.connect(dec / "contact" / "contact.db")
    conn.execute(
        "CREATE TABLE contact(id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, alias TEXT, nick_name TEXT, remark TEXT)"
    )
    conn.execute("INSERT INTO contact VALUES (1,'wxid_alice',0,'','Al','Alice')")
    conn.execute("INSERT INTO contact VALUES (2,'wr_group@chatroom',2,'','Studio',NULL)")
    conn.execute("INSERT INTO contact VALUES (3,'demo_account',0,'','me',NULL)")
    conn.execute(
        "CREATE TABLE chat_room(username TEXT PRIMARY KEY, nick_name TEXT)"
    )
    conn.execute("INSERT INTO chat_room VALUES ('wr_group@chatroom','Studio')")
    conn.commit()
    conn.close()

    msg = sqlite3.connect(dec / "message" / "message_0.db")
    msg.execute("CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY)")
    msg.execute("INSERT INTO Name2Id(user_name) VALUES ('demo_account')")
    msg.execute("INSERT INTO Name2Id(user_name) VALUES ('wxid_alice')")
    msg.execute("INSERT INTO Name2Id(user_name) VALUES ('other@chatroom')")
    private = msg_table_name("wxid_alice")
    room = msg_table_name("wr_group@chatroom")
    ddl = f'''CREATE TABLE {private}(
        local_id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER,
        local_type INTEGER,
        sort_seq INTEGER,
        real_sender_id INTEGER,
        create_time INTEGER,
        status INTEGER,
        message_content BLOB,
        WCDB_CT_message_content INTEGER
    )'''
    msg.execute(ddl)
    msg.execute(ddl.replace(private, room))
    compressed = zstandard.ZstdCompressor().compress("压缩文本".encode("utf-8"))
    # two messages same second same text must both survive
    msg.execute(
        f"INSERT INTO {private}(server_id,local_type,sort_seq,real_sender_id,create_time,status,message_content,WCDB_CT_message_content) VALUES (11,1,1,1,1700000000,2,'hello',NULL)"
    )
    msg.execute(
        f"INSERT INTO {private}(server_id,local_type,sort_seq,real_sender_id,create_time,status,message_content,WCDB_CT_message_content) VALUES (12,1,2,2,1700000000,2,'hello',NULL)"
    )
    msg.execute(
        f"INSERT INTO {private}(server_id,local_type,sort_seq,real_sender_id,create_time,status,message_content,WCDB_CT_message_content) VALUES (13,1,3,2,1700000001,2,?,4)",
        (compressed,),
    )
    msg.execute(
        f"INSERT INTO {room}(server_id,local_type,sort_seq,real_sender_id,create_time,status,message_content,WCDB_CT_message_content) VALUES (21,1,1,2,1700000100,2,'in-group',NULL)"
    )
    # huge server id
    msg.execute(
        f"INSERT INTO {private}(server_id,local_type,sort_seq,real_sender_id,create_time,status,message_content,WCDB_CT_message_content) VALUES (9223372036854775807,3,4,1,1700000200,2,x'00ff',NULL)"
    )
    msg.commit()
    msg.close()
    return dec


class ExportPipelineTests(unittest.TestCase):
    def test_contact_table_preferred_over_stranger(self) -> None:
        from wechat_export.schema import map_contact_schema
        from wechat_export.livedb_export import load_contacts, find_targets

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "contact.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE contact(id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, alias TEXT, nick_name TEXT, remark TEXT)"
            )
            conn.execute("INSERT INTO contact VALUES (1,'wxid_alice',1,'','Alice',NULL)")
            conn.execute("INSERT INTO contact VALUES (2,'wr_group@chatroom',2,'','Studio',NULL)")
            conn.execute(
                "CREATE TABLE stranger(id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, alias TEXT, nick_name TEXT, remark TEXT)"
            )
            conn.execute("INSERT INTO stranger VALUES (1,'wxid_other',1,'','someone',NULL)")
            conn.execute("CREATE TABLE chatroom_member(room_id INTEGER, member_id INTEGER)")
            conn.execute("CREATE TABLE chat_room(username TEXT PRIMARY KEY, owner TEXT)")
            conn.commit()
            mapping = map_contact_schema(conn)
            conn.close()
            self.assertEqual(mapping["contact_table"], "contact")
            self.assertEqual(mapping["chat_room_table"], "chat_room")
            contacts = load_contacts(db)
            self.assertIn("wxid_alice", contacts)
            self.assertNotIn("wxid_other", contacts)
            targets = find_targets(contacts, ["Studio", "Alice"])
            self.assertEqual(len(targets["Alice"]), 1)
            self.assertEqual(targets["Alice"][0]["username"], "wxid_alice")
            self.assertEqual(len(targets["Studio"]), 1)
            self.assertEqual(targets["Studio"][0]["username"], "wr_group@chatroom")


    def test_targets_and_idempotent_export(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cfg(root)
            (cfg.private_root).mkdir(parents=True)
            (cfg.exports_root).mkdir()
            dec = _build_dbs(root)
            records, targets, meta = collect_records(dec, cfg, "live-db", "snap-test")
            self.assertEqual(len(targets["Alice"]), 1)
            self.assertEqual(len(targets["Studio"]), 1)
            # 5 records: 4 private + 1 group
            self.assertEqual(len(records), 5)
            texts = [r.text for r in records if r.conversation_id == "wxid_alice" and r.message_type_normalized == "text"]
            self.assertEqual(texts.count("hello"), 2)
            self.assertIn("压缩文本", [r.text for r in records])
            big = [r for r in records if r.server_message_id == "9223372036854775807"][0]
            self.assertEqual(big.message_type_normalized, "image")
            self.assertEqual(big.parse_status, "partial")
            self.assertTrue(big.attachment_refs and big.attachment_refs[0].get("data"))
            self.assertEqual(big.attachment_refs[0].get("encoding"), "base64")
            out1 = export_records(records, targets, cfg, "run1", source_kind="live-db", backup2_coverage="unverified", extra_notes=["synthetic"])
            n1 = (out1 / "all" / "messages.jsonl").read_text(encoding="utf-8").count("\n")
            out2 = export_records(records, targets, cfg, "run1", source_kind="live-db", backup2_coverage="unverified", extra_notes=["synthetic"])
            n2 = (out2 / "all" / "messages.jsonl").read_text(encoding="utf-8").count("\n")
            self.assertEqual(n1, n2)
            self.assertEqual(n1, 5)
            tpriv = list((out1 / "targets").glob("Alice/messages.jsonl"))
            self.assertTrue(tpriv)
            priv_n = tpriv[0].read_text(encoding="utf-8").count("\n")
            self.assertEqual(priv_n, 4)
            grp = list((out1 / "targets").glob("Studio/messages.jsonl"))
            self.assertEqual(grp[0].read_text(encoding="utf-8").count("\n"), 1)
            self.assertTrue((out1 / "quality-report.md").exists())
            manifest = (out1 / "manifest.json").read_text(encoding="utf-8")
            self.assertIn("live-db", manifest)
            self.assertIn("unverified", manifest)
            all_jsonl = (out1 / "all" / "messages.jsonl").read_text(encoding="utf-8")
            self.assertGreater(n1, 0)
            self.assertIn('"source_kind": "live-db"', all_jsonl)
            self.assertIn("hello", all_jsonl)
            self.assertIn("压缩文本", all_jsonl)
            self.assertIn("in-group", all_jsonl)
            priv_lines = tpriv[0].read_text(encoding="utf-8").splitlines()
            for line in priv_lines:
                self.assertIn('"conversation_id": "wxid_alice"', line)
                self.assertNotIn('"conversation_id": "wr_group@chatroom"', line)
                self.assertIn('"source_kind": "live-db"', line)
            grp_lines = grp[0].read_text(encoding="utf-8").splitlines()
            for line in grp_lines:
                self.assertIn('"conversation_id": "wr_group@chatroom"', line)
                self.assertNotIn('"conversation_id": "wxid_alice"', line)
            quality = (out1 / "quality-report.md").read_text(encoding="utf-8")
            self.assertIn("backup2_coverage: `unverified`", quality)
            self.assertIn("selected_source_exported", (out1 / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((out1 / "all" / "messages.csv").exists())
            self.assertTrue(list((out1 / "targets" / "Alice").glob("*.md")))
            self.assertTrue(list((out1 / "targets" / "Studio").glob("*.md")))
            man = (out1 / "manifest.json").read_text(encoding="utf-8")
            self.assertIn("partial", man)  # binary image is partial, not a fake complete archive
