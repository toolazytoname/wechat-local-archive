from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from wechat_export.archive_server import ArchiveHTTPServer, ArchiveHandler, ServerContext
from wechat_export.http_security import new_session_token
from wechat_export.runtime import resolve_runtime
from tests.test_insights_identity import _msg, _tree


class InsightsHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        xml = '<?xml version="1.0"?><msg><appmsg><title>Article</title><url>https://example.invalid/learn</url></appmsg></msg>'
        self.root = _tree(
            Path(self.tmp.name) / "demo",
            [
                _msg(record_uid="1", is_self=True, sender_id="me", text="我想每周整理一次笔记"),
                _msg(record_uid="2", is_self=False, sender_id="wxid_alice", text="Yes"),
                _msg(record_uid="3", is_self=True, sender_id="me", text="second self line"),
                _msg(record_uid="5", is_self=True, sender_id="me", text="第三句本人发言用来满足样本下限。", timestamp_utc="2026-01-02T01:00:05+00:00"),
                _msg(
                    record_uid="4",
                    conversation_id="room@chatroom",
                    conversation_type="room",
                    conversation_display_name="Studio",
                    is_self=False,
                    sender_id="wxid_alice",
                    text=xml,
                    message_type_normalized="app",
                ),
            ],
        )
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
        self.archive_id = ctx.binding.archive_id
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def _headers(self, write=False) -> dict[str, str]:
        headers = {"Host": f"127.0.0.1:{self.port}", "X-Archive-ID": self.archive_id}
        if write:
            headers.update(
                {
                    "Origin": f"http://127.0.0.1:{self.port}",
                    "Content-Type": "application/json",
                    "Cookie": f"wla_session={self.token}",
                    "X-CSRF-Token": self.token,
                }
            )
        return headers

    def _get(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        conn.request("GET", path, headers=self._headers())
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode())
        conn.close()
        return resp.status, payload

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        raw = json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        headers = self._headers(True)
        headers["Content-Length"] = str(len(raw))
        conn.request("POST", path, body=raw, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode())
        conn.close()
        return resp.status, payload

    def test_context_import_profile_and_learning(self) -> None:
        st, ctx = self._get("/api/insights/context")
        self.assertEqual(st, 200)
        self.assertEqual(ctx["audit"]["verification_state"], "consistent")
        st, saved = self._post("/api/insights/context", {"accept_consistent_self": True})
        self.assertEqual(st, 200)
        st, role = self._post("/api/insights/context", {"conversation_id": "room@chatroom", "purpose": "read_later"})
        self.assertEqual(role["conversation_roles"][0]["purpose"], "read_later")
        st, imported = self._post("/api/learning/imports", {"conversation_id": "room@chatroom"})
        self.assertEqual(st, 200)
        self.assertGreaterEqual(imported["items_created"], 1)
        st, items = self._get("/api/learning/items")
        self.assertGreaterEqual(items["count"], 1)
        st, preview = self._post("/api/profiles/preview", {})
        self.assertTrue(preview["available"])
        self.assertFalse(preview["remote"])
        self.assertIn("non_readable_by_kind", preview["coverage"])
        st, denied = self._post("/api/profiles/runs", {"kind": "self", "scope": {}, "engine": "byok", "approve_remote": True})
        self.assertEqual(st, 400)
        self.assertEqual(denied["code"], "needs_consent")
        st, run = self._post("/api/profiles/runs", {"kind": "self", "scope": {}})
        self.assertEqual(st, 200)
        self.assertFalse(any(o.get("synthetic") for o in run.get("observations") or []))
        st, pack = self._post("/api/insights/exports", {"kind": "learning"})
        self.assertEqual(st, 200)
        self.assertGreater(pack["item_count"], 0)
        self.assertTrue(Path(pack["path"]).is_dir())

    def test_fetch_stays_disabled(self) -> None:
        self._post("/api/insights/context", {"accept_consistent_self": True})
        self._post("/api/learning/imports", {"conversation_id": "room@chatroom"})
        _, items = self._get("/api/learning/items")
        item_id = items["items"][0]["item_id"]
        st, payload = self._post(f"/api/learning/items/{item_id}/fetch", {"url": "https://example.invalid/x"})
        self.assertEqual(st, 400)
        self.assertEqual(payload["code"], "fetch_disabled")
        st, payload = self._post(f"/api/learning/items/{item_id}/fetch", {"url": "http://127.0.0.1/secret"})
        self.assertEqual(payload["code"], "fetch_disabled")

    def test_remote_preview_and_consented_run(self) -> None:
        from wechat_export.insights.providers import save_provider_config

        save_provider_config(
            self.httpd.context.runtime.private_root,
            {
                "kind": "remote",
                "engine_id": "weichao_gpt-6-astra",
                "base_url": "https://token.weichao.site/v1",
                "model": "gpt-6-astra",
                "api_key": "sk-test-not-real",
            },
        )
        self._post("/api/insights/context", {"accept_consistent_self": True})
        st, preview = self._post("/api/profiles/preview", {})
        self.assertEqual(st, 200)
        self.assertTrue(preview["remote"])
        self.assertEqual(preview["consent"]["host"], "token.weichao.site")
        self.assertNotIn("sk-test-not-real", json.dumps(preview))
        st, blocked = self._post("/api/profiles/runs", {"kind": "self", "scope": {}})
        self.assertEqual(st, 200)
        self.assertEqual(blocked["engine_id"], "local_explicit")

        class FakeResp:
            status = 200

            def read(self, _n=-1):
                content = {
                    "observations": [
                        {
                            "dimension": "stated_plans",
                            "statement": "我想每周整理一次笔记",
                            "basis": "explicit_fact",
                            "quote": "我想每周整理一次笔记",
                            "evidence": [
                                {
                                    "record_uid": "1",
                                    "conversation_id": "wxid_alice",
                                    "sender_id": "me",
                                    "quote": "我想每周整理一次笔记",
                                }
                            ],
                            "evidence_ids": ["1"],
                            "caveats": ["原话"],
                        }
                    ]
                }
                return json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def fake_urlopen(req, timeout=None, context=None):
            self.assertEqual(req.full_url, "https://token.weichao.site/v1/chat/completions")
            posted = json.loads(req.data.decode())
            self.assertNotIn("sk-test-not-real", json.dumps(posted))
            return FakeResp()

        st, forged = self._post("/api/profiles/runs", {"kind": "self", "scope": {}, "engine": "byok", "approve_remote": "false"})
        self.assertEqual(st, 400)
        self.assertEqual(forged["code"], "needs_consent")
        st, ticket = self._post(
            "/api/profiles/consent",
            {"kind": "self", "scope": {}, "engine": "byok", "approve_remote": True},
        )
        self.assertEqual(st, 200)
        with unittest.mock.patch("wechat_export.insights.providers.default_urlopen", fake_urlopen):
            st, run = self._post(
                "/api/profiles/runs",
                {
                    "kind": "self",
                    "scope": {},
                    "engine": "byok",
                    "approve_remote": True,
                    "consent_ticket": ticket["ticket_id"],
                },
            )
        self.assertEqual(st, 200)
        self.assertEqual(run["engine_id"], "weichao_gpt-6-astra")
        self.assertIn("每周整理一次笔记", " ".join(o["statement"] for o in run["observations"]))
        self.assertFalse(any(o.get("synthetic") for o in run["observations"]))

    def test_grok_cli_preview_and_consented_run(self) -> None:
        from wechat_export.insights.providers import save_provider_config

        binary = Path(self.tmp.name) / "fake-grok"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(binary, 0o755)
        save_provider_config(
            self.httpd.context.runtime.private_root,
            {
                "kind": "grok_cli",
                "engine_id": "grok-cli-grok-4.6",
                "command": str(binary),
                "model": "grok-4.6",
            },
        )
        self._post("/api/insights/context", {"accept_consistent_self": True})
        st, preview = self._post("/api/profiles/preview", {})
        self.assertEqual(st, 200)
        self.assertTrue(preview["remote"])
        self.assertEqual(preview["engine_kind"], "grok_cli")
        self.assertEqual(preview["consent"]["host"], "grok.com")
        st, local = self._post("/api/profiles/runs", {"kind": "self", "scope": {}})
        self.assertEqual(st, 200)
        self.assertEqual(local["engine_id"], "local_explicit")

        def fake_run(argv, cwd=None, timeout=None, capture_output=None, text=None, env=None):
            self.assertIn("--prompt-file", argv)
            self.assertEqual(argv[argv.index("-m") + 1], "grok-4.6")
            content = {
                "observations": [
                    {
                        "dimension": "stated_plans",
                        "statement": "我想每周整理一次笔记",
                        "basis": "explicit_fact",
                        "quote": "我想每周整理一次笔记",
                        "evidence": [
                            {
                                "record_uid": "1",
                                "conversation_id": "wxid_alice",
                                "sender_id": "me",
                                "quote": "我想每周整理一次笔记",
                            }
                        ],
                        "evidence_ids": ["1"],
                        "caveats": ["原话"],
                    }
                ]
            }
            return type("Proc", (), {"returncode": 0, "stdout": json.dumps({"structured_output": content}), "stderr": ""})()

        st, ticket = self._post(
            "/api/profiles/consent",
            {"kind": "self", "scope": {}, "engine": "grok_cli", "approve_remote": True},
        )
        self.assertEqual(st, 200)
        with unittest.mock.patch("wechat_export.insights.providers.subprocess.run", fake_run):
            st, run = self._post(
                "/api/profiles/runs",
                {
                    "kind": "self",
                    "scope": {},
                    "engine": "grok_cli",
                    "approve_remote": True,
                    "consent_ticket": ticket["ticket_id"],
                },
            )
        self.assertEqual(st, 200)
        self.assertEqual(run["engine_id"], "grok-cli-grok-4.6")
        self.assertIn("每周整理一次笔记", " ".join(o["statement"] for o in run["observations"]))

    def test_engine_catalog_byok_key_stays_off_the_wire(self) -> None:
        from wechat_export.insights.providers import save_provider_config

        save_provider_config(
            self.httpd.context.runtime.private_root,
            {"kind": "grok_cli", "engine_id": "grok-cli-grok-4.6", "model": "grok-4.6", "command": "/bin/echo"},
        )
        st, listing = self._get("/api/insights/engines")
        self.assertEqual(st, 200)
        ids = [item["id"] for item in listing["engines"]]
        self.assertEqual(ids, ["local_explicit", "grok_cli", "byok"])
        st, saved = self._post(
            "/api/insights/engines",
            {
                "default": "byok",
                "byok": {
                    "base_url": "https://token.weichao.site/v1",
                    "model": "gpt-6-astra",
                    "api_key": "sk-test-not-real",
                },
            },
        )
        self.assertEqual(st, 200)
        self.assertEqual(saved["default"], "byok")
        self.assertNotIn("sk-test-not-real", json.dumps(saved))
        st, again = self._get("/api/insights/engines")
        self.assertNotIn("sk-test-not-real", json.dumps(again))
        byok = next(item for item in again["engines"] if item["id"] == "byok")
        self.assertTrue(byok["has_api_key"])
        self._post("/api/insights/context", {"accept_consistent_self": True})
        st, preview = self._post("/api/profiles/preview", {})
        self.assertTrue(preview["remote"])
        self.assertEqual(preview["default_engine"], "byok")
        self.assertGreaterEqual(len(preview["engines"]), 3)
        st, denied = self._post("/api/profiles/runs", {"kind": "self", "scope": {}, "engine": "byok"})
        self.assertEqual(st, 400)
        self.assertEqual(denied["code"], "needs_consent")
