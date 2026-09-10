from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wechat_export.authorization import LIVE_GRANT_PHRASE, live_operations_permitted
from wechat_export.jobs import JobStore
from wechat_export.workflow import WorkflowHooks, advance, start_read_job


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "jobs")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_synthetic_reaches_ready(self) -> None:
        job = start_read_job(self.store, account_id="acc_demo", adapter_id="synthetic", synthetic=True)
        self.assertEqual(job.state, "awaiting_consent")
        job = advance(self.store, job, "consent", {"confirm_preservation": True})
        self.assertEqual(job.state, "ready")
        self.assertIn("not a real-account", job.payload["phase_detail"])

    def test_real_adapter_blocks_key_capture(self) -> None:
        job = start_read_job(self.store, account_id="acc_real", adapter_id="macos-xwechat-4-arm64", synthetic=False)
        job = advance(
            self.store,
            job,
            "consent",
            {
                "confirm_preservation": True,
                "confirm_debug_copy": True,
                "confirm_key_capture": True,
                "confirm_enter_wechat": True,
            },
        )
        self.assertEqual(job.state, "blocked")
        self.assertEqual(job.payload["block_reason"], "real_key_capture_requires_fresh_confirmation")
        self.assertFalse(job.payload.get("started_key_capture"))

    def test_waiting_is_not_failure_and_cancel_works(self) -> None:
        job = start_read_job(self.store, account_id=None, adapter_id="macos-xwechat-4-arm64", synthetic=False)
        self.assertEqual(job.state, "awaiting_account")
        job = advance(self.store, job, "cancel", {})
        self.assertEqual(job.state, "cancelled")

    def test_select_account(self) -> None:
        job = start_read_job(self.store, account_id=None, adapter_id="macos-xwechat-4-arm64", synthetic=True)
        job = advance(self.store, job, "select_account", {"account_id": "acc_one"})
        self.assertEqual(job.state, "awaiting_consent")
        self.assertEqual(job.payload["account_id"], "acc_one")

    def test_continue_from_materials_reaches_ready(self) -> None:
        job = start_read_job(self.store, account_id="acc_real", adapter_id="macos-xwechat-4-arm64", synthetic=False)

        def continue_materials(store, job, body):
            job.state = "ready"
            job.payload["phase_detail"] = "continue used registered snapshot"
            job.payload["export_source_id"] = "export:demo"
            job.payload["new_user_first_read"] = False
            job.payload["started_key_capture"] = False
            store.save(job)
            return job

        hooks = WorkflowHooks(
            evaluate_adapter=lambda: {"candidate": True, "key_capture_allowed": False},
            preflight=lambda: {"ok": True},
            continue_materials=continue_materials,
            run_live=lambda store, job: job,
        )
        job = advance(
            self.store,
            job,
            "continue_from_materials",
            {"source_id": "snapshot:demo", "confirm_preservation": True},
            hooks=hooks,
        )
        self.assertEqual(job.state, "ready")
        self.assertFalse(job.payload["new_user_first_read"])
        self.assertFalse(job.payload.get("started_key_capture"))

    def test_unknown_version_cannot_receive_live_grant(self) -> None:
        job = start_read_job(self.store, account_id="acc_real", adapter_id="macos-xwechat-4-arm64", synthetic=False)
        job = advance(
            self.store,
            job,
            "consent",
            {
                "confirm_preservation": True,
                "confirm_debug_copy": True,
                "confirm_key_capture": True,
                "confirm_enter_wechat": True,
            },
        )
        self.assertEqual(job.state, "blocked")
        hooks = WorkflowHooks(
            evaluate_adapter=lambda: {"candidate": False, "reason": "unsupported_version", "key_capture_allowed": False},
            preflight=lambda: {"ok": True},
            continue_materials=lambda store, job, body: job,
            run_live=lambda store, job: job,
        )
        job = advance(self.store, job, "grant_live_operations", {"phrase": LIVE_GRANT_PHRASE}, hooks=hooks)
        self.assertEqual(job.state, "blocked")
        self.assertEqual(job.payload["block_reason"], "unsupported_version")
        self.assertFalse(job.payload.get("started_key_capture"))

    def test_live_grant_without_preflight_still_blocks_run(self) -> None:
        job = start_read_job(self.store, account_id="acc_real", adapter_id="macos-xwechat-4-arm64", synthetic=False)
        job = advance(
            self.store,
            job,
            "consent",
            {
                "confirm_preservation": True,
                "confirm_debug_copy": True,
                "confirm_key_capture": True,
                "confirm_enter_wechat": True,
            },
        )
        hooks = WorkflowHooks(
            evaluate_adapter=lambda: {"candidate": True, "reason": None, "key_capture_allowed": False},
            preflight=lambda: {"ok": False, "reason": "wechat_running"},
            continue_materials=lambda store, job, body: job,
            run_live=lambda store, job: job,
        )
        job = advance(self.store, job, "grant_live_operations", {"phrase": LIVE_GRANT_PHRASE}, hooks=hooks)
        self.assertEqual(job.state, "preflight")
        ok, reason = live_operations_permitted(
            job_id=job.job_id,
            payload=job.payload,
            adapter={"candidate": True, "key_capture_allowed": False},
            preflight={"ok": False, "reason": "wechat_running"},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "wechat_running")
        self.assertFalse(job.payload.get("started_key_capture"))

    def test_live_gates_pass_do_not_start_capture(self) -> None:
        job = start_read_job(self.store, account_id="acc_real", adapter_id="macos-xwechat-4-arm64", synthetic=False)
        captured = {"n": 0}

        def run_live(store, job):
            ok, reason = live_operations_permitted(
                job_id=job.job_id,
                payload=job.payload,
                adapter={"candidate": True, "key_capture_allowed": False},
                preflight={"ok": True},
            )
            self.assertTrue(ok, reason)
            captured["n"] += 1
            job.state = "awaiting_wechat_exit"
            job.payload["started_key_capture"] = False
            job.payload["live_gates_passed"] = True
            store.save(job)
            return job

        hooks = WorkflowHooks(
            evaluate_adapter=lambda: {"candidate": True, "key_capture_allowed": False},
            preflight=lambda: {"ok": True},
            continue_materials=lambda store, job, body: job,
            run_live=run_live,
        )
        job = advance(
            self.store,
            job,
            "consent",
            {
                "confirm_preservation": True,
                "confirm_debug_copy": True,
                "confirm_key_capture": True,
                "confirm_enter_wechat": True,
            },
        )
        job = advance(self.store, job, "grant_live_operations", {"phrase": LIVE_GRANT_PHRASE}, hooks=hooks)
        job = advance(self.store, job, "run_live", {}, hooks=hooks)
        self.assertEqual(captured["n"], 1)
        self.assertEqual(job.state, "awaiting_wechat_exit")
        self.assertTrue(job.payload.get("live_gates_passed"))
        self.assertFalse(job.payload.get("started_key_capture"))
