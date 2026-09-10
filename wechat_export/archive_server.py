"""Loopback-only archive viewer and first-run wizard. Refuses any bind except 127.0.0.1."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from wechat_export.archive_index import ensure_index_current, build_index, default_index_path
from wechat_export.compatibility import evaluate_environment
from wechat_export.discovery import discover_accounts
from wechat_export.environment import collect_environment
from wechat_export.export_service import (
    ExportCancelled,
    QueryError,
    QuerySpec,
    count_messages,
    count_selection,
    known_conversation_ids,
    write_slice,
)
from wechat_export.http_security import (
    CSRF_HEADER,
    HttpGuardError,
    cookie_header,
    loads_object,
    parse_content_length,
    public_error,
    require_json_content_type,
    require_write_auth,
    new_session_token,
)
from wechat_export.archive_binding import ArchiveBinding, ArchiveBindingError
from wechat_export.insights.store import InsightsError
from wechat_export.insights_routes import handle_get as handle_insights_get
from wechat_export.insights_routes import handle_write as handle_insights_write
from wechat_export.jobs import JobStore
from wechat_export.loopback import LOOPBACK_HOST, allowed_request_host, validate_bind_host
from wechat_export.media_resolve import resolve_media_file, sniff_media
from wechat_export.media_audit import MediaProbe
from wechat_export.media_stream import open_local_media, byte_range, copy_range, UnsatisfiableRange
from wechat_export.materials import list_materials
from wechat_export.output_locations import OutputLocations, choose_native_folder
from wechat_export.runtime import RuntimePaths, list_local_archives, resolve_runtime, resolve_source_id
from wechat_export.workflow import advance, describe_state, start_read_job

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)

_MESSAGE_BASE = (
    "record_uid",
    "conversation_id",
    "sender_display_name",
    "is_self",
    "timestamp_utc",
    "message_type",
    "preview",
    "readable",
    "source_kind",
    "text",
    "media_kind",
    "media_title",
)


def _message_select(conn: sqlite3.Connection) -> str:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    selected = [name for name in _MESSAGE_BASE if name in cols]
    for extra in ("media_md5", "duration_ms", "card_json"):
        if extra in cols:
            selected.append(extra)
    return ", ".join(selected)


def viewer_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


@dataclass
class ServerContext:
    bind_port: int
    session_token: str
    runtime: RuntimePaths
    export_dir: Path | None = None
    index_path: Path | None = None
    media_root: Path | None = None
    hardlink_db: Path | None = None
    binding: ArchiveBinding | None = None
    job_store: JobStore | None = None
    lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.lock is None:
            self.lock = threading.RLock()
        if self.job_store is None:
            self.job_store = JobStore(self.runtime.jobs_root)
            self.job_store.recover_interrupted()
        from wechat_export.scratch import sweep
        self.scratch_recovery = {
            "jobs": sweep(self.runtime.jobs_root, apply=True),
            "work": sweep(self.runtime.data_root / "work", apply=True),
        }
        if self.export_dir is not None and self.index_path is not None:
            self.activate_archive(self.export_dir, self.index_path, self.media_root, self.hardlink_db)

    def activate_archive(self, root, index, media=None, hardlink=None):
        from wechat_export.scratch import sweep
        recovery = {"selected_archive": sweep(root, apply=True),
                    "selected_slices": sweep(root / "slices", apply=True)}
        binding = ArchiveBinding.capture(root, index, media_root=media, hardlink_db=hardlink)
        with self.lock:
            self.export_dir, self.index_path = root, index
            self.media_root, self.hardlink_db = media, hardlink
            self.binding = binding
        return binding


class ArchiveHTTPServer(ThreadingHTTPServer):
    context: ServerContext


class ArchiveHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(viewer_dir()), **kwargs)

    @property
    def ctx(self) -> ServerContext:
        return self.server.context  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        import sys

        path = urlparse(self.path).path
        sys.stderr.write("archive %s %s %s\n" % (self.address_string(), self.command, path))

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", cookie_header(self.ctx.session_token))
        super().end_headers()

    def _forbidden(self) -> None:
        self.send_response(403)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"error":"forbidden host","code":"forbidden_host"}')

    def _guard(self) -> bool:
        if not allowed_request_host(self.headers.get("Host"), self.ctx.bind_port):
            self._forbidden()
            return False
        return True

    def _send_json(self, payload: object, status: int = 200) -> None:
        binding = getattr(self, "bound_request", None)
        if binding is not None:
            binding.verify()
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error_json(self, exc: BaseException) -> None:
        self.bound_request = None
        if isinstance(exc, ArchiveBindingError):
            self._send_json({"error": str(exc), "code": exc.code}, 409)
            return
        if isinstance(exc, HttpGuardError):
            self._send_json(public_error(exc), exc.status)
            return
        if isinstance(exc, QueryError):
            self._send_json({"error": str(exc), "code": exc.code}, 400)
            return
        if isinstance(exc, InsightsError):
            status = 404 if exc.code == "not_found" else 409 if exc.code in {"revision_conflict", "migration_in_progress"} else 400
            self._send_json({"error": str(exc), "code": exc.code}, status)
            return
        if isinstance(exc, FileNotFoundError):
            self._send_json({"error": "not found", "code": "not_found"}, 404)
            return
        if isinstance(exc, ValueError):
            self._send_json({"error": str(exc), "code": "bad_request"}, 400)
            return
        self._send_json({"error": "internal", "code": "internal"}, 500)

    def _require_archive(self, q=None) -> ArchiveBinding:
        with self.ctx.lock:
            binding = self.ctx.binding
        if binding is None:
            raise HttpGuardError("No archive open", 409, "setup_mode")
        header = self.headers.get("X-Archive-ID")
        query_id = ((q or {}).get("archive_id") or [None])[0]
        supplied = header or query_id
        if not supplied:
            raise ArchiveBindingError("archive_binding_required", "Select an archive before reading or exporting it.")
        if supplied != binding.archive_id or (header and query_id and header != query_id):
            raise ArchiveBindingError("archive_changed", "The selected archive changed. Reopen it and preview again.")
        binding.verify()
        self.bound_request = binding
        return binding

    def _db(self, binding: ArchiveBinding) -> sqlite3.Connection:
        binding.verify()
        conn = sqlite3.connect(binding.index_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _read_json_body(self) -> dict[str, Any]:
        length = parse_content_length(self.headers.get("Content-Length"))
        require_json_content_type(self.headers.get("Content-Type"))
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HttpGuardError("truncated body", 400, "truncated_body")
        return loads_object(raw)

    def _require_write(self) -> None:
        require_write_auth(
            cookie_header_value=self.headers.get("Cookie"),
            csrf_header=self.headers.get(CSRF_HEADER),
            origin=self.headers.get("Origin"),
            session_token=self.ctx.session_token,
            port=self.ctx.bind_port,
        )

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        q = parse_qs(parsed.query)
        if path.startswith("/api/"):
            try:
                self._api_get(path, q)
            except Exception as exc:  # noqa: BLE001
                self._send_error_json(exc)
            return
        if path == "/":
            self.path = "/index.html"
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            body = self._read_json_body()
            self._require_write()
            self._api_post(path, body)
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(exc)

    def do_PATCH(self) -> None:  # noqa: N802
        self.do_POST()

    def _api_get(self, path: str, q: dict[str, list[str]]) -> None:
        if path == '/api/setup/output-locations':
            self._send_json({'locations': OutputLocations(self.ctx.runtime).list()})
            return
        if path == "/api/desktop-health":
            import os, secrets
            token = os.environ.get("WLA_DESKTOP_TOKEN")
            supplied = self.headers.get("X-Desktop-Token", "")
            if not token or not secrets.compare_digest(token, supplied):
                self._send_json({"code":"not_found"}, 404)
            else:
                self._send_json({"desktop_ready":True,"desktop_pid":os.getpid()})
            return
        if path == "/api/bootstrap":
            self._send_json(self._bootstrap())
            return
        if path == "/api/session":
            self._send_json({"csrf": self.ctx.session_token})
            return
        if path == "/api/setup/environment":
            env = collect_environment()
            compat = evaluate_environment(env, private_root=self.ctx.runtime.private_root)
            self._send_json(
                {
                    "platform": env.get("platform"),
                    "machine": env.get("machine"),
                    "mac_ver": env.get("mac_ver"),
                    "wechat_present": env.get("wechat_present"),
                    "wechat_version": env.get("wechat_version"),
                    "wechat_build": env.get("wechat_build"),
                    "wechat_arch": env.get("wechat_arch"),
                    "wechat_fingerprint": env.get("wechat_fingerprint"),
                    "wechat_running": env.get("wechat_running"),
                    "xwechat_root_exists": env.get("xwechat_root_exists"),
                    "tools": env.get("tools"),
                    "disk": env.get("disk"),
                    "compatibility": compat,
                }
            )
            return
        if path == "/api/setup/accounts":
            self._send_json(discover_accounts())
            return
        if path == "/api/setup/archives":
            self._send_json({"archives": list_local_archives(self.ctx.runtime)})
            return
        if path == "/api/setup/materials":
            self._send_json({"materials": list_materials(self.ctx.runtime)})
            return
        if path == "/api/jobs":
            job_id = (q.get("id") or [""])[0]
            job = self.ctx.job_store.get(job_id) if job_id else None
            if not job:
                self._send_json({"error": "not found", "code": "not_found"}, 404)
                return
            self._send_json(describe_state(job))
            return
        binding = self._require_archive(q)
        if handle_insights_get(self, path, q):
            return
        if path == "/api/attachment":
            from wechat_export.recovered_media import attachment, public_attachment
            self._send_json(public_attachment(attachment(binding.root, (q.get("uid") or [""])[0])))
            return
        if path == "/api/media":
            self._serve_media(q, binding)
            return
        conn = self._db(binding)
        try:
            if path == "/api/meta":
                self._send_json({**self._meta(conn), **binding.public()})
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
                self._messages(conn, q)
                return
            if path == "/api/search":
                self._search(conn, q)
                return
            if path == "/api/export/preview":
                spec = QuerySpec.from_mapping(
                    {
                        "scope": {
                            "kind": (q.get("scope") or ["conversations"])[0],
                            "conversation_ids": q.get("conversation_id") or [],
                        },
                        "since": (q.get("since") or [None])[0],
                        "until": (q.get("until") or [None])[0],
                        "readable_only": (q.get("readable") or [""])[0] == "1",
                        "message_types": q.get("message_type") or [],
                        "format": "jsonl",
                        "display_timezone": (q.get("display_timezone") or ["UTC"])[0],
                    },
                    known_ids=known_conversation_ids(conn),
                )
                counts = count_selection(conn, spec)
                self._send_json({"count": counts["selected_count"], "selection_accounting": counts, "query": spec.to_public_dict(), **binding.public()})
                return
            self._send_json({"error": "not found", "code": "not_found"}, 404)
        finally:
            conn.close()

    def _api_post(self, path: str, body: dict[str, Any]) -> None:
        if handle_insights_write(self, "POST" if not self.command == "PATCH" else "PATCH", path, body):
            return
        if path == '/api/setup/storage-plan':
            from wechat_export.discovery import resolve_account_dir
            from wechat_export.storage_plan import estimate_storage
            account = resolve_account_dir(str(body.get('account_id') or ''))
            output = OutputLocations(self.ctx.runtime).resolve(body.get('destination_id', 'default'))
            plan = estimate_storage(source=account / 'db_storage', work_root=self.ctx.runtime.data_root,
                                    output_root=output)
            self._send_json(plan)
            return
        if path == '/api/setup/choose-output':
            if body:
                raise QueryError('此接口只接受系统目录选择，不接受浏览器路径。', 'native_selection_required')
            selected = choose_native_folder()
            if selected is None:
                self._send_json({'cancelled': True})
            else:
                location = OutputLocations(self.ctx.runtime).register_native_selection(selected)
                self._send_json({'cancelled': False, 'location': location})
            return
        if path == "/api/setup/open-archive":
            source_id = str(body.get("source_id") or "")
            export_dir = resolve_source_id(source_id, self.ctx.runtime)
            index = ensure_index_current(export_dir)
            media = export_dir / "media"
            hardlink = export_dir / "hardlink.db"
            binding = self.ctx.activate_archive(export_dir, index, media if media.exists() else None, hardlink if hardlink.exists() else None)
            self._send_json({"ok": True, "source_id": source_id, "mode": "archive", **binding.public()})
            return
        if path == "/api/workflow/start":
            if any(key in body for key in ('path', 'output_path', 'output_root', 'destination_path')):
                raise QueryError('Use a registered destination ID, not a browser path.', 'native_selection_required')
            destination_id = body.get('destination_id', 'default')
            destination_binding = OutputLocations(self.ctx.runtime).binding(destination_id)
            job = start_read_job(
                self.ctx.job_store,
                account_id=body.get("account_id"),
                adapter_id=str(body.get("adapter_id") or "macos-xwechat-4-arm64"),
                synthetic=bool(body.get("synthetic")),
                destination_id=destination_id, destination_binding=destination_binding,
            )
            self._send_json(describe_state(job))
            return
        if path.startswith("/api/workflow/") and path.endswith("/advance"):
            job_id = path.split("/")[3]
            job = self.ctx.job_store.get(job_id)
            if not job:
                raise FileNotFoundError("job")
            if body.get("command") != "cancel" and self.ctx.job_store.busy(job_id):
                self._send_json({"error": "job busy", "code": "job_busy"}, 409)
                return
            if str(body.get("command") or "") in {"continue_from_materials", "prepare_reader", "acquire_key"}:
                def work():
                    current = self.ctx.job_store.get(job_id)
                    if current:
                        advance(self.ctx.job_store, current, str(body["command"]), body, runtime=self.ctx.runtime)
                started = self.ctx.job_store.run_in_thread(job_id, work)
                if not started:
                    self._send_json({"error": "job busy", "code": "job_busy"}, 409)
                    return
                self._send_json({**describe_state(job), "accepted": True}, 202)
                return
            job = advance(
                self.ctx.job_store,
                job,
                str(body.get("command") or ""),
                body,
                runtime=self.ctx.runtime,
            )
            self._send_json(describe_state(job))
            return
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job_id = path.split("/")[3]
            job = self.ctx.job_store.request_cancel(job_id)
            if not job:
                raise FileNotFoundError("job")
            self._send_json(describe_state(job))
            return
        if path.startswith("/api/jobs/") and path.endswith("/reveal"):
            job_id = path.split("/")[3]
            job = self.ctx.job_store.get(job_id)
            if not job or not job.payload.get("path"):
                raise FileNotFoundError("job")
            path_out = Path(job.payload["path"])
            roots = [self.ctx.runtime.exports_root, self.ctx.runtime.slices_root]
            if job.payload.get("authorized_output_root"):
                roots.append(Path(job.payload["authorized_output_root"]))
            if self.ctx.export_dir:
                roots.append(self.ctx.export_dir / "slices")
            if not any(_path_under(path_out, root) for root in roots if root):
                raise ValueError("path not allowed")
            subprocess.run(["open", "-R", str(path_out)], check=False)
            self._send_json({"ok": True, "job_id": job_id})
            return
        if path == "/api/export":
            binding = self._require_archive()
            conn = self._db(binding)
            try:
                spec = QuerySpec.from_mapping(body, known_ids=known_conversation_ids(conn))
                total = count_messages(conn, spec)
            finally:
                conn.close()
            binding.verify()
            job = self._start_export_job(spec, total, binding)
            self._send_json({"job_id": job.job_id, "state": job.state, "total": total, **binding.public()})
            return
        self._send_json({"error": "not found", "code": "not_found"}, 404)

    def _bootstrap(self) -> dict[str, Any]:
        env = collect_environment()
        with self.ctx.lock:
            binding = self.ctx.binding
            scratch_recovery = dict(self.ctx.scratch_recovery)
        return {
            **(binding.public() if binding else {"archive_id": None, "source_revision": None}),
            "mode": "archive" if binding else "setup",
            "csrf": self.ctx.session_token,
            "port": self.ctx.bind_port,
            "runtime": {
                "kind": self.ctx.runtime.kind,
                "data_root_kind": self.ctx.runtime.kind,
                "scratch_cleanup": scratch_recovery,
            },
            "compatibility": evaluate_environment(env, private_root=self.ctx.runtime.private_root),
            "archives": list_local_archives(self.ctx.runtime),
            "materials": list_materials(self.ctx.runtime),
            "archive_import_allowed": True,
        }

    def _meta(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = {r["key"]: r["value"] for r in _rows(conn, "SELECT key, value FROM meta")}
        manifest = {}
        if rows.get("manifest"):
            manifest = json.loads(rows["manifest"])
        return {
            "export_dir": rows.get("export_dir") or (self.bound_request.root.name if getattr(self, "bound_request", None) else ""),
            "message_count": int(rows.get("message_count") or 0),
            "readable_count": int(rows.get("readable_count") or 0),
            "conversation_count": int(rows.get("conversation_count") or 0),
            "display_timezone": rows.get("display_timezone") or "America/Los_Angeles",
            "source_kind": manifest.get("source_kind"),
            "backup2_coverage": manifest.get("backup2_coverage"),
            "targets": manifest.get("targets") or {},
            "interval": "[since,until)",
        }

    def _messages(self, conn: sqlite3.Connection, q: dict[str, list[str]]) -> None:
        cid = (q.get("conversation_id") or [""])[0]
        if not cid:
            self._send_json({"error": "conversation_id required", "code": "bad_request"}, 400)
            return
        readable = (q.get("readable") or ["0"])[0] == "1"
        try:
            limit = min(200, max(1, int((q.get("limit") or ["80"])[0])))
        except ValueError:
            self._send_json({"error": "bad pagination", "code": "bad_request"}, 400)
            return
        where = "conversation_id = ?"
        params: list[Any] = [cid]
        if readable:
            where += " AND readable = 1"
        around = (q.get("around") or [""])[0]
        before_ts = (q.get("before_ts") or [""])[0]
        before_uid = (q.get("before_uid") or [""])[0]
        select = _message_select(conn)
        if around:
            row = conn.execute(
                f"SELECT timestamp_utc, record_uid FROM messages WHERE {where} AND record_uid = ?",
                tuple(params + [around]),
            ).fetchone()
            if row:
                older = _rows(
                    conn,
                    f"SELECT {select} FROM messages WHERE {where} AND (timestamp_utc, record_uid) <= (?, ?) "
                    f"ORDER BY timestamp_utc DESC, record_uid DESC LIMIT ?",
                    tuple(params + [row[0], row[1], 40]),
                )
                newer = _rows(
                    conn,
                    f"SELECT {select} FROM messages WHERE {where} AND (timestamp_utc, record_uid) > (?, ?) "
                    f"ORDER BY timestamp_utc ASC, record_uid ASC LIMIT ?",
                    tuple(params + [row[0], row[1], 40]),
                )
                older.reverse()
                rows = older + newer
            else:
                rows = []
        elif before_ts and before_uid:
            rows = _rows(
                conn,
                f"SELECT {select} FROM messages WHERE {where} AND (timestamp_utc, record_uid) < (?, ?) "
                f"ORDER BY timestamp_utc DESC, record_uid DESC LIMIT ?",
                tuple(params + [before_ts, before_uid, limit]),
            )
            rows.reverse()
        else:
            rows = _rows(
                conn,
                f"SELECT {select} FROM messages WHERE {where} "
                f"ORDER BY timestamp_utc DESC, record_uid DESC LIMIT ?",
                tuple(params + [limit]),
            )
            rows.reverse()
        total = conn.execute(f"SELECT count(*) FROM messages WHERE {where}", params).fetchone()[0]
        has_older = False
        if rows:
            first = rows[0]
            older_n = conn.execute(
                f"SELECT count(*) FROM messages WHERE {where} AND (timestamp_utc, record_uid) < (?, ?)",
                tuple(params + [first["timestamp_utc"], first["record_uid"]]),
            ).fetchone()[0]
            has_older = int(older_n) > 0
        from wechat_export.recovered_media import attachment, public_attachment
        for message in rows:
            recovered = attachment(self.bound_request.root, message['record_uid'])
            if recovered is not None: message['attachment'] = public_attachment(recovered)
        self._send_json(
            {
                "total": int(total),
                "limit": limit,
                "has_older": has_older,
                "messages": rows,
            }
        )

    def _search(self, conn: sqlite3.Connection, q: dict[str, list[str]]) -> None:
        query = (q.get("q") or [""])[0].strip()
        if len(query) < 2:
            self._send_json({"error": "q too short", "code": "bad_request"}, 400)
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

    def _serve_media(self, q: dict[str, list[str]], binding: ArchiveBinding) -> None:
        uid = (q.get("uid") or [""])[0]
        if not uid:
            self._send_json({"error": "not found", "code": "not_found"}, 404)
            return
        from wechat_export.recovered_media import attachment, verified_object
        recovered = attachment(binding.root, uid)
        if recovered is not None:
            if recovered['status'] != 'available':
                self._send_json({"code":"media_missing","status":recovered['status']},404)
                return
            with verified_object(binding.root, recovered) as (stream, size):
                binding.verify()
                header = None if self.headers.get("If-Range") else self.headers.get("Range")
                try: start,end,partial = byte_range(header,size)
                except UnsatisfiableRange:
                    self.send_response(416);self.send_header("Content-Range",f"bytes */{size}");self.send_header("Content-Length","0");self.end_headers();return
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", recovered['mime'])
                self.send_header("Content-Length",str(end-start));self.send_header("Accept-Ranges","bytes")
                if partial:self.send_header("Content-Range",f"bytes {start}-{end-1}/{size}")
                download = recovered['kind']=='file' or (q.get('download') or [''])[0]=='1'
                disposition='attachment' if download else 'inline'
                self.send_header("Content-Disposition", disposition+"; filename*=UTF-8''"+quote(recovered['filename'],safe=''))
                self.end_headers()
                try:copy_range(stream,self.wfile,start,end)
                except (OSError,EOFError):self.close_connection=True
            return
        conn = self._db(binding)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            extra = ", media_md5, duration_ms" if "media_md5" in cols else ""
            row = conn.execute(
                f"SELECT conversation_id, timestamp_utc, text, message_type, media_kind{extra} "
                f"FROM messages WHERE record_uid = ?",
                (uid,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            self._send_json({"error": "not found", "code": "not_found"}, 404)
            return
        mapping = dict(row)
        resolved = resolve_media_file(
            media_root=binding.media_root,
            hardlink_db=binding.hardlink_db,
            conversation_id=mapping["conversation_id"],
            timestamp_utc=mapping["timestamp_utc"],
            payload=mapping["text"],
            type_name=mapping["message_type"] or mapping.get("media_kind") or "unknown",
            md5=mapping.get("media_md5"),
            media_kind=mapping.get("media_kind"),
        )
        if not resolved.get("found") or not resolved.get("path"):
            self._send_json({"error": "media missing", "code": "media_missing", "status": resolved.get("status")}, 404)
            return
        path = Path(resolved["path"])
        with open_local_media(binding.media_root, path) as (stream, size):
            binding.verify()
            mime = sniff_media(stream.read(32)) or "application/octet-stream"
            # No validators are issued for mutable attachments. An If-Range
            # condition therefore cannot be confirmed: send the full body.
            range_header = None if self.headers.get("If-Range") else self.headers.get("Range")
            try:
                start, end, partial = byte_range(range_header, size)
            except UnsatisfiableRange:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end - 1}/{size}")
            if mime == "application/octet-stream":
                self.send_header("Content-Disposition", "attachment")
            self.end_headers()
            try:
                copy_range(stream, self.wfile, start, end)
            except (OSError, EOFError):
                # Headers/body have begun: never append a JSON error to media.
                self.close_connection = True

    def _start_export_job(self, spec: QuerySpec, total: int, binding: ArchiveBinding):
        store = self.ctx.job_store
        export_dir = binding.root
        job = store.create(
            "export",
            "queued",
            {
                "archive_id": binding.archive_id,
                "source_revision": binding.revision,
                "authorized_output_root": str(export_dir / "slices"),
                "query": spec.to_public_dict(),
                "written": 0,
                "total": total,
                "phase_detail": "Export queued",
            },
        )

        def run() -> None:
            current = store.get(job.job_id)
            if not current or store.cancelled(job.job_id):
                return
            current.state = "snapshotting"
            current.payload["phase_detail"] = "Copying the selected archive revision, not the active tab's source"
            store.save(current)
            conn = None
            try:
                from wechat_export.scratch import ScratchSpace
                with ScratchSpace(self.ctx.runtime.jobs_root, "http-export-source") as temporary:
                    snapshot = binding.snapshot_to(temporary.payload / "archive", lambda: store.cancelled(job.job_id))
                    conn = sqlite3.connect((snapshot / "archive.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
                    conn.row_factory = sqlite3.Row
                    current.state = "running"
                    current.payload["phase_detail"] = "Writing the pinned canonical source"
                    store.save(current)
                    def progress(n: int) -> None:
                        cur = store.get(job.job_id)
                        if cur:
                            cur.payload["written"] = n
                            store.save(cur)
                    result = write_slice(
                        conn, snapshot, spec, jobs_root=export_dir / "slices", source="canonical",
                        should_cancel=lambda: store.cancelled(job.job_id), on_progress=progress,
                        expected_count=total, source_binding=binding.public(),
                        media_probe=MediaProbe(binding.media_root, binding.hardlink_db),
                    )
                    conn.close()
                    conn = None
                cur = store.get(job.job_id)
                if not cur:
                    return
                if cur.cancel_requested:
                    cur.state = "cancelled"
                    store.save(cur)
                    return
                cur.state = "ready"
                cur.payload["written"] = result["count"]
                cur.payload["count"] = result["count"]
                cur.payload["path"] = result["path"]
                cur.payload["public_path"] = result["path"]
                cur.payload["result_id"] = result["job_id"]
                cur.payload["phase_detail"] = "Export complete"
                store.save(cur)
            except ArchiveBindingError as exc:
                cur = store.get(job.job_id)
                if cur:
                    cur.state = "failed"
                    cur.error = {"code": exc.code, "error": str(exc)}
                    store.save(cur)
            except ExportCancelled:
                cur = store.get(job.job_id)
                if cur:
                    cur.state = "cancelled"
                    cur.payload["phase_detail"] = "Export cancelled"
                    store.save(cur)
            except Exception:
                cur = store.get(job.job_id)
                if cur:
                    cur.state = "failed"
                    cur.error = {"code": "export_failed", "error": "export failed"}
                    store.save(cur)
            finally:
                if conn is not None:
                    conn.close()

        store.run_in_thread(job.job_id, run)
        return job


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def serve(
    export_dir: Path | None = None,
    host: str = LOOPBACK_HOST,
    port: int = 8765,
    runtime: RuntimePaths | None = None,
) -> None:
    host = validate_bind_host(host)
    runtime = runtime or resolve_runtime()
    index = None
    media_root = None
    hardlink = None
    if export_dir is not None:
        export_dir = export_dir.resolve()
        index = ensure_index_current(export_dir)
        media = export_dir / "media"
        media_root = media if media.exists() else None
        hardlink_path = export_dir / "hardlink.db"
        hardlink = hardlink_path if hardlink_path.exists() else None
    static = viewer_dir()
    if not (static / "index.html").exists():
        raise FileNotFoundError(f"viewer assets missing at {static}")
    ctx = ServerContext(
        bind_port=port,
        session_token=new_session_token(),
        runtime=runtime,
        export_dir=export_dir,
        index_path=index,
        media_root=media_root,
        hardlink_db=hardlink,
    )
    httpd = ArchiveHTTPServer((host, port), ArchiveHandler)
    httpd.context = ctx
    print(f"archive viewer http://{host}:{port}")
    httpd.serve_forever()
