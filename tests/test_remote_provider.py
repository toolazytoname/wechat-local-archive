from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from wechat_export.insights.identity import audit_self, save_identity, set_role
from wechat_export.insights.profile_pipeline import run_profile
from wechat_export.insights.providers import (
    GrokCliProvider,
    RemoteOpenAIProvider,
    load_provider_config,
    public_engine_list,
    public_provider_view,
    resolve_provider,
    save_provider_config,
    update_engine_catalog,
)
from wechat_export.insights.store import InsightsError, open_store
from tests.test_insights_identity import _msg, _tree


def _remote_config(**kwargs) -> dict:
    cfg = {
        "kind": "remote",
        "engine_id": "weichao_gpt-6-astra",
        "display_name": "token.weichao.site / gpt-6-astra",
        "base_url": "https://token.weichao.site/v1",
        "model": "gpt-6-astra",
        "api_key": "sk-test-not-real",
    }
    cfg.update(kwargs)
    return cfg


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class RemoteProviderTests(unittest.TestCase):
    def test_save_config_is_private_and_redacted_in_public_view(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            path = save_provider_config(private, _remote_config())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = load_provider_config(private)
            self.assertEqual(loaded["model"], "gpt-6-astra")
            view = public_provider_view(loaded)
            blob = json.dumps(view)
            self.assertNotIn("sk-test-not-real", blob)
            self.assertNotIn('"api_key":', blob)
            self.assertTrue(view["remote"])
            self.assertEqual(view["host"], "token.weichao.site")
            self.assertEqual(view["model"], "gpt-6-astra")

    def test_resolve_remote_without_consent_cannot_analyze(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            save_provider_config(private, _remote_config())
            provider = resolve_provider(private, allow_synthetic=False)
            self.assertEqual(provider.kind, "remote")
            self.assertTrue(provider.available())
            with self.assertRaises(InsightsError) as ctx:
                provider.analyze({"records": [{"record_uid": "s1", "text": "我想每周整理一次笔记"}]})
            self.assertEqual(ctx.exception.code, "needs_consent")

    def test_prefer_local_even_when_remote_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            save_provider_config(private, _remote_config())
            provider = resolve_provider(private, prefer_local=True)
            self.assertEqual(provider.kind, "local_explicit")

    def test_rejects_loopback_and_http(self) -> None:
        with self.assertRaises(InsightsError) as ctx:
            RemoteOpenAIProvider(
                _remote_config(base_url="http://token.weichao.site/v1"),
                consent_granted=True,
            ).analyze({"records": [{"record_uid": "s1", "text": "x"}]})
        self.assertEqual(ctx.exception.code, "bad_provider")
        with self.assertRaises(InsightsError) as ctx:
            RemoteOpenAIProvider(
                _remote_config(base_url="https://127.0.0.1/v1"),
                consent_granted=True,
            ).analyze({"records": [{"record_uid": "s1", "text": "x"}]})
        self.assertEqual(ctx.exception.code, "bad_provider")

    def test_analyze_posts_openai_payload_and_parses_observations(self) -> None:
        calls: list = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(req)
            content = {
                "observations": [
                    {
                        "dimension": "stated_plans",
                        "statement": "我想每周整理一次笔记",
                        "basis": "explicit_fact",
                        "quote": "我想每周整理一次笔记",
                        "evidence": [
                            {
                                "record_uid": "s1",
                                "conversation_id": "wxid_alice",
                                "sender_id": "me",
                                "quote": "我想每周整理一次笔记",
                            }
                        ],
                        "evidence_ids": ["s1"],
                        "caveats": ["这是记录中的原话。"],
                    }
                ]
            }
            return _FakeResponse({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]})

        provider = RemoteOpenAIProvider(_remote_config(), consent_granted=True, urlopen=fake_urlopen)
        result = provider.analyze(
            {
                "kind": "self",
                "self_ids": ["me"],
                "records": [
                    {
                        "record_uid": "s1",
                        "conversation_id": "wxid_alice",
                        "sender_id": "me",
                        "timestamp_utc": "2026-01-02T01:00:00+00:00",
                        "text": "我想每周整理一次笔记，留下读后感。",
                    }
                ],
            }
        )
        self.assertEqual(result["engine_id"], "weichao_gpt-6-astra")
        self.assertFalse(result["synthetic"])
        self.assertEqual(result["observations"][0]["evidence_ids"], ["s1"])
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertEqual(req.full_url, "https://token.weichao.site/v1/chat/completions")
        self.assertTrue(req.get_header("Authorization").startswith("Bearer sk-test"))
        posted = json.loads(req.data.decode("utf-8"))
        self.assertEqual(posted["model"], "gpt-6-astra")
        self.assertNotIn("sk-test-not-real", json.dumps(posted))
        user_payload = json.loads(posted["messages"][1]["content"])
        self.assertEqual(user_payload["records"][0]["record_uid"], "s1")
        self.assertNotIn("api_key", user_payload)

    def test_run_profile_uses_remote_then_drops_forged_and_foreign_speech(self) -> None:
        class FakeRemote:
            kind = "remote"
            engine_id = "fake-remote"

            def analyze(self, payload):
                rec = payload["records"][0]
                return {
                    "observations": [
                        {
                            "dimension": "stated_plans",
                            "statement": "我想每周整理一次笔记，留下读后感。",
                            "basis": "explicit_fact",
                            "quote": "我想每周整理一次笔记，留下读后感。",
                            "evidence": [
                                {
                                    "record_uid": rec["record_uid"],
                                    "conversation_id": rec["conversation_id"],
                                    "sender_id": rec["sender_id"],
                                    "quote": "我想每周整理一次笔记，留下读后感。",
                                }
                            ],
                            "evidence_ids": [rec["record_uid"]],
                            "caveats": ["原话"],
                        },
                        {
                            "dimension": "stated_plans",
                            "statement": "invented",
                            "basis": "explicit_fact",
                            "quote": "invented",
                            "evidence": [{"record_uid": "missing", "sender_id": "me", "quote": "invented"}],
                            "evidence_ids": ["missing"],
                        },
                        {
                            "dimension": "stated_plans",
                            "statement": "他的MBTI是INTJ",
                            "basis": "explicit_fact",
                            "quote": rec["text"][:8],
                            "evidence": [
                                {
                                    "record_uid": rec["record_uid"],
                                    "conversation_id": rec["conversation_id"],
                                    "sender_id": rec["sender_id"],
                                    "quote": rec["text"][:8],
                                }
                            ],
                            "evidence_ids": [rec["record_uid"]],
                        },
                    ]
                }

        with tempfile.TemporaryDirectory() as td:
            root = _tree(
                Path(td) / "e",
                [
                    _msg(record_uid="s1", is_self=True, sender_id="me", text="我想每周整理一次笔记，留下读后感。"),
                    _msg(record_uid="s2", is_self=True, sender_id="me", text="I plan to keep a reading log."),
                    _msg(record_uid="s3", is_self=True, sender_id="me", text="随便说一句也算样本。"),
                    _msg(
                        record_uid="s4",
                        conversation_id="room@chatroom",
                        conversation_type="room",
                        conversation_display_name="Studio",
                        is_self=True,
                        sender_id="me",
                        text="我想把收藏群里的文章当成我的职业能力。",
                    ),
                ],
            )
            store = open_store(Path(td) / "data", "rev")
            conn = sqlite3.connect(root / "archive.sqlite")
            conn.row_factory = sqlite3.Row
            save_identity(store, audit_self(conn))
            set_role(store, "room@chatroom", "read_later")
            run = run_profile(
                store,
                conn,
                kind="self",
                source_revision="rev",
                scope={},
                subject_person_id="self",
                engine_id="fake-remote",
                provider=FakeRemote(),
            )
            conn.close()
            statements = " ".join(o["statement"] for o in run["observations"])
            self.assertIn("每周整理一次笔记", statements)
            self.assertNotIn("invented", statements)
            self.assertNotIn("MBTI", statements)
            self.assertNotIn("职业能力", statements)
            self.assertTrue(all(not o["synthetic"] for o in run["observations"]))
            self.assertEqual(run["engine_id"], "fake-remote")

    def test_api_key_file_is_used_instead_of_env_agnes_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "relay.key"
            key_path.write_text("sk-file-key-only", encoding="utf-8")
            os.chmod(key_path, 0o600)
            captured = {}

            def fake_urlopen(req, timeout=None, context=None):
                captured["auth"] = req.get_header("Authorization")
                return _FakeResponse({"choices": [{"message": {"content": '{"observations":[]}'}}]})

            provider = RemoteOpenAIProvider(
                _remote_config(api_key="", api_key_file=str(key_path), api_key_env="AGNES_API_KEY"),
                consent_granted=True,
                urlopen=fake_urlopen,
            )
            with patch.dict(os.environ, {"AGNES_API_KEY": "sk-agnes-must-not-be-used"}, clear=False):
                provider.analyze({"records": [{"record_uid": "s1", "text": "我想每周整理一次笔记"}]})
            self.assertEqual(captured["auth"], "Bearer sk-file-key-only")

    def test_ui_wires_per_task_consent(self) -> None:
        source = Path(__file__).resolve().parents[1] / "wechat_export" / "static" / "profiles.js"
        text = source.read_text(encoding="utf-8")
        self.assertIn("approve_remote", text)
        self.assertIn("批准本次", text)
        self.assertIn("不发送附件和密钥", text)
        self.assertIn("Grok CLI", text)
        self.assertIn("BYOK", text)
        self.assertIn("生成本机原话", text)
        self.assertIn("用 AI 生成", text)
        self.assertIn("approve_remote", text)
        self.assertIn("AI 后端", text)
        self.assertNotRegex(text, r"sk-[A-Za-z0-9_-]{8,}")


class DualEngineCatalogTests(unittest.TestCase):
    def test_legacy_and_catalog_can_select_cli_or_byok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            binary = Path(td) / "grok"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(binary, 0o755)
            listing = update_engine_catalog(
                private,
                {
                    "default": "grok_cli",
                    "grok_cli": {"command": str(binary), "model": "grok-4.6"},
                    "byok": {
                        "base_url": "https://token.weichao.site/v1",
                        "model": "gpt-6-astra",
                        "api_key": "sk-test-not-real",
                    },
                },
            )
            blob = json.dumps(listing)
            self.assertNotIn("sk-test-not-real", blob)
            self.assertEqual(listing["default"], "grok_cli")
            ids = [item["id"] for item in listing["engines"]]
            self.assertEqual(ids, ["local_explicit", "grok_cli", "byok"])
            byok = next(item for item in listing["engines"] if item["id"] == "byok")
            self.assertTrue(byok["has_api_key"])
            grok = resolve_provider(private, engine="grok_cli")
            self.assertEqual(grok.kind, "grok_cli")
            with self.assertRaises(InsightsError) as ctx:
                grok.analyze({"records": [{"record_uid": "s1", "text": "x"}]})
            self.assertEqual(ctx.exception.code, "needs_consent")
            byok_provider = resolve_provider(private, engine="byok", consent={"approve_remote": True})
            self.assertEqual(byok_provider.kind, "remote")
            self.assertTrue(byok_provider.consent_granted)
            local = resolve_provider(private, prefer_local=True)
            self.assertEqual(local.kind, "local_explicit")
            again = public_engine_list(private)
            self.assertNotIn("sk-test-not-real", json.dumps(again))
            stored = (private / "analysis-provider.json").read_text(encoding="utf-8")
            self.assertIn('"api_key"', stored)
            self.assertEqual((private / "analysis-provider.json").stat().st_mode & 0o777, 0o600)

    def test_rejects_loopback_byok_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            with self.assertRaises(InsightsError) as ctx:
                update_engine_catalog(private, {"byok": {"base_url": "https://127.0.0.1/v1", "model": "gpt"}})
            self.assertEqual(ctx.exception.code, "bad_provider")


class GrokCliProviderTests(unittest.TestCase):
    def test_consent_and_public_view(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            binary = Path(td) / "grok"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(binary, 0o755)
            save_provider_config(
                private,
                {
                    "kind": "grok_cli",
                    "engine_id": "grok-cli-grok-4.6",
                    "command": str(binary),
                    "model": "grok-4.6",
                },
            )
            provider = resolve_provider(private)
            self.assertEqual(provider.kind, "grok_cli")
            with self.assertRaises(InsightsError) as ctx:
                provider.analyze({"records": [{"record_uid": "s1", "text": "我想每周整理一次笔记"}]})
            self.assertEqual(ctx.exception.code, "needs_consent")
            view = public_provider_view(load_provider_config(private))
            self.assertTrue(view["remote"])
            self.assertEqual(view["host"], "grok.com")
            self.assertEqual(view["model"], "grok-4.6")
            self.assertNotIn("AGNES_API_KEY", json.dumps(view))

    def test_headless_argv_uses_empty_cwd_and_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "grok"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(binary, 0o755)
            captured: dict[str, Any] = {}

            def fake_run(argv, cwd=None, timeout=None, capture_output=None, text=None, env=None):
                captured["argv"] = argv
                captured["cwd"] = cwd
                captured["cwd_files"] = os.listdir(cwd)
                prompt = Path(argv[argv.index("--prompt-file") + 1])
                captured["prompt"] = prompt.read_text(encoding="utf-8")
                content = {
                    "observations": [
                        {
                            "dimension": "stated_plans",
                            "statement": "我想每周整理一次笔记",
                            "basis": "explicit_fact",
                            "quote": "我想每周整理一次笔记",
                            "evidence": [{"record_uid": "s1", "sender_id": "me", "quote": "我想每周整理一次笔记"}],
                            "evidence_ids": ["s1"],
                            "caveats": ["原话"],
                        }
                    ]
                }
                return type("Proc", (), {"returncode": 0, "stdout": json.dumps({"text": json.dumps(content, ensure_ascii=False)}), "stderr": ""})()

            provider = GrokCliProvider(
                {"kind": "grok_cli", "command": str(binary), "model": "grok-4.6", "engine_id": "grok-cli-grok-4.6"},
                consent_granted=True,
                run_cmd=fake_run,
            )
            result = provider.analyze(
                {
                    "kind": "self",
                    "self_ids": ["me"],
                    "records": [
                        {
                            "record_uid": "s1",
                            "conversation_id": "wxid_alice",
                            "sender_id": "me",
                            "text": "我想每周整理一次笔记，留下读后感。",
                        }
                    ],
                }
            )
            argv = " ".join(captured["argv"])
            self.assertNotIn("我想每周整理一次笔记", argv)
            self.assertIn("--prompt-file", captured["argv"])
            self.assertIn("--max-turns", captured["argv"])
            self.assertIn("dontAsk", captured["argv"])
            self.assertIn("s1", captured["prompt"])
            self.assertNotIn("pyproject.toml", captured["cwd_files"])
            self.assertEqual(result["observations"][0]["evidence_ids"], ["s1"])
            self.assertFalse(result["synthetic"])

    def test_retries_without_json_schema_when_cli_rejects_it(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "grok"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(binary, 0o755)
            calls: list[list[str]] = []

            def fake_run(argv, cwd=None, timeout=None, capture_output=None, text=None, env=None):
                calls.append(list(argv))
                if "--json-schema" in argv:
                    return type("Proc", (), {"returncode": 1, "stdout": "", "stderr": "bad schema"})()
                content = {"observations": []}
                return type("Proc", (), {"returncode": 0, "stdout": json.dumps({"structured_output": content}), "stderr": ""})()

            provider = GrokCliProvider(
                {"kind": "grok_cli", "command": str(binary), "model": "grok-4.6"},
                consent_granted=True,
                run_cmd=fake_run,
            )
            result = provider.analyze({"records": [{"record_uid": "s1", "text": "我想每周整理一次笔记"}]})
            self.assertEqual(len(calls), 2)
            self.assertIn("--json-schema", calls[0])
            self.assertNotIn("--json-schema", calls[1])
            self.assertEqual(result["observations"], [])

    def test_rejects_shell_metacharacters_in_command(self) -> None:
        provider = GrokCliProvider({"kind": "grok_cli", "command": "grok; rm -rf /"}, consent_granted=True)
        with self.assertRaises(InsightsError) as ctx:
            provider.analyze({"records": [{"record_uid": "s1", "text": "x"}]})
        self.assertEqual(ctx.exception.code, "bad_provider")
