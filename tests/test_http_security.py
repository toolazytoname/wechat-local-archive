from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from wechat_export.archive_index import build_index
from wechat_export.archive_server import ArchiveHTTPServer, ArchiveHandler, ServerContext
from wechat_export.http_security import parse_content_length, HttpGuardError, new_session_token
from wechat_export.runtime import resolve_runtime

from tests.test_export_service import _demo_tree


class ContentLengthTests(unittest.TestCase):
    def test_missing_negative_and_garbage(self) -> None:
        with self.assertRaises(HttpGuardError) as ctx:
            parse_content_length(None)
        self.assertEqual(ctx.exception.code, "content_length_required")
        with self.assertRaises(HttpGuardError):
            parse_content_length("-3")
        with self.assertRaises(HttpGuardError):
            parse_content_length("1e9")
        with self.assertRaises(HttpGuardError):
            parse_content_length("nope")
        self.assertEqual(parse_content_length("12"), 12)


class HttpWriteGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _demo_tree(Path(self.tmp.name) / "demo")
        runtime = resolve_runtime(Path(self.tmp.name) / "data")
        self.token = new_session_token()
        ctx = ServerContext(
            bind_port=0,
            session_token=self.token,
            runtime=runtime,
            export_dir=self.root,
            index_path=self.root / "archive.sqlite",
        )
        self.httpd = ArchiveHTTPServer(("127.0.0.1", 0), ArchiveHandler)
        ctx.bind_port = self.httpd.server_address[1]
        self.httpd.context = ctx
        self.port = ctx.bind_port
        self.archive_id = ctx.binding.archive_id if ctx.binding else None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def _request(self, path: str, body: bytes | None, headers: dict[str, str], include_length: bool = True) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", path)
        for key, value in headers.items():
            conn.putheader(key, value)
        if include_length:
            payload = body or b""
            conn.putheader("Content-Length", str(len(payload)))
            conn.endheaders(payload)
        else:
            conn.endheaders()
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {"raw": raw.decode("utf-8", "replace")}
        return resp.status, payload

    def _ok_headers(self) -> dict[str, str]:
        return {
            "Host": f"127.0.0.1:{self.port}",
            "Origin": f"http://127.0.0.1:{self.port}",
            "Content-Type": "application/json",
            "Cookie": f"wla_session={self.token}",
            "X-CSRF-Token": self.token,
            "X-Archive-ID": self.archive_id,
        }

    def test_missing_session_rejected(self) -> None:
        headers = self._ok_headers()
        headers.pop("Cookie")
        status, payload = self._request("/api/export", b'{"scope":{"kind":"all"}}', headers)
        self.assertEqual(status, 403)
        self.assertEqual(payload.get("code"), "session_required")

    def test_external_origin_rejected(self) -> None:
        headers = self._ok_headers()
        headers["Origin"] = "http://evil.example"
        status, payload = self._request("/api/export", b'{"scope":{"kind":"all"}}', headers)
        self.assertEqual(status, 403)
        self.assertEqual(payload.get("code"), "origin_denied")

    def test_missing_content_length_rejected(self) -> None:
        headers = self._ok_headers()
        status, payload = self._request("/api/export", b"{}", headers, include_length=False)
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("code"), "content_length_required")

    def test_empty_selection_http(self) -> None:
        status, payload = self._request(
            "/api/export",
            json.dumps({"scope": {"kind": "conversations", "conversation_ids": []}}).encode(),
            self._ok_headers(),
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("code"), "empty_selection")

    def _wait_job(self, job_id: str) -> dict:
        deadline = time.time() + 8
        while time.time() < deadline:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("GET", f"/api/jobs?id={job_id}", headers={"Host": f"127.0.0.1:{self.port}"})
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            conn.close()
            if payload.get("state") in {"ready", "failed", "cancelled"}:
                return payload
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish")

    def test_explicit_all_and_unique_jobs(self) -> None:
        body = json.dumps({"scope": {"kind": "all"}, "format": "jsonl"}).encode()
        status1, first = self._request("/api/export", body, self._ok_headers())
        status2, second = self._request("/api/export", body, self._ok_headers())
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertNotEqual(first["job_id"], second["job_id"])
        done1 = self._wait_job(first["job_id"])
        done2 = self._wait_job(second["job_id"])
        self.assertEqual(done1["state"], "ready")
        self.assertEqual(done2["state"], "ready")
        self.assertEqual(done1["payload_public"]["written"], 4)
        path1 = done1["payload_public"]["path"]
        path2 = done2["payload_public"]["path"]
        self.assertTrue(Path(path1).is_file())
        self.assertTrue(Path(path2).is_file())
        self.assertNotEqual(path1, path2)

    def test_since_zulu_preview_counts_four(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "GET",
            "/api/export/preview?scope=all&since=2026-01-02T01:00:00.000Z",
            headers={"Host": f"127.0.0.1:{self.port}", "X-Archive-ID": self.archive_id},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(payload["count"], 4)

    def test_path_traversal_source_id(self) -> None:
        status, payload = self._request(
            "/api/setup/open-archive",
            json.dumps({"source_id": "export:../secret"}).encode(),
            self._ok_headers(),
        )
        self.assertEqual(status, 400)

    def test_index_still_readable(self) -> None:
        conn = sqlite3.connect(self.root / "archive.sqlite")
        n = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        conn.close()
        self.assertEqual(n, 4)


class SetupModeHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        runtime = resolve_runtime(Path(self.tmp.name) / "data")
        self.token = new_session_token()
        ctx = ServerContext(
            bind_port=0,
            session_token=self.token,
            runtime=runtime,
            export_dir=None,
            index_path=None,
        )
        self.httpd = ArchiveHTTPServer(("127.0.0.1", 0), ArchiveHandler)
        ctx.bind_port = self.httpd.server_address[1]
        self.httpd.context = ctx
        self.port = ctx.bind_port
        self.archive_id = ctx.binding.archive_id if ctx.binding else None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_bootstrap_is_setup_without_archive(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        conn.request("GET", "/api/bootstrap", headers={"Host": f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(payload["mode"], "setup")
        self.assertTrue(payload["archive_import_allowed"])
        self.assertIn("csrf", payload)
        self.assertFalse(payload["compatibility"]["stages"]["key_acquisition_verified"])

    def test_meta_conflict_without_archive(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/meta", headers={"Host": f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        conn.close()
        self.assertEqual(resp.status, 409)
        self.assertEqual(payload.get("code"), "setup_mode")
