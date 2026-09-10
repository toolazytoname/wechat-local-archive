"""Guided live-db read workflow. Consent alone never starts a debugger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path

from wechat_export.adapters import macos_xwechat
from wechat_export.authorization import (
    LIVE_GRANT_PHRASE,
    consents_complete,
    live_operations_permitted,
    make_live_grant,
)
from wechat_export.jobs import Job, JobStore
from wechat_export.materials import resolve_materials
from wechat_export.runtime import RuntimePaths

STATES = (
    "created",
    "preflight",
    "awaiting_account",
    "awaiting_consent",
    "awaiting_wechat_exit",
    "snapshotting",
    "snapshot_verified",
    "preparing_reader",
    "awaiting_user_action",
    "acquiring_key",
    "key_verified",
    "decrypting",
    "normalizing",
    "indexing",
    "awaiting_sample_check",
    "ready",
    "cancelled",
    "failed",
    "blocked",
)

TERMINAL = {"cancelled", "failed", "blocked", "ready"}


READER_ACTIONS = {
    'helper_identity_unverified': '无法确认需要权限例外的辅助程序身份，已停止副本准备。',
    'entitlements_unreadable': '无法读取原厂签名权限声明。未把未知权限当作空集继续签名。',
    'signed_entitlements_mismatch': '副本签名后的权限与预期不一致，未启动副本。请检查本机签名审计。',
    'bundle_fingerprint_or_signature_unverified': '无法验证当前安装包的完整指纹或原厂签名，未启动读取。请检查安装包，不要关闭系统保护。',
    'environment_changed_during_prepare': '准备期间微信安装包已改变。此副本不会用于取钥；检查更新状态后创建新任务。',
    'environment_changed': '微信版本、系统或模块指纹与准备时不一致。请创建新任务，不复用旧副本。',
    'snapshot_verification_incomplete': '快照一致性未通过，未开始副本准备。请检查本机报告后重试。',
    'snapshot_changed': '快照期间源数据库有变化。未使用这份快照继续取钥。请退出相关程序后创建新任务。',
    'source_files_open': '有进程仍打开源数据库文件。请自行关闭相关程序后重试；工具不会强制结束它们。',
    'file_occupancy_inspection_failed': '无法确认数据库是否被其他进程占用。按未知状态停止，不把检测失败当作空闲。',
    'insufficient_disk_space': '本机工作区空间不足。更换最终档案目录不会减少本机快照和副本所需空间。',
    'snapshot_exists': '此任务已有快照工作目录。为避免覆盖，请创建新任务。',
    'reader_not_prepared': '本任务没有可启动的已准备副本，请先完成准备步骤。',
    'preflight_failed': '启动前检查未通过。请退出微信并重新检测环境；不会自动处理登录或系统权限。',
    'capture_already_attempted': '此任务已经尝试过取钥，禁止重复启动。检查本机报告后创建新任务。',
    'consents_incomplete': '本任务的逐项确认不完整，未启动读取。',
    'capture_verification_incomplete': '取钥结果缺少必要验证，未登记为可用密钥。',
    'captured_key_file_invalid': '密钥文件未满足私有文件校验，未登记或继续导出。',
    'original_changed_since_prepare': '原厂安装包与准备时不一致。未启动调试副本。',
    'debug_copy_changed_since_prepare': '调试副本与准备报告的指纹不一致。未启动副本。',
    'no_authenticated_candidate': '没有候选通过数据库认证，未生成可用密钥。不会尝试猜测算法或降低验证。',
    'original_reopened_or_inspection_failed': '正式微信重新打开或进程检查失败，已停止当前取钥。',
    'original_changed': '原厂安装包的校验结果发生变化。已停止，不进行自动恢复或覆盖。',
    'wechat_running': '请先自行退出正式微信。工具不会强制退出或登出账号。',
    'reader_command_failed': '副本准备或签名检查命令失败。已停止，不修改原厂 App 或系统保护。',
}


class WorkflowError(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class WorkflowHooks:
    evaluate_adapter: Callable[[], dict[str, Any]]
    preflight: Callable[[], dict[str, Any]]
    continue_materials: Callable[[JobStore, Job, dict[str, Any]], Job]
    run_live: Callable[[JobStore, Job], Job]


def default_hooks() -> WorkflowHooks:
    return WorkflowHooks(
        evaluate_adapter=lambda: macos_xwechat.evaluate(__import__("wechat_export.environment", fromlist=["collect_environment"]).collect_environment()),
        preflight=macos_xwechat.preflight,
        continue_materials=_continue_from_materials,
        run_live=_run_live,
    )


def start_read_job(store: JobStore, *, account_id: str | None, adapter_id: str, synthetic: bool,
                   destination_id: str = "default", destination_binding: dict | None = None) -> Job:
    payload = {
        "account_id": account_id,
        "destination_id": destination_id,
        "destination_binding": destination_binding,
        "adapter_id": adapter_id,
        "synthetic": synthetic,
        "confirm_preservation": False,
        "confirm_debug_copy": False,
        "confirm_key_capture": False,
        "confirm_enter_wechat": False,
        "new_user_first_read": False,
        "phase_detail": "Select an account and confirm that original WeChat files will not be modified.",
    }
    state = "awaiting_account" if not account_id else "awaiting_consent"
    return store.create("guided_read", state, payload)


def describe_state(job: Job) -> dict[str, Any]:
    state = job.state
    cancellable = state not in TERMINAL
    user_action = None
    if state == "awaiting_account":
        user_action = "Choose which local WeChat directory is yours."
    elif state == "awaiting_consent":
        user_action = "Confirm snapshot of a copy. Original app, SIP, and login stay untouched."
    elif state == "awaiting_wechat_exit":
        user_action = "Quit WeChat yourself so the live-db copy is idle. This tool will not force-quit."
    elif state == "awaiting_user_action":
        user_action = "If a debug copy shows 进入微信, click that control yourself. This tool will not click it."
    elif state == "awaiting_sample_check":
        user_action = "Open the archive and check a few messages before treating export as accepted."
    elif state == "blocked":
        user_action = job.payload.get("block_next_step") or "A required gate is not satisfied."
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "state": state,
        "cancellable": cancellable,
        "user_action": user_action,
        "synthetic": bool(job.payload.get("synthetic")),
        "error": job.error,
        "progress": job.payload.get("progress"),
        "payload_public": {
            "account_id": job.payload.get("account_id"),
            "destination_id": job.payload.get("destination_id", "default"),
            "destination_path": (job.payload.get("destination_binding") or {}).get("path"),
            "adapter_id": job.payload.get("adapter_id"),
            "phase_detail": job.payload.get("phase_detail"),
            "block_reason": job.payload.get("block_reason"),
            "consents_complete": consents_complete(job.payload),
            "live_grant": bool((job.payload.get("live_grant") or {}).get("phrase_accepted")),
            "continue_source_id": job.payload.get("continue_source_id"),
            "export_source_id": job.payload.get("export_source_id"),
            "new_user_first_read": bool(job.payload.get("new_user_first_read")),
            "live_key_acquisition_completed": bool(job.payload.get("live_key_acquisition_completed")),
            "result_id": job.payload.get("result_id"),
            "written": job.payload.get("written"),
            "total": job.payload.get("total"),
            "path": job.payload.get("public_path"),
        },
    }


def advance(
    store: JobStore,
    job: Job,
    command: str,
    body: dict[str, Any],
    *,
    hooks: WorkflowHooks | None = None,
    runtime: RuntimePaths | None = None,
) -> Job:
    hooks = hooks or default_hooks()
    if store.cancelled(job.job_id) or job.state == "cancelled":
        job.state = "cancelled"
        store.save(job)
        return job
    if command == "cancel":
        return store.request_cancel(job.job_id) or job
    if job.state in TERMINAL and job.state != "blocked":
        raise WorkflowError("job is finished", "terminal")
    if command == "select_account":
        account_id = str(body.get("account_id") or "")
        if not account_id:
            raise WorkflowError("account_id required", "account_required")
        if account_id != job.payload.get("account_id"):
            for name in ("live_grant", "prepared_snapshot", "prepared_environment", "prepared_binding", "capture_attempted", "live_key_acquisition_completed", "key_file_name", "export_source_id", "continue_source_id"):
                job.payload.pop(name, None)
            for name in ("confirm_preservation", "confirm_debug_copy", "confirm_key_capture", "confirm_enter_wechat"):
                job.payload[name] = False
        job.payload["account_id"] = account_id
        job.state = "awaiting_consent"
        job.payload["phase_detail"] = "Account recorded. Confirm snapshot boundaries before any copy starts."
        store.save(job)
        return job
    if command == "consent":
        job.payload["confirm_preservation"] = body.get("confirm_preservation") is True
        job.payload["confirm_debug_copy"] = body.get("confirm_debug_copy") is True
        job.payload["confirm_key_capture"] = body.get("confirm_key_capture") is True
        job.payload["confirm_enter_wechat"] = body.get("confirm_enter_wechat") is True
        if not job.payload["confirm_preservation"]:
            job.state = "awaiting_consent"
            job.payload["phase_detail"] = "Snapshot will not start until preservation is confirmed."
            store.save(job)
            return job
        if job.payload.get("synthetic"):
            return _run_synthetic(store, job)
        return _block_real_capture(store, job)
    if command == "grant_live_operations":
        if job.payload.get("synthetic"):
            raise WorkflowError("synthetic jobs cannot take a live grant", "synthetic_job")
        if not consents_complete(job.payload):
            return _block(store, job, "consents_incomplete", "All four confirmations are required before a live grant.")
        adapter = hooks.evaluate_adapter()
        if not adapter.get("candidate"):
            return _block(
                store,
                job,
                adapter.get("reason") or "unsupported_environment",
                "This environment is not an adapter candidate.",
            )
        try:
            job.payload["live_grant"] = make_live_grant(job.job_id, str(body.get("phrase") or ""))
        except ValueError:
            return _block(store, job, "live_grant_phrase_mismatch", f"Type {LIVE_GRANT_PHRASE} to bind a live grant to this job only.")
        job.payload["phase_detail"] = "Live grant is bound to this job. Preflight still has to pass."
        job.payload["block_reason"] = None
        job.error = None
        job.state = "preflight"
        store.save(job)
        return job
    if command == "confirm_sample":
        if (job.state != "awaiting_sample_check" or
                body.get("confirm_sample") is not True or
                not job.payload.get("export_source_id")):
            raise WorkflowError("Open and check the selected archive first", "sample_confirmation_required")
        job.state = "ready"
        job.payload["phase_detail"] = "Existing archive validated and sample accepted by user."
        store.save(job)
        return job
    if command in {"prepare_reader", "acquire_key"}:
        return _live_step(store, job, command, body, runtime)
    if command == "run_live":
        return hooks.run_live(store, job)
    if command == "continue_from_materials":
        if job.payload.get("confirm_preservation") is not True and body.get("confirm_preservation") is not True:
            job.state = "awaiting_consent"
            job.payload["phase_detail"] = "Confirm preservation before continuing from a snapshot."
            store.save(job)
            return job
        job.payload["confirm_preservation"] = True
        job.payload["continue_source_id"] = str(body.get("source_id") or "")
        job.payload["runtime_marker"] = bool(runtime)
        if runtime is not None:
            job.payload["_runtime_data_root"] = str(runtime.data_root)
        return hooks.continue_materials(store, job, body)
    raise WorkflowError("unknown command", "unknown_command")


def _block(store: JobStore, job: Job, code: str, detail: str) -> Job:
    job.state = "blocked"
    job.payload["block_reason"] = code
    job.payload["block_next_step"] = detail
    job.payload["phase_detail"] = detail
    job.error = {"code": code, "error": detail}
    job.payload["started_key_capture"] = False
    store.save(job)
    return job


def _block_real_capture(store: JobStore, job: Job) -> Job:
    """Consent without a this-job live grant still cannot start capture."""
    return _block(
        store,
        job,
        "real_key_capture_requires_fresh_confirmation",
        "Confirmations were recorded, but live WeChat steps stay blocked until this job receives "
        f"a live grant ({LIVE_GRANT_PHRASE}) and preflight passes. Existing keys are not a first-read.",
    )


def _run_live(store: JobStore, job: Job) -> Job:
    adapter = macos_xwechat.evaluate(
        __import__("wechat_export.environment", fromlist=["collect_environment"]).collect_environment()
    )
    pre = macos_xwechat.preflight()
    ok, reason = live_operations_permitted(
        job_id=job.job_id,
        payload=job.payload,
        adapter=adapter,
        preflight=pre,
    )
    if not ok:
        return _block(store, job, reason or "live_not_permitted", "Live WeChat steps are not permitted on this job.")
    job.state = "awaiting_wechat_exit"
    job.payload["started_key_capture"] = False
    job.payload["live_gates_passed"] = True
    job.payload["new_user_first_read"] = False
    job.payload["phase_detail"] = (
        "Live gates passed for this job. Debug copy and LLDB are not auto-started; "
        "a first-read rehearsal still needs its own confirmation."
    )
    job.error = None
    store.save(job)
    return job


def _continue_from_materials(store: JobStore, job: Job, body: dict[str, Any]) -> Job:
    from wechat_export.runtime import resolve_runtime

    source_id = str(body.get("source_id") or job.payload.get("continue_source_id") or "")
    if not source_id:
        raise WorkflowError("source_id required", "source_required")
    runtime = resolve_runtime(job.payload.get("_runtime_data_root"))
    materials = resolve_materials(source_id, runtime)
    from wechat_export.output_locations import OutputLocations, check_identity
    locations = OutputLocations(runtime)
    destination_id = job.payload.get('destination_id', 'default')
    try:
        output_parent = locations.resolve(destination_id)
        saved_destination = job.payload.get('destination_binding')
        if saved_destination:
            check_identity(saved_destination)
            if str(output_parent) != saved_destination['path']:
                raise ValueError('destination_changed')
        else:
            saved_destination = locations.binding(destination_id)
            job.payload['destination_binding'] = saved_destination
        def validate_destination():
            if locations.binding(destination_id) != saved_destination:
                raise ValueError('destination_changed')
            check_identity(saved_destination)
    except (ValueError, OSError):
        return _block(store, job, 'destination_unavailable', 'Output location is offline or replaced. Reconnect it, or select a new location and start a new task.')
    # Reuse is not decryption: validate provenance and every canonical row before
    # offering a registered archive. Missing material must never turn into ready.
    import json
    from wechat_export.archive_index import build_index

    binding_path = materials["root"] / "account-binding.json"
    if binding_path.is_file():
        binding = json.loads(binding_path.read_text())
        if binding.get("account_id") != job.payload.get("account_id"):
            return _block(store, job, "snapshot_account_mismatch", "Snapshot is bound to another selected account.")
    elif body.get("confirm_snapshot_account") is not True:
        return _block(store, job, "snapshot_account_confirmation_required", "Explicitly confirm the selected snapshot belongs to this selected account.")
    job.payload.pop("export_source_id", None)
    job.payload["new_user_first_read"] = False
    job.state = "preflight"
    store.save(job)
    for child in sorted(output_parent.iterdir()) if output_parent.is_dir() else []:
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            manifest = json.loads((child / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("source_snapshot_id") != materials["run_id"]:
                continue
            if manifest.get("source_kind") != "live-db" or manifest.get("backup2_coverage") != "unverified":
                continue
            messages = child / "all" / "messages.jsonl"
            if not messages.resolve().is_relative_to(child.resolve()):
                raise ValueError("archive path escape")
            count = 0
            with messages.open(encoding="utf-8") as stream:
                for line in stream:
                    if store.cancelled(job.job_id):
                        return store.request_cancel(job.job_id) or job
                    row = json.loads(line)
                    if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k] for k in ("record_uid", "conversation_id")):
                        raise ValueError("canonical identifiers missing")
                    if (row.get("source_kind") != "live-db" or
                            row.get("source_snapshot_id") != materials["run_id"]):
                        raise ValueError("record provenance mismatch")
                    count += 1
            if count == 0 or count != manifest.get("record_count"):
                raise ValueError("record count mismatch")
            job.state = "indexing"
            job.payload["phase_detail"] = "Validating an existing export; no new decryption or key capture."
            store.save(job)
            build_index(child)
        except (OSError, ValueError, TypeError):
            continue
        if store.cancelled(job.job_id):
            return store.request_cancel(job.job_id) or job
        job.payload["export_source_id"] = locations.source_id(destination_id, child.name)
        job.payload["continue_source_id"] = source_id
        job.payload["phase_detail"] = "Existing export validated. Open it and explicitly confirm sample review."
        job.payload["block_reason"] = None
        job.error = None
        job.state = "awaiting_sample_check"
        store.save(job)
        return job
    # No previous export: execute the genuine authenticated pipeline.
    from wechat_export.read_pipeline import process_snapshot, PipelineError, PipelineCancelled
    from wechat_export.config import AppConfig
    from wechat_export.discovery import resolve_account_dir
    from wechat_export.fsutil import write_json

    key_name = job.payload.get("key_file_name")
    reference = materials["root"] / "key-reference.json"
    if not key_name and reference.is_file():
        saved = json.loads(reference.read_text())
        if saved.get("account_id") == job.payload.get("account_id"):
            key_name = saved.get("key_file_name")
    if key_name:
        import re
        if not isinstance(key_name, str) or not re.fullmatch(r"passphrase-[0-9a-f-]{36}\.raw", key_name):
            return _block(store, job, "invalid_key_reference", "Snapshot key reference is invalid.")
    key = runtime.private_root / (key_name or "passphrase.raw")
    if not key.is_file():
        return _block(store, job, "validated_export_missing", "No matching export or local key. Use first-read key acquisition.")
    try:
        account = resolve_account_dir(str(job.payload.get("account_id") or ""))
        binding = materials["root"] / "account-binding.json"
        if binding.is_file():
            if json.loads(binding.read_text()).get("account_id") != job.payload["account_id"]:
                return _block(store, job, "snapshot_account_mismatch", "Select the account bound to this snapshot.")
        elif body.get("confirm_snapshot_account") is not True:
            return _block(store, job, "snapshot_account_confirmation_required", "Confirm this selected snapshot belongs to the selected account.")
        cfg = AppConfig(
            project_root=runtime.data_root, data_root=runtime.data_root,
            xwechat_root=account.parent, account_backup_root=Path(str(job.payload["account_id"])),
            backup_set="", source_backup2=runtime.data_root / "unused-backup",
            live_account_root=account, live_db_root=materials["live_db"],
            display_timezone=str(body.get("display_timezone") or "UTC"), keys_path=None,
            config_path=runtime.private_root / "config.json", account=str(job.payload["account_id"]),
        )
        def progress(state, done, total):
            job.state = state
            job.payload.update(written=done, total=total, phase_detail=state)
            store.save(job)
        out = process_snapshot(snapshot=materials["live_db"], passphrase_file=key,
                               cfg=cfg, run_id="read-" + job.job_id,
                               snapshot_id=materials["run_id"], progress=progress,
                               cancelled=lambda: store.cancelled(job.job_id),
                               output_parent=output_parent, validate_destination=validate_destination)
        job.payload["export_source_id"] = locations.source_id(destination_id, out.name)
        job.payload["continue_source_id"] = source_id
        job.payload["phase_detail"] = "Snapshot decrypted, exported and indexed. Verify sample before accepting."
        job.state = "awaiting_sample_check"
        job.error = None
        store.save(job)
        return job
    except PipelineCancelled:
        return store.request_cancel(job.job_id) or job
    except (PipelineError, OSError, ValueError):
        return _block(store, job, "snapshot_processing_failed", "Snapshot processing failed; originals unchanged. Check local diagnostic state; start a new job to retry.")



def _run_synthetic(store: JobStore, job: Job) -> Job:
    sequence = [
        ("preflight", "Synthetic environment accepted."),
        ("snapshotting", "Synthetic snapshot of fixture files."),
        ("snapshot_verified", "Synthetic snapshot hashes recorded."),
        ("awaiting_user_action", "Synthetic user action is auto-complete in tests only."),
        ("key_verified", "Synthetic HMAC fixture verified."),
        ("decrypting", "Synthetic decrypt skipped — fixture already plaintext."),
        ("normalizing", "Synthetic records normalized."),
        ("indexing", "Synthetic index built."),
        ("awaiting_sample_check", "Inspect the four demo messages."),
        ("ready", "Synthetic guided read is ready. This is not a real-account verification."),
    ]
    for state, detail in sequence:
        if store.cancelled(job.job_id):
            job.state = "cancelled"
            store.save(job)
            return job
        job.state = state
        job.payload["phase_detail"] = detail
        job.payload["new_user_first_read"] = False
        store.save(job)
    return job


def _live_step(store, job, command, body, runtime):
    import fcntl
    import json
    from wechat_export.authorization import live_operations_permitted
    from wechat_export.discovery import resolve_account_dir
    from wechat_export.environment import collect_environment
    from wechat_export.fsutil import write_json, ensure_dir
    from wechat_export.livedb_snapshot import snapshot_strict
    from wechat_export.live_reader import prepare_copy, acquire_key, ReaderError
    if runtime is None or job.payload.get("synthetic"):
        raise WorkflowError("Real runtime required", "runtime_required")
    if body.get("confirm_live_step") is not True or body.get("confirm_library_exception") is not True:
        raise WorkflowError("This stage requires explicit confirmation", "live_step_confirmation_required")
    ensure_dir(runtime.data_root / "work")
    with (runtime.jobs_root / "live-reader.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _block(store, job, "reader_busy", "Another reader owns the live operation lock.")
        try:
            from wechat_export.output_locations import OutputLocations, check_identity
            locations = OutputLocations(runtime)
            try:
                destination = locations.binding(job.payload.get('destination_id', 'default'))
                saved = job.payload.get('destination_binding')
                if saved and (saved != destination or check_identity(saved) != Path(destination['path'])):
                    raise ValueError('destination_changed')
                job.payload['destination_binding'] = destination
            except (OSError, ValueError):
                return _block(store, job, 'destination_unavailable', 'Output location is offline or replaced. No reader operation started. Reconnect it or create a new task with another location.')
            from wechat_export.compatibility_registry import environment_binding
            environment = collect_environment()
            adapter = macos_xwechat.evaluate(environment)
            binding = environment_binding(environment)
            pre = macos_xwechat.preflight()
            if command == "prepare_reader":
                ok, reason = live_operations_permitted(job_id=job.job_id, payload=job.payload, adapter=adapter, preflight=pre)
                if not ok:
                    return _block(store, job, reason, "Quit WeChat, check environment and authorize this job again.")
                if not binding or (environment.get('wechat_codesign') or {}).get('verified') is not True:
                    raise ReaderError('bundle_fingerprint_or_signature_unverified')
                account = resolve_account_dir(job.payload["account_id"])
                run_id = "snapshot-" + job.job_id
                work = runtime.data_root / "work" / run_id
                if work.exists():
                    raise ReaderError("snapshot_exists")
                import shutil
                from wechat_export.storage_plan import tree_bytes
                source_bytes = tree_bytes(account / 'db_storage')
                app_bytes = (environment.get('wechat_fingerprint') or {}).get('total_bytes')
                if type(app_bytes) is not int or app_bytes <= 0:
                    raise ReaderError('bundle_fingerprint_or_signature_unverified')
                if shutil.disk_usage(runtime.data_root).free < source_bytes * 4 + app_bytes * 2:
                    raise ReaderError("insufficient_disk_space")
                job.payload["live_grant"]["consumed"] = True
                job.payload["_runtime_data_root"] = str(runtime.data_root)
                job.state = "snapshotting"
                store.save(job)
                snapshot_result = snapshot_strict(account / "db_storage", work / "live-db", lambda: store.cancelled(job.job_id))
                if (snapshot_result.get('consistency') != 'idle_hash_verified' or
                        snapshot_result.get('hot_copies') != 0 or snapshot_result.get('db_count', 0) < 2):
                    raise ReaderError('snapshot_verification_incomplete')
                if store.cancelled(job.job_id):
                    raise ReaderError('cancelled')
                job.state = 'snapshot_verified'
                store.save(job)
                write_json(work / "account-binding.json", {"account_id": job.payload["account_id"]})
                job.state = "preparing_reader"
                store.save(job)
                prepare_copy(app=Path("/Applications/WeChat.app"), account=account, work=work,
                             cancelled=lambda: store.cancelled(job.job_id))
                from wechat_export.build_fingerprint import SCHEMA as FINGERPRINT_SCHEMA
                prepared_report = json.loads((work / 'reader-prepared.json').read_text())
                if (prepared_report.get('fingerprint_schema') != FINGERPRINT_SCHEMA or
                        prepared_report.get('original_bundle_sha256') != binding['bundle_sha256']):
                    raise ReaderError('environment_changed_during_prepare')
                job.payload['prepared_binding'] = binding
                job.payload["prepared_snapshot"] = run_id
                job.payload["prepared_environment"] = [adapter.get("wechat_version"), adapter.get("wechat_build")]
                job.payload["continue_source_id"] = "snapshot:" + run_id
                job.state = "awaiting_user_action"
                job.payload["phase_detail"] = "Copy prepared, not launched. Confirm launch; then manually click 进入微信 if shown. Never scan QR or log out."
                store.save(job)
                return job
            run_id = job.payload.get("prepared_snapshot")
            if job.state != "awaiting_user_action" or not run_id:
                raise ReaderError("reader_not_prepared")
            if not pre.get("ok") or not adapter.get("candidate"):
                raise ReaderError("preflight_failed")
            if (not binding or job.payload.get('prepared_binding') != binding or
                    (environment.get('wechat_codesign') or {}).get('verified') is not True):
                raise ReaderError("environment_changed")
            if job.payload.get('capture_attempted'):
                raise ReaderError('capture_already_attempted')
            if not consents_complete(job.payload):
                raise ReaderError('consents_incomplete')
            work = runtime.data_root / "work" / run_id
            job.payload["capture_attempted"] = True
            job.state = "acquiring_key"
            job.payload["phase_detail"] = "Debugger running for at most 120 seconds. Click 进入微信 yourself; cancel if QR/login required."
            store.save(job)
            key_name = "passphrase-" + job.job_id + ".raw"
            result = macos_xwechat.execute_key_capture(job=job, app=Path("/Applications/WeChat.app"), copy=work / "WeChat-debug.app",
                                 snapshot=work / "live-db", output=runtime.private_root / key_name,
                                 cancelled=lambda: store.cancelled(job.job_id))
            if (not isinstance(result, dict) or result.get('hmac_verified') is not True or
                    not isinstance(result.get('databases_verified'), int) or
                    isinstance(result.get('databases_verified'), bool) or result['databases_verified'] < 2 or
                    result.get('breakpoint_hit') is not True):
                raise ReaderError('capture_verification_incomplete')
            if store.cancelled(job.job_id):
                raise ReaderError('cancelled')
            import os, stat
            key_file = runtime.private_root / key_name
            try:
                key_stat = key_file.lstat()
            except OSError:
                raise ReaderError('captured_key_file_invalid') from None
            if (not stat.S_ISREG(key_stat.st_mode) or key_stat.st_mode & 0o077 or
                    key_stat.st_uid != os.getuid() or key_stat.st_nlink != 1 or key_stat.st_size != 32):
                raise ReaderError('captured_key_file_invalid')
            write_json(work / "capture-result.json", result)
            write_json(work / "key-reference.json", {"account_id": job.payload["account_id"], "key_file_name": key_name})
            job.payload["key_file_name"] = key_name
            job.payload["live_key_acquisition_completed"] = True
            job.payload["new_user_first_read"] = False
            job.state = "key_verified"
            store.save(job)
            return _continue_from_materials(store, job, {"source_id": "snapshot:" + run_id})
        except Exception as exc:
            code = str(exc) if str(exc) in READER_ACTIONS else 'reader_operation_failed'
            write_json(runtime.jobs_root / (job.job_id + "-diagnostic.json"),
                       {"stage": command, "error_type": type(exc).__name__, "code": code})
            if store.cancelled(job.job_id):
                return store.request_cancel(job.job_id) or job
            return _block(store, job, code, READER_ACTIONS.get(code,
                          '读取步骤已停止。请检查本机诊断并创建新任务；不会自动恢复覆盖或降低系统保护。'))
