"""HTTP handlers for insights, learning and profiles. Archive-bound, CSRF on writes."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from wechat_export.insights.identity import (
    ambiguous_display_names,
    audit_self,
    list_people,
    list_roles,
    load_identity,
    excluded_conversation_ids,
    read_later_conversation_ids,
    save_identity,
    set_role,
    upsert_person,
)
from wechat_export.insights.recovery import list_recovery, recover_missing
from wechat_export.insights.exporter import export_profile
from wechat_export.insights.tasks import start_learning_task, start_profile_task, bound_task, public_task
from wechat_export.insights.consent import consume_ticket, issue_ticket, require_json_true
from wechat_export.insights.profile_pipeline import (
    add_correction,
    collect_profile_records,
    coverage,
    excluded_evidence,
    get_run,
    list_runs,
    run_profile,
)
from wechat_export.insights.providers import (
    load_provider_config,
    public_engine_list,
    public_provider_view,
    resolve_provider,
    update_engine_catalog,
)
from wechat_export.insights.store import InsightsError, open_store, utc_now
from wechat_export.learning.exporter import export_learning_pack
from wechat_export.learning.fetcher import fetch_not_enabled
from wechat_export.learning.importer import get_item, import_conversation, list_items, paste_body, patch_item
from wechat_export.learning.notes import add_review_card, save_note
from wechat_export.learning.semantic import prepare_learning
from wechat_export.learning.summaries import summarize_item
from wechat_export.snapshot import make_snapshot_id


def _store(handler):
    binding = handler._require_archive()
    return open_store(handler.ctx.runtime.data_root, binding.revision, archive_root=binding.root), binding


def handle_get(handler, path: str, q: dict[str, list[str]]) -> bool:
    if path == "/api/insights/recovery":
        store,binding=_store(handler)
        handler._send_json({'candidates':list_recovery(store,handler.ctx.runtime.data_root)})
        return True
    if path == "/api/insights/tasks":
        binding=handler._require_archive()
        handler.ctx.job_store.recover_interrupted()
        jobs=[]
        for candidate in handler.ctx.job_store.root.glob("*.json"):
            try:
                job=bound_task(handler.ctx.job_store,candidate.stem,binding)
                jobs.append(public_task(job))
            except InsightsError:continue
        handler._send_json({"tasks":sorted(jobs,key=lambda j:j['created_at'],reverse=True)[:30]})
        return True
    if path.startswith("/api/insights/tasks/"):
        binding=handler._require_archive()
        handler._send_json(public_task(bound_task(handler.ctx.job_store,path.rsplit('/',1)[-1],binding)))
        return True
    if path == "/api/insights/engines":
        handler._require_archive()
        handler._send_json(public_engine_list(handler.ctx.runtime.private_root))
        return True
    if path == "/api/insights/context":
        store, binding = _store(handler)
        conn = handler._db(binding)
        try:
            audit = audit_self(conn)
            identity = load_identity(store)
            handler._send_json(
                {
                    "source_revision": binding.revision,
                    "audit": audit,
                    "identity": identity,
                    "people": list_people(store),
                    "conversation_roles": list_roles(store),
                    "ambiguous_names": ambiguous_display_names(conn),
                    "needs_identity_confirmation": audit["verification_state"] != "consistent" and not (
                        identity and identity["verification_state"] == "user_confirmed"
                    ),
                    "migration": {
                        "status": store.get_meta("migration_status"),
                        "legacy_conflict": store.get_meta("legacy_conflict"),
                        "legacy_namespace": store.get_meta("legacy_namespace"),
                        "migrated_from": store.get_meta("migrated_from"),
                    },
                }
            )
        finally:
            conn.close()
        return True
    if path == "/api/learning/items":
        store, _binding = _store(handler)
        items = list_items(
            store,
            query=(q.get("q") or [""])[0],
            reading=(q.get("reading") or [""])[0],
            content=(q.get("content") or [""])[0],
            topic=(q.get("topic") or [""])[0],
        )
        handler._send_json({"items": items, "count": len(items)})
        return True
    if path.startswith("/api/learning/items/") and path.count("/") == 4:
        store, binding = _store(handler)
        item_id = path.rsplit("/", 1)[-1]
        handler._send_json(get_item(store, item_id, archive_root=binding.root))
        return True
    if path == "/api/profiles/runs":
        store, _binding = _store(handler)
        kind = (q.get("kind") or [""])[0]
        handler._send_json({"runs": list_runs(store, kind=kind or None)})
        return True
    if path.startswith("/api/profiles/runs/") and path.count("/") == 4:
        store, _binding = _store(handler)
        handler._send_json(get_run(store, path.rsplit("/", 1)[-1]))
        return True
    if path.startswith("/api/insights/evidence/"):
        store, _binding = _store(handler)
        eid = path.rsplit("/", 1)[-1]
        row = store.conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (eid,)).fetchone()
        if row is None:
            raise InsightsError("not found", "not_found")
        handler._send_json(dict(row))
        return True
    return False


def handle_write(handler, method: str, path: str, body: dict[str, Any]) -> bool:
    if path == "/api/insights/engines/test":
        handler._require_archive()
        require_json_true(body.get("confirm_test"), "confirm_test")
        # Fixed synthetic material only; never open a chat DB or consume an upload ticket.
        provider = resolve_provider(handler.ctx.runtime.private_root, allow_synthetic=False,
                                    consent={"approve_remote": True}, engine=str(body.get("engine") or ""))
        if getattr(provider, "kind", "") not in {"remote", "grok_cli"}:
            raise InsightsError("请选择一个 AI 服务。", "needs_engine")
        try:
            result = provider.analyze({"kind":"self", "self_ids":["synthetic_me"], "records":[{
                "record_uid":"connection_test", "conversation_id":"synthetic", "sender_id":"synthetic_me",
                "timestamp_utc":"2026-01-01T00:00:00Z", "text":"我计划每周整理一次读书笔记。"}]})
            if not isinstance(result.get("observations"), list):
                raise InsightsError("Invalid model response", "remote_invalid")
            handler._send_json({"ok":True,"synthetic":True})
        except InsightsError as exc:
            from wechat_export.insights.tasks import task_error_message
            handler._send_json({"ok":False,"code":exc.code,"message":task_error_message(exc.code),"synthetic":True})
        return True
    if path == "/api/insights/recovery":
        require_json_true(body.get('confirm_recovery'))
        store,binding=_store(handler)
        conn=handler._db(binding)
        try:current=audit_self(conn)
        finally:conn.close()
        identity=load_identity(store)
        if not identity or not set(identity['self_sender_ids']).issubset(set(current['self_sender_ids'])):
            raise InsightsError('当前身份与档案不匹配。','wrong_account')
        handler._send_json(recover_missing(store,handler.ctx.runtime.data_root,
            str(body.get('candidate_id') or ''),str(body.get('fingerprint') or ''),identity['self_sender_ids']))
        return True
    if path.startswith("/api/insights/tasks/") and path.endswith("/restart"):
        binding=handler._require_archive()
        job=bound_task(handler.ctx.job_store,path.split('/')[4],binding)
        if job.payload.get('remote') or job.state not in {'failed','blocked','cancelled'}:
            raise InsightsError('该任务需要重新预览与批准，不能自动重发。','needs_consent')
        provider=resolve_provider(handler.ctx.runtime.private_root,allow_synthetic=False,prefer_local=True)
        request={'kind':job.payload['profile_kind'],'scope':job.payload.get('scope') or {},'subject_person_id':job.payload.get('subject_person_id')}
        handler._send_json(start_profile_task(handler.ctx.job_store,handler.ctx.runtime.data_root,binding,request=request,provider=provider))
        return True
    if path.startswith("/api/insights/tasks/") and path.endswith("/cancel"):
        binding=handler._require_archive()
        job=bound_task(handler.ctx.job_store,path.split('/')[4],binding)
        if job.state not in {'ready','failed','cancelled','blocked'}:
            job=handler.ctx.job_store.request_cancel(job.job_id)
        handler._send_json(public_task(job))
        return True
    if path == "/api/insights/reveal":
        handler._require_archive()
        name=str(body.get('delivery_id') or '')
        if not re.fullmatch(r'(learning|profile)_[A-Za-z0-9_-]+',name):
            raise InsightsError('Unknown delivery','not_found')
        root=(handler.ctx.runtime.data_root/'deliveries').resolve()
        dest=root/name
        if dest.is_symlink() or not dest.is_dir() or dest.resolve().parent!=root:
            raise InsightsError('Unknown delivery','not_found')
        open_html = bool(body.get('open_html'))
        if open_html:
            html_path = dest / '开始阅读.html'
            if not html_path.is_file() or html_path.is_symlink() or html_path.resolve().parent != dest.resolve():
                raise InsightsError('Offline page missing','not_found')
            subprocess.run(['open', str(html_path)], check=False)
        else:
            subprocess.run(['open','-R',str(dest)],check=False)
        handler._send_json({'ok':True,'opened':'html' if open_html else 'folder'})
        return True
    if path == "/api/insights/context":
        store, binding = _store(handler)
        conn = handler._db(binding)
        try:
            if body.get("confirm_self_sender_id"):
                audit = audit_self(conn)
                save_identity(store, audit, confirmed_ids=[str(body["confirm_self_sender_id"])])
            elif body.get("accept_consistent_self"):
                save_identity(store, audit_self(conn))
            if body.get("person"):
                upsert_person(store, body["person"])
            if body.get("conversation_id") and body.get("purpose"):
                known = {r["conversation_id"] for r in conn.execute("SELECT conversation_id FROM conversations")}
                if body["conversation_id"] not in known:
                    raise InsightsError("conversation not in this archive", "unknown_conversation")
                set_role(store, str(body["conversation_id"]), str(body["purpose"]))
            handler._send_json(
                {
                    "identity": load_identity(store),
                    "people": list_people(store),
                    "conversation_roles": list_roles(store),
                }
            )
        finally:
            conn.close()
        return True
    if path == "/api/learning/imports":
        store, binding = _store(handler)
        conversation_id = str(body.get("conversation_id") or "")
        if not conversation_id:
            roles = read_later_conversation_ids(store)
            if len(roles) != 1:
                raise InsightsError("select a collection conversation", "collection_required")
            conversation_id = roles[0]
        conn = handler._db(binding)
        try:
            result = import_conversation(store, conn, conversation_id, archive_root=binding.root)
        finally:
            conn.close()
        handler._send_json(result)
        return True
    if path.startswith("/api/learning/items/") and path.endswith("/content"):
        store, _binding = _store(handler)
        item_id = path.split("/")[4]
        handler._send_json(paste_body(store, item_id, str(body.get("text") or "")))
        return True
    if path.startswith("/api/learning/items/") and path.endswith("/fetch"):
        fetch_not_enabled()
        return True
    if path.startswith("/api/learning/items/") and path.endswith("/notes"):
        store, _binding = _store(handler)
        item_id = path.split("/")[4]
        handler._send_json(
            save_note(
                store,
                item_id,
                str(body.get("text") or ""),
                quote_span=body.get("quote_span"),
                content_id=body.get("content_id"),
                note_id=body.get("note_id"),
                revision=body.get("revision"),
            )
        )
        return True
    if path.startswith("/api/learning/items/") and path.endswith("/review-cards"):
        store, _binding = _store(handler)
        item_id = path.split("/")[4]
        handler._send_json(add_review_card(store, item_id, str(body.get("question") or ""), str(body.get("answer") or "")))
        return True
    if path.startswith("/api/learning/items/") and path.endswith(("/summary-preview","/summary-consent","/summaries")):
        store,binding=_store(handler)
        item_id=path.split('/')[4]
        if path.endswith('/summaries') and body.get('mode')!='remote':
            handler._send_json(summarize_item(store,item_id,engine_id='local_excerpt'));return True
        prepared=prepare_learning(store,item_id)
        engine=str(body.get('engine') or '')
        if path.endswith('/summary-preview'):
            view=public_engine_list(handler.ctx.runtime.private_root)
            handler._send_json({'chars':prepared['chars'],'request_count':prepared['request_count'],
                'content_id':prepared['content_id'],'engines':view['engines'],'default_engine':view['default']})
            return True
        require_json_true(body.get('approve_remote'))
        provider=resolve_provider(handler.ctx.runtime.private_root,allow_synthetic=False,consent={'approve_remote':True},engine=engine)
        if not hasattr(provider,'summarize'):raise InsightsError('请选择支持文章整理的云端引擎。','needs_engine')
        identity=load_identity(store)
        params={'kind':'learning','engine_id':provider.engine_id,
            'endpoint':getattr(provider,'base_url',None) or provider.public_view().get('host'),
            'model':getattr(provider,'model',None),'scope':{'item_id':item_id,'content_id':prepared['content_id'],'body_hash':prepared['body_hash']},
            'record_uids':[p['paragraph_id'] for p in prepared['paragraphs']],
            'source_revision':binding.revision,'identity_revision':identity['revision'] if identity else 0}
        if path.endswith('/summary-consent'):
            handler._send_json(issue_ticket(store,**params));return True
        consume_ticket(store,str(body.get('consent_ticket') or ''),**params)
        handler._send_json(start_learning_task(handler.ctx.job_store,handler.ctx.runtime.data_root,binding,
            item_id=item_id,provider=provider,prepared=prepared));return True
    if path.startswith("/api/learning/items/") and method == "PATCH":
        store, _binding = _store(handler)
        item_id = path.rsplit("/", 1)[-1]
        handler._send_json(patch_item(store, item_id, body))
        return True
    if path == "/api/profiles/preview":
        store, binding = _store(handler)
        conn = handler._db(binding)
        try:
            identity = load_identity(store)
            if identity is None:
                audit = audit_self(conn)
                handler._send_json({"needs_identity": True, "audit": audit, "engine": resolve_provider(handler.ctx.runtime.private_root).engine_id})
                return True
            scope = body.get("scope") or {}
            if body.get("since") and "since" not in scope:
                scope["since"] = body.get("since")
            if body.get("until") and "until" not in scope:
                scope["until"] = body.get("until")
            skip_uids, _skip_statements = excluded_evidence(store)
            stats = coverage(
                conn,
                self_ids=identity["self_sender_ids"],
                excluded=excluded_conversation_ids(store),
                scope=scope,
            )
            listing = public_engine_list(handler.ctx.runtime.private_root)
            view = public_provider_view(load_provider_config(handler.ctx.runtime.private_root))
            sample = collect_profile_records(
                conn,
                kind=str(body.get("kind") or "self"),
                self_ids=identity["self_sender_ids"],
                excluded=excluded_conversation_ids(store),
                scope=scope,
                skip_uids=skip_uids,
            )
            handler._send_json(
                {
                    "coverage": stats,
                    "engine_id": view.get("engine_id") or "local_explicit",
                    "engine_kind": view.get("id") or view.get("kind") or "local_explicit",
                    "available": bool(view.get("available")),
                    "remote": bool(view.get("remote")),
                    "default_engine": listing["default"],
                    "engines": listing["engines"],
                    "consent": {
                        "required": bool(view.get("remote")),
                        "endpoint": view.get("base_url") or view.get("host"),
                        "host": view.get("host"),
                        "model": view.get("model"),
                        "fields": view.get("fields") or [],
                        "attachments": False,
                        "estimated_chars": sample["estimated_chars"],
                        "upload_count": len(sample["records"]),
                        "scope_hash": None,
                        "record_uids": sample.get("record_uids") or [row["record_uid"] for row in sample["records"]],
                    },
                    "note": "收藏学习会话默认不作为人物特征证据。本机原话不上传；Grok CLI 和 BYOK 只发送范围内的文字摘录，不发送附件和密钥。",
                }
            )
        finally:
            conn.close()
        return True
    if path == "/api/insights/engines":
        handler._require_archive()
        handler._send_json(update_engine_catalog(handler.ctx.runtime.private_root, body))
        return True
    if path == "/api/profiles/consent":
        store, binding = _store(handler)
        require_json_true(body.get("approve_remote"))
        engine = str(body.get("engine") or "")
        if engine in {"", "local", "local_explicit"}:
            raise InsightsError("local extraction does not need cloud consent", "needs_engine")
        provider = resolve_provider(
            handler.ctx.runtime.private_root,
            allow_synthetic=False,
            consent={"approve_remote": True},
            engine=engine,
        )
        identity = load_identity(store)
        if identity is None:
            raise InsightsError("confirm who you are before generating a profile", "identity_unresolved")
        conn = handler._db(binding)
        try:
            skip_uids, _skip_statements = excluded_evidence(store)
            sample = collect_profile_records(
                conn,
                kind=str(body.get("kind") or "self"),
                self_ids=identity["self_sender_ids"],
                excluded=excluded_conversation_ids(store),
                scope=body.get("scope") or {},
                skip_uids=skip_uids,
            )
        finally:
            conn.close()
        ticket = issue_ticket(
            store,
            kind=str(body.get("kind") or "self"),
            engine_id=provider.engine_id,
            endpoint=getattr(provider, "base_url", None) or getattr(provider, "public_view", lambda: {})().get("host"),
            model=getattr(provider, "model", None),
            scope=body.get("scope") or {},
            record_uids=[row["record_uid"] for row in sample["records"]],
            source_revision=binding.revision,
            identity_revision=identity["revision"],
        )
        handler._send_json(ticket)
        return True
    if path == "/api/profiles/runs":
        store, binding = _store(handler)
        private_root = handler.ctx.runtime.private_root
        engine = str(body.get("engine") or "").strip()
        if engine in {"", "local", "local_explicit"}:
            provider = resolve_provider(private_root, allow_synthetic=False, prefer_local=True)
        else:
            require_json_true(body.get("approve_remote"))
            ticket_id = str(body.get("consent_ticket") or "")
            if not ticket_id:
                raise InsightsError("cloud analysis needs a separate per-task approval", "needs_consent")
            provider = resolve_provider(
                private_root,
                allow_synthetic=False,
                consent={"approve_remote": True},
                engine=engine,
            )
        conn = handler._db(binding)
        try:
            if getattr(provider, "kind", "") in {"remote", "grok_cli"}:
                identity = load_identity(store)
                if identity is None:
                    raise InsightsError("confirm who you are before generating a profile", "identity_unresolved")
                skip_uids, _skip_statements = excluded_evidence(store)
                sample = collect_profile_records(
                    conn,
                    kind=str(body.get("kind") or "self"),
                    self_ids=identity["self_sender_ids"],
                    excluded=excluded_conversation_ids(store),
                    scope=body.get("scope") or {},
                    skip_uids=skip_uids,
                )
                consume_ticket(
                    store,
                    ticket_id,
                    kind=str(body.get("kind") or "self"),
                    engine_id=provider.engine_id,
                    endpoint=getattr(provider, "base_url", None) or (provider.public_view().get("host") if hasattr(provider, "public_view") else None),
                    model=getattr(provider, "model", None),
                    scope=body.get("scope") or {},
                    record_uids=[row["record_uid"] for row in sample["records"]],
                    source_revision=binding.revision,
                    identity_revision=identity["revision"],
                )
            if body.get('background') is True:
                result=start_profile_task(handler.ctx.job_store,handler.ctx.runtime.data_root,binding,
                    request=body,provider=provider,approved_records=sample['records'] if getattr(provider,'kind','') in {'remote','grok_cli'} else None)
                handler._send_json(result)
                return True
            result = run_profile(
                store,
                conn,
                kind=str(body.get("kind") or "self"),
                source_revision=binding.revision,
                scope=body.get("scope") or {},
                subject_person_id=body.get("subject_person_id"),
                engine_id=provider.engine_id,
                provider=provider,
            )
        finally:
            conn.close()
        handler._send_json(result)
        return True
    if path.startswith("/api/profiles/observations/") and path.endswith("/corrections"):
        store, _binding = _store(handler)
        oid = path.split("/")[4]
        handler._send_json(add_correction(store, oid, str(body.get("action") or ""), body.get("user_text")))
        return True
    if path == "/api/insights/exports":
        store, _binding = _store(handler)
        kind = str(body.get("kind") or "learning")
        if kind in {"profile","self","friend"}:
            dest=handler.ctx.runtime.data_root/'deliveries'/f'profile_{make_snapshot_id()}'
            result=export_profile(store,str(body.get('run_id') or ''),dest)
            handler._send_json({**result,'delivery_id':dest.name})
            return True
        if kind != "learning":
            raise InsightsError("only learning pack export is implemented in this version", "unsupported_export")
        dest = handler.ctx.runtime.data_root / "deliveries" / f"learning_{make_snapshot_id()}"
        result = export_learning_pack(store, dest, archive_root=_binding.root)
        handler._send_json({"path": result["path"], "item_count": result["item_count"], "media_count": result.get("media_count", 0), "job_id": dest.name,"delivery_id":dest.name})
        return True
    return False
