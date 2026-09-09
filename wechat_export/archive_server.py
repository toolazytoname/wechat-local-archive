"""Loopback-only archive viewer. Binds 127.0.0.1, never serves keys."""

from __future__ import annotations

import json
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from wechat_export.archive_index import default_index_path

VIEWER_DIR = Path(__file__).resolve().parents[1] / "viewer"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


class ArchiveHandler(SimpleHTTPRequestHandler):
    index_path: Path
    export_dir: Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

    def log_message(self, fmt: str, *args: object) -> None:
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("archive %s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, payload: object, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.index_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        q = parse_qs(parsed.query)
        if path.startswith("/api/"):
            try:
                self._api(path, q)
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, 404)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": type(exc).__name__}, 500)
            return
        if path == "/":
            self.path = "/index.html"
        return SimpleHTTPRequestHandler.do_GET(self)

    def _api(self, path: str, q: dict[str, list[str]]) -> None:
        conn = self._db()
        try:
            if path == "/api/meta":
                rows = {r["key"]: r["value"] for r in _rows(conn, "SELECT key, value FROM meta")}
                manifest = {}
                if rows.get("manifest"):
                    manifest = json.loads(rows["manifest"])
                self._send_json(
                    {
                        "export_dir": Path(rows.get("export_dir", str(self.export_dir))).name,
                        "message_count": int(rows.get("message_count") or 0),
                        "readable_count": int(rows.get("readable_count") or 0),
                        "conversation_count": int(rows.get("conversation_count") or 0),
                        "source_kind": manifest.get("source_kind"),
                        "backup2_coverage": manifest.get("backup2_coverage"),
                        "targets": manifest.get("targets") or {},
                    }
                )
                return
            if path == "/api/conversations":
                query = (q.get("q") or [""])[0].strip()
                sql = "SELECT * FROM conversations"
                params: tuple = ()
                if query:
                    sql += " WHERE display_name LIKE ? OR conversation_id LIKE ?"
                    like = f"%{query}%"
                    params = (like, like)
                sql += " ORDER BY last_timestamp_utc DESC"
                self._send_json(_rows(conn, sql, params))
                return
            if path == "/api/messages":
                cid = (q.get("conversation_id") or [""])[0]
                if not cid:
                    self._send_json({"error": "conversation_id required"}, 400)
                    return
                readable = (q.get("readable") or ["0"])[0] == "1"
                try:
                    offset = max(0, int((q.get("offset") or ["0"])[0]))
                    limit = min(200, max(1, int((q.get("limit") or ["80"])[0])))
                except ValueError:
                    self._send_json({"error": "bad pagination"}, 400)
                    return
                where = "conversation_id = ?"
                params_list: list = [cid]
                if readable:
                    where += " AND readable = 1"
                total = conn.execute(f"SELECT count(*) FROM messages WHERE {where}", params_list).fetchone()[0]
                rows = _rows(
                    conn,
                    f"SELECT record_uid, conversation_id, sender_display_name, is_self, timestamp_utc, "
                    f"message_type, preview, readable, source_kind, text "
                    f"FROM messages WHERE {where} ORDER BY timestamp_utc, record_uid LIMIT ? OFFSET ?",
                    tuple(params_list + [limit, offset]),
                )
                self._send_json({"total": total, "offset": offset, "limit": limit, "messages": rows})
                return
            if path == "/api/search":
                query = (q.get("q") or [""])[0].strip()
                if len(query) < 2:
                    self._send_json({"error": "q too short"}, 400)
                    return
                like = f"%{query}%"
                rows = _rows(
                    conn,
                    """
                    SELECT m.record_uid, m.conversation_id, c.display_name, m.sender_display_name,
                           m.timestamp_utc, m.preview, m.source_kind
                    FROM messages m
                    LEFT JOIN conversations c ON c.conversation_id = m.conversation_id
                    WHERE m.preview LIKE ? OR ifnull(m.text, '') LIKE ?
                    ORDER BY m.timestamp_utc DESC
                    LIMIT 80
                    """,
                    (like, like),
                )
                self._send_json(rows)
                return
            self._send_json({"error": "not found"}, 404)
        finally:
            conn.close()


def serve(export_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    index = default_index_path(export_dir)
    if not index.exists():
        raise FileNotFoundError(f"index missing: {index}. Run: python -m wechat_export index --export-dir {export_dir}")
    ArchiveHandler.index_path = index
    ArchiveHandler.export_dir = export_dir
    httpd = ThreadingHTTPServer((host, port), ArchiveHandler)
    print(f"archive viewer http://{host}:{port}  (loopback only)")
    httpd.serve_forever()
