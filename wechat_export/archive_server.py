"""Loopback-only archive viewer. Refuses any bind except 127.0.0.1."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from wechat_export.archive_index import default_index_path
from wechat_export.loopback import LOOPBACK_HOST, allowed_request_host, validate_bind_host

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def viewer_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


class ArchiveHandler(SimpleHTTPRequestHandler):
    index_path: Path
    export_dir: Path
    bind_port: int = 8765

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(viewer_dir()), **kwargs)

    def log_message(self, fmt: str, *args: object) -> None:
        import sys

        sys.stderr.write("archive %s - %s\n" % (self.address_string(), fmt % args))

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _forbidden(self) -> None:
        self.send_response(403)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"error":"forbidden host"}')

    def _guard(self) -> bool:
        if not allowed_request_host(self.headers.get("Host"), self.bind_port):
            self._forbidden()
            return False
        return True

    def _send_json(self, payload: object, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.index_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        q = parse_qs(parsed.query)
        if path.startswith("/api/"):
            try:
                self._api(path, q)
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, 404)
            except Exception:  # noqa: BLE001
                self._send_json({"error": "internal"}, 500)
            return
        if path == "/":
            self.path = "/index.html"
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        parsed = urlparse(self.path)
        if unquote(parsed.path) != "/api/export":
            self._send_json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self._send_json({"error": "payload too large"}, 400)
            return
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            result = _write_slice(self.index_path, self.export_dir, body)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        self._send_json(result)

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
                        "export_dir": rows.get("export_dir") or self.export_dir.name,
                        "message_count": int(rows.get("message_count") or 0),
                        "readable_count": int(rows.get("readable_count") or 0),
                        "conversation_count": int(rows.get("conversation_count") or 0),
                        "display_timezone": rows.get("display_timezone") or "America/Los_Angeles",
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
                around = (q.get("around") or [""])[0]
                if around:
                    pos = conn.execute(
                        f"SELECT count(*) FROM messages WHERE {where} AND (timestamp_utc, record_uid) < "
                        f"(SELECT timestamp_utc, record_uid FROM messages WHERE record_uid = ?)",
                        tuple(params_list + [around]),
                    ).fetchone()
                    if pos:
                        offset = max(0, int(pos[0]) - 20)
                total = conn.execute(f"SELECT count(*) FROM messages WHERE {where}", params_list).fetchone()[0]
                rows = _rows(
                    conn,
                    f"SELECT record_uid, conversation_id, sender_display_name, is_self, timestamp_utc, "
                    f"message_type, preview, readable, source_kind, text, media_kind, media_title "
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
                cid = (q.get("conversation_id") or [""])[0]
                sql = """
                    SELECT m.record_uid, m.conversation_id, c.display_name, m.sender_display_name,
                           m.timestamp_utc, m.preview, m.source_kind
                    FROM messages m
                    LEFT JOIN conversations c ON c.conversation_id = m.conversation_id
                    WHERE (m.preview LIKE ? OR ifnull(m.text, '') LIKE ?)
                """
                params_s: list = [like, like]
                if cid:
                    sql += " AND m.conversation_id = ?"
                    params_s.append(cid)
                sql += " ORDER BY m.timestamp_utc DESC LIMIT 80"
                self._send_json(_rows(conn, sql, tuple(params_s)))
                return
            if path == "/api/export/preview":
                body = {k: (q.get(k) or [""])[0] for k in ("conversation_id", "since", "until", "readable")}
                ids = q.get("conversation_id") or []
                count = _count_slice(conn, ids, body.get("since") or None, body.get("until") or None, body.get("readable") == "1")
                self._send_json({"count": count})
                return
            self._send_json({"error": "not found"}, 404)
        finally:
            conn.close()


def _count_slice(conn: sqlite3.Connection, ids: list[str], since: str | None, until: str | None, readable: bool) -> int:
    sql = "SELECT count(*) FROM messages WHERE 1=1"
    params: list = []
    if ids:
        sql += " AND conversation_id IN (%s)" % ",".join("?" * len(ids))
        params.extend(ids)
    if since:
        sql += " AND timestamp_utc >= ?"
        params.append(since)
    if until:
        sql += " AND timestamp_utc <= ?"
        params.append(until)
    if readable:
        sql += " AND readable = 1"
    return int(conn.execute(sql, params).fetchone()[0])


def _write_slice(index_path: Path, export_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
    fmt = body.get("format") or "jsonl"
    if fmt not in {"jsonl", "csv", "md"}:
        raise ValueError("format must be jsonl, csv, or md")
    ids = [str(x) for x in (body.get("conversation_ids") or []) if x]
    since = body.get("since") or None
    until = body.get("until") or None
    readable = bool(body.get("readable_only"))
    conn = sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM messages WHERE 1=1"
        params: list = []
        if ids:
            sql += " AND conversation_id IN (%s)" % ",".join("?" * len(ids))
            params.extend(ids)
        if since:
            sql += " AND timestamp_utc >= ?"
            params.append(since)
        if until:
            sql += " AND timestamp_utc <= ?"
            params.append(until)
        if readable:
            sql += " AND readable = 1"
        sql += " ORDER BY timestamp_utc, record_uid"
        rows = list(conn.execute(sql, params))
    finally:
        conn.close()
    stamp = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    dest = export_dir / "slices" / stamp
    dest.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        path = dest / "messages.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    elif fmt == "csv":
        import csv

        path = dest / "messages.csv"
        fields = ["timestamp_utc", "conversation_id", "sender_display_name", "media_kind", "preview", "text"]
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(dict(row))
    else:
        path = dest / "messages.md"
        lines = ["# export", ""]
        for row in rows:
            d = dict(row)
            who = d.get("sender_display_name") or "unknown"
            body_t = d.get("text") or d.get("preview") or ""
            lines.append(f"- {d.get('timestamp_utc')} {who}: {body_t}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(path), "count": len(rows), "format": fmt}


def serve(export_dir: Path, host: str = LOOPBACK_HOST, port: int = 8765) -> None:
    host = validate_bind_host(host)
    index = default_index_path(export_dir)
    if not index.exists():
        raise FileNotFoundError(f"index missing: {index}. Run: python -m wechat_export index --export-dir {export_dir}")
    static = viewer_dir()
    if not (static / "index.html").exists():
        raise FileNotFoundError(f"viewer assets missing at {static}")
    ArchiveHandler.index_path = index
    ArchiveHandler.export_dir = export_dir
    ArchiveHandler.bind_port = port
    httpd = ThreadingHTTPServer((host, port), ArchiveHandler)
    print(f"archive viewer http://{host}:{port}")
    httpd.serve_forever()
