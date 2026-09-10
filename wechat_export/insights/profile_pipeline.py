"""Coverage stats and conservative explicit-statement extraction. Not a personality test."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from wechat_export.export_service import QueryError, parse_time_to_ms
from wechat_export.insights.identity import excluded_conversation_ids, load_identity
from wechat_export.insights.profile_validate import (
    SENSITIVE,
    classify_statement_support,
    filter_valid,
    validate_observation,
)
from wechat_export.insights.store import InsightStore, InsightsError, utc_now

DEFAULT_REMOTE_RECORD_LIMIT = 80
DEFAULT_REMOTE_TEXT_CHARS = 400

PLAN_RE = re.compile(
    r"((?:我想|我打算|我准备|我希望|我要|I want to|I plan to|I'd like to)[^。.!?\n]{2,80})",
    re.I,
)
REPORTED_SPEECH_RE = re.compile(
    r"(他说|她说|他们说|有人说|不是我的计划|但这不是|开玩笑|难道我|“我想|\"I want|「我想)",
    re.I,
)


@dataclass
class LocalExplicitProvider:
    engine_id: str = "local_explicit"
    kind: str = "local_explicit"

    def available(self) -> bool:
        return True

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload["extractor"](payload)


def parse_scope_time_ms(value: Any, *, end: bool = False) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except ValueError:
            raise InsightsError("invalid date", "invalid_range") from None
        if end:
            dt = dt + timedelta(days=1)
        return int(dt.timestamp() * 1000)
    try:
        ms = parse_time_to_ms(text)
    except QueryError:
        raise InsightsError("invalid date", "invalid_range") from None
    if ms is None:
        raise InsightsError("invalid date", "invalid_range")
    return ms


def _ms_to_utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def normalize_scope(scope: dict[str, Any] | None, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    raw = dict(scope or {})
    ids: list[str] = []
    if "conversation_ids" in raw:
        ids = [str(item) for item in (raw.get("conversation_ids") or []) if item]
        if not ids:
            raise InsightsError("select at least one conversation", "empty_scope")
    elif raw.get("conversation_id"):
        ids = [str(raw["conversation_id"])]
    if ids and conn is not None:
        known = {row["conversation_id"] for row in conn.execute("SELECT conversation_id FROM conversations")}
        missing = [cid for cid in ids if cid not in known]
        if missing:
            raise InsightsError("conversation not in this archive", "unknown_conversation")
    if raw.get("since_ms") is not None:
        since_ms = int(raw["since_ms"])
    elif raw.get("since"):
        since_ms = parse_scope_time_ms(raw.get("since"), end=False)
    else:
        since_ms = None
    if raw.get("until_ms") is not None:
        until_ms = int(raw["until_ms"])
    elif raw.get("until"):
        until_ms = parse_scope_time_ms(raw.get("until"), end=True)
    else:
        until_ms = None
    if since_ms is not None and until_ms is not None and since_ms >= until_ms:
        raise InsightsError("until must be after since", "invalid_range")
    out = dict(raw)
    if ids:
        out["conversation_ids"] = ids
        if len(ids) == 1:
            out["conversation_id"] = ids[0]
    if since_ms is not None:
        out["since"] = _ms_to_utc_iso(since_ms)
        out["since_ms"] = since_ms
    elif "since" in out:
        out["since"] = None
    if until_ms is not None:
        out["until"] = _ms_to_utc_iso(until_ms)
        out["until_ms"] = until_ms
    elif "until" in out:
        out["until"] = None
    return out


def coverage(
    conn: sqlite3.Connection,
    *,
    self_ids: list[str],
    excluded: set[str],
    since: str | None = None,
    until: str | None = None,
    conversation_ids: list[str] | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(scope or {})
    if since is not None:
        merged.setdefault("since", since)
    if until is not None:
        merged.setdefault("until", until)
    if conversation_ids is not None:
        merged["conversation_ids"] = conversation_ids
    where, params = apply_scope_filters(["1=1"], [], scope=merged, excluded=set(), conn=conn)
    if excluded:
        where.append("conversation_id NOT IN (%s)" % ",".join("?" * len(excluded)))
        params.extend(sorted(excluded))
    clause = " AND ".join(where)
    rows = conn.execute(
        f"SELECT conversation_id, is_self, sender_id, readable, media_kind, substr(timestamp_utc,1,7) AS month FROM messages WHERE {clause}",
        params,
    ).fetchall()
    self_n = other_n = unknown_n = unread_n = 0
    by_conv: dict[str, int] = {}
    by_month: dict[str, int] = {}
    self_by_conv: dict[str, int] = {}
    unread_by_kind: dict[str, int] = {}
    for row in rows:
        cid = row["conversation_id"]
        if cid in excluded:
            continue
        by_conv[cid] = by_conv.get(cid, 0) + 1
        month = row["month"] or "unknown"
        by_month[month] = by_month.get(month, 0) + 1
        if not row["readable"]:
            unread_n += 1
            kind = str(row["media_kind"] or "unknown")
            unread_by_kind[kind] = unread_by_kind.get(kind, 0) + 1
        if row["is_self"] == 1:
            self_n += 1
            self_by_conv[cid] = self_by_conv.get(cid, 0) + 1
        elif row["sender_id"] in self_ids:
            self_n += 1
        elif not row["sender_id"]:
            unknown_n += 1
        else:
            other_n += 1
    total = self_n + other_n + unknown_n
    heavy = None
    if self_n:
        top = max(self_by_conv.items(), key=lambda kv: kv[1])
        if top[1] / self_n >= 0.5:
            heavy = {"conversation_id": top[0], "share": round(top[1] / self_n, 3)}
    return {
        "self_count": self_n,
        "other_count": other_n,
        "unknown_count": unknown_n,
        "non_readable": unread_n,
        "non_readable_by_kind": unread_by_kind,
        "total_in_scope": total,
        "by_conversation": by_conv,
        "by_month": by_month,
        "self_by_conversation": self_by_conv,
        "family_or_single_thread_heavy": heavy,
        "excluded_conversations": sorted(excluded),
    }


def _has_timestamp_ms(conn: sqlite3.Connection | None) -> bool:
    if conn is None:
        return False
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    return "timestamp_ms" in cols


def apply_scope_filters(
    where: list[str],
    params: list[Any],
    *,
    scope: dict[str, Any],
    excluded: set[str],
    conn: sqlite3.Connection | None = None,
) -> tuple[list[str], list[Any]]:
    scope = normalize_scope(scope, conn=conn)
    if scope.get("conversation_ids"):
        ids = [str(item) for item in scope["conversation_ids"] if item]
        where.append("conversation_id IN (%s)" % ",".join("?" * len(ids)))
        params.extend(ids)
    elif scope.get("conversation_id"):
        where.append("conversation_id = ?")
        params.append(scope["conversation_id"])
    if excluded:
        where.append("conversation_id NOT IN (%s)" % ",".join("?" * len(excluded)))
        params.extend(sorted(excluded))
    use_ms = _has_timestamp_ms(conn)
    if scope.get("since_ms") is not None and use_ms:
        where.append("timestamp_ms >= ?")
        params.append(int(scope["since_ms"]))
    elif scope.get("since"):
        where.append("timestamp_utc >= ?")
        params.append(scope["since"])
    if scope.get("until_ms") is not None and use_ms:
        where.append("timestamp_ms < ?")
        params.append(int(scope["until_ms"]))
    elif scope.get("until"):
        where.append("timestamp_utc < ?")
        params.append(scope["until"])
    return where, params


def excluded_evidence(store: InsightStore) -> tuple[set[str], set[str]]:
    uids: set[str] = set()
    statements: set[str] = set()
    rows = store.conn.execute(
        """
        SELECT c.excluded_evidence_json, o.evidence_json, o.statement
        FROM corrections c
        JOIN observations o ON o.observation_id = c.observation_id
        WHERE c.action = 'exclude'
        """
    ).fetchall()
    for row in rows:
        statements.add(row["statement"])
        for blob in (row["excluded_evidence_json"], row["evidence_json"]):
            try:
                payload = json.loads(blob or "[]")
            except json.JSONDecodeError:
                continue
            for item in payload:
                uid = item if isinstance(item, str) else (item or {}).get("record_uid")
                if uid:
                    uids.add(str(uid))
    return uids, statements


def extract_self_observations(
    conn: sqlite3.Connection,
    *,
    self_ids: list[str],
    excluded: set[str],
    scope: dict[str, Any],
    skip_uids: set[str] | None = None,
    skip_statements: set[str] | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ["readable = 1", "is_self = 1"]
    if self_ids:
        where.append("sender_id IN (%s)" % ",".join("?" * len(self_ids)))
        params.extend(self_ids)
    where, params = apply_scope_filters(where, params, scope=scope, excluded=excluded, conn=conn)
    sql = f"SELECT record_uid, conversation_id, sender_id, timestamp_utc, text FROM messages WHERE {' AND '.join(where)} ORDER BY timestamp_utc"
    out: list[dict[str, Any]] = []
    allowed = set()
    for row in conn.execute(sql, params):
        text = (row["text"] or "").strip()
        if not text or text.lstrip().startswith("<"):
            continue
        if skip_uids and row["record_uid"] in skip_uids:
            continue
        if REPORTED_SPEECH_RE.search(text):
            continue
        allowed.add(row["record_uid"])
        # Select complete sentence units, never crop conditions or pronouns.
        for sentence in re.split(r"[。.!?！？\n]+", text):
            statement = sentence.strip()
            if not PLAN_RE.search(statement):
                continue
            if SENSITIVE.search(statement):
                continue
            if skip_statements and statement in skip_statements:
                continue
            obs = {
                "dimension": "scoped_observation",
                "statement": statement,
                "basis": "scoped_observation",
                "quote": statement,
                "source_text": text,
                "evidence": [
                    {
                        "record_uid": row["record_uid"],
                        "conversation_id": row["conversation_id"],
                        "sender_id": row["sender_id"],
                        "quote": statement,
                    }
                ],
                "evidence_ids": [row["record_uid"]],
                "context_scope": row["conversation_id"],
                "caveats": ["这是记录中的完整句段摘录，保留条件和主语；不等于本人意图或已核实事实。"],
            }
            try:
                validate_observation(obs, allowed_uids=allowed, self_ids=set(self_ids), subject="self")
            except InsightsError:
                continue
            out.append(obs)
    return out


def extract_friend_observations(
    conn: sqlite3.Connection,
    *,
    friend_sender_ids: list[str],
    excluded: set[str],
    scope: dict[str, Any],
    skip_uids: set[str] | None = None,
    skip_statements: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not friend_sender_ids:
        return []
    params: list[Any] = list(friend_sender_ids)
    where = ["readable = 1", "is_self = 0", "sender_id IN (%s)" % ",".join("?" * len(friend_sender_ids))]
    where, params = apply_scope_filters(where, params, scope=scope, excluded=excluded, conn=conn)
    sql = f"SELECT record_uid, conversation_id, sender_id, timestamp_utc, text FROM messages WHERE {' AND '.join(where)} ORDER BY timestamp_utc"
    out = []
    allowed = set()
    for row in conn.execute(sql, params):
        text = (row["text"] or "").strip()
        if not text or text.lstrip().startswith("<"):
            continue
        if skip_uids and row["record_uid"] in skip_uids:
            continue
        allowed.add(row["record_uid"])
        snippet = text
        if skip_statements and snippet in skip_statements:
            continue
        obs = {
            "dimension": "stated_by_friend",
            "statement": snippet,
            "basis": "scoped_observation",
            "quote": snippet,
            "source_text": text,
            "evidence": [
                {
                    "record_uid": row["record_uid"],
                    "conversation_id": row["conversation_id"],
                    "sender_id": row["sender_id"],
                    "quote": snippet,
                }
            ],
            "evidence_ids": [row["record_uid"]],
            "caveats": ["这是该发送者发出的完整文本，可能包含引用、转述或假设；不等于其本人观点或已核实事实。"],
        }
        try:
            validate_observation(obs, allowed_uids=allowed, self_ids=set(), subject="friend")
        except InsightsError:
            continue
        out.append(obs)
        if len(out) >= 40:
            break
    return out


def collect_profile_records(
    conn: sqlite3.Connection,
    *,
    kind: str,
    self_ids: list[str],
    excluded: set[str],
    scope: dict[str, Any],
    limit: int = DEFAULT_REMOTE_RECORD_LIMIT,
    max_chars: int = DEFAULT_REMOTE_TEXT_CHARS,
    skip_uids: set[str] | None = None,
) -> dict[str, Any]:
    params: list[Any] = []
    if kind == "friend":
        friend_ids = list(scope.get("friend_sender_ids") or [])
        if not friend_ids:
            return {"records": [], "estimated_chars": 0, "candidate_count": 0}
        where = ["readable = 1", "is_self = 0", "sender_id IN (%s)" % ",".join("?" * len(friend_ids))]
        params.extend(friend_ids)
    else:
        where = ["readable = 1", "is_self = 1"]
        if self_ids:
            where.append("sender_id IN (%s)" % ",".join("?" * len(self_ids)))
            params.extend(self_ids)
    where, params = apply_scope_filters(where, params, scope=scope, excluded=excluded, conn=conn)
    sql = (
        "SELECT record_uid, conversation_id, sender_id, timestamp_utc, text "
        f"FROM messages WHERE {' AND '.join(where)} ORDER BY timestamp_utc DESC"
    )
    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in conn.execute(sql, params):
        text = (row["text"] or "").strip()
        if not text or text.lstrip().startswith("<"):
            continue
        if skip_uids and row["record_uid"] in skip_uids:
            continue
        clipped = text[:max_chars]
        record = {
            "record_uid": row["record_uid"],
            "conversation_id": row["conversation_id"],
            "sender_id": row["sender_id"],
            "timestamp_utc": row["timestamp_utc"],
            "text": clipped,
        }
        rank = 0 if PLAN_RE.search(text) else 1
        ranked.append((rank, record))
    ranked.sort(key=lambda item: item[0])
    records = [item[1] for item in ranked[: max(1, limit)]]
    allowed_convos = None
    if "conversation_ids" in scope:
        allowed_convos = {str(item) for item in (scope.get("conversation_ids") or []) if item}
    elif scope.get("conversation_id"):
        allowed_convos = {str(scope["conversation_id"])}
    if allowed_convos is not None:
        for item in records:
            if item["conversation_id"] not in allowed_convos:
                raise InsightsError("record is outside the frozen scope", "evidence_out_of_scope")
    return {
        "records": records,
        "estimated_chars": sum(len(item["text"]) for item in records),
        "candidate_count": len(ranked),
        "record_uids": [item["record_uid"] for item in records],
    }


def _collect_evidence_ids(obs: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for uid in obs.get("evidence_ids") or []:
        if uid and str(uid) not in ids:
            ids.append(str(uid))
    for item in obs.get("evidence") or []:
        uid = item if isinstance(item, str) else (item or {}).get("record_uid")
        if uid and str(uid) not in ids:
            ids.append(str(uid))
    return ids


def _rebuild_observation(obs: dict[str, Any], records: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    ids = _collect_evidence_ids(obs)
    if not ids:
        return None, "missing_evidence"
    missing = [uid for uid in ids if uid not in records]
    if missing:
        return None, "unknown_evidence"
    evidence = []
    for uid in ids:
        rec = records[uid]
        quote = rec["text"]
        evidence.append(
            {
                "record_uid": rec["record_uid"],
                "conversation_id": rec["conversation_id"],
                "sender_id": rec["sender_id"],
                "quote": quote,
            }
        )
    source_text = records[evidence[0]["record_uid"]]["text"]
    statement = str(obs.get("statement") or evidence[0]["quote"])
    support = classify_statement_support(statement, source_text)
    if support in {"unsupported", "contradicted"}:
        return None, support
    caveats = list(obs.get("caveats") or ["观察必须能在原文中核对。"])
    if support == "restatement":
        caveats.append("这是对原话的归纳复述，须对照原文；不是已核实事实，也不是画像归纳完成。")
    elif support == "excerpt":
        caveats.append("这是记录中的原话摘录，不是已核实的事实。")
    rebuilt = {
        "dimension": obs.get("dimension") or "scoped_observation",
        "statement": statement,
        "basis": "scoped_observation",
        "quote": evidence[0]["quote"],
        "source_text": source_text,
        "evidence": evidence,
        "evidence_ids": [item["record_uid"] for item in evidence],
        "context_scope": evidence[0]["conversation_id"],
        "caveats": caveats,
        "support": support,
    }
    return rebuilt, None


def _merge_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for obs in observations:
        key = str(obs.get("statement") or "")
        if key not in merged:
            merged[key] = dict(obs)
            merged[key]["evidence"] = list(obs.get("evidence") or [])
            merged[key]["evidence_ids"] = list(obs.get("evidence_ids") or [])
            order.append(key)
            continue
        seen = set(merged[key]["evidence_ids"])
        for item in obs.get("evidence") or []:
            uid = item.get("record_uid") if isinstance(item, dict) else item
            if uid and str(uid) not in seen:
                merged[key]["evidence"].append(item)
                merged[key]["evidence_ids"].append(str(uid))
                seen.add(str(uid))
    return [merged[key] for key in order]


def _observations_from_provider(
    provider: Any,
    conn: sqlite3.Connection,
    *,
    kind: str,
    self_ids: list[str],
    excluded: set[str],
    scope: dict[str, Any],
    skip_uids: set[str] | None = None,
    skip_statements: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if getattr(provider, "kind", "local_explicit") not in {"remote", "grok_cli"}:
        if kind == "self":
            observations = extract_self_observations(
                conn,
                self_ids=self_ids,
                excluded=excluded,
                scope=scope,
                skip_uids=skip_uids,
                skip_statements=skip_statements,
            )
            return "completed", _merge_observations(observations), {"upload_count": 0, "estimated_chars": 0, "candidate_count": len(observations)}
        observations = extract_friend_observations(
            conn,
            friend_sender_ids=list(scope.get("friend_sender_ids") or []),
            excluded=excluded,
            scope=scope,
            skip_uids=skip_uids,
            skip_statements=skip_statements,
        )
        observations = _merge_observations(observations)
        status = "insufficient" if len(observations) < 2 else "completed"
        return status, observations, {"upload_count": 0, "estimated_chars": 0, "candidate_count": len(observations)}

    packed = collect_profile_records(
        conn,
        kind=kind,
        self_ids=self_ids,
        excluded=excluded,
        scope=scope,
        skip_uids=skip_uids,
    )
    records = packed["records"]
    if not records:
        return "insufficient", [], packed
    raw = provider.analyze(
        {
            "kind": kind,
            "self_ids": self_ids,
            "records": records,
        }
    )
    by_uid = {item["record_uid"]: item for item in records}
    prepared: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for obs in raw.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        rebuilt, reason = _rebuild_observation(obs, by_uid)
        if rebuilt is None:
            rejected.append({"reason": reason or "invalid_evidence", "statement": str(obs.get("statement") or "")})
            continue
        if skip_statements and rebuilt["statement"] in skip_statements:
            continue
        if skip_uids and any(uid in skip_uids for uid in rebuilt["evidence_ids"]):
            continue
        prepared.append(rebuilt)
    kept = filter_valid(
        prepared,
        allowed_uids=set(by_uid),
        self_ids=set(self_ids) if kind == "self" else set(),
        subject="self" if kind == "self" else "friend",
        records=by_uid,
    )
    kept = _merge_observations(kept)
    packed["processed_count"] = len(kept)
    packed["rejected"] = rejected
    packed["rejected_count"] = len(rejected)
    if not kept:
        return "partial", [], packed
    return "completed", kept, packed


def run_profile(
    store: InsightStore,
    conn: sqlite3.Connection,
    *,
    kind: str,
    source_revision: str,
    scope: dict[str, Any],
    subject_person_id: str | None,
    engine_id: str,
    provider: Any | None = None,
    before_publish=None,
) -> dict[str, Any]:
    identity = load_identity(store)
    if identity is None or identity["verification_state"] in {"missing", "conflict"}:
        raise InsightsError("confirm who you are before generating a profile", "identity_unresolved")
    self_ids = identity["self_sender_ids"]
    excluded = excluded_conversation_ids(store)
    skip_uids, skip_statements = excluded_evidence(store)
    stats = coverage(
        conn,
        self_ids=self_ids,
        excluded=excluded,
        scope=scope,
    )
    extra: dict[str, Any] = {}
    if kind == "self" and stats["self_count"] < 3:
        status = "insufficient"
        observations: list[dict[str, Any]] = []
    elif kind == "friend" and not scope.get("friend_sender_ids"):
        raise InsightsError("select a friend by stable sender id", "friend_required")
    else:
        status, observations, extra = _observations_from_provider(
            provider,
            conn,
            kind=kind,
            self_ids=self_ids,
            excluded=excluded,
            scope=scope,
            skip_uids=skip_uids,
            skip_statements=skip_statements,
        )
    if before_publish is not None:
        before_publish()
    run_id = "run_" + uuid.uuid4().hex[:12]
    processed = extra.get("processed_count", len(observations))
    candidate = extra.get("candidate_count", processed)
    try:
        store.conn.execute(
        """
        INSERT INTO profile_runs(run_id, kind, subject_person_id, scope_json, identity_revision, engine_id, status, coverage_json, result_json, processed_count, created_at, source_revision)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            kind,
            subject_person_id,
            json.dumps(scope, ensure_ascii=False),
            identity["revision"],
            engine_id,
            status,
            json.dumps(stats, ensure_ascii=False),
            json.dumps(
                {
                    "observation_count": len(observations),
                    "upload_count": len(extra.get("records") or []),
                    "estimated_chars": extra.get("estimated_chars", 0),
                    "candidate_count": candidate,
                    "sampled_count": len(extra.get("records") or []),
                    "rejected": extra.get("rejected") or [],
                    "rejected_count": extra.get("rejected_count", 0),
                    "record_limit": DEFAULT_REMOTE_RECORD_LIMIT,
                    "text_char_limit": DEFAULT_REMOTE_TEXT_CHARS,
                },
                ensure_ascii=False,
            ),
            processed,
            utc_now(),
            source_revision,
        ),
    )
        for obs in observations:
            oid = "obs_" + uuid.uuid4().hex[:16]
            store.conn.execute(
                """
                INSERT INTO observations(observation_id, run_id, dimension, statement, basis, context_scope, evidence_json, caveats_json, review_state, synthetic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0)
                """,
                (
                    oid,
                    run_id,
                    obs["dimension"],
                    obs["statement"],
                    obs["basis"],
                    obs.get("context_scope"),
                    json.dumps(obs["evidence"], ensure_ascii=False),
                    json.dumps(obs.get("caveats") or []),
                ),
            )
            for ev in obs["evidence"]:
                eid = "ev_" + hashlib.sha256((oid + ev["record_uid"]).encode()).hexdigest()[:12]
                store.conn.execute(
                    """
                    INSERT INTO evidence(evidence_id, run_id, record_uid, conversation_id, sender_id, author_role, quote, body_hash, content_origin)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid,
                        run_id,
                        ev["record_uid"],
                        ev.get("conversation_id"),
                        ev.get("sender_id"),
                        "self" if kind == "self" else "friend",
                        ev.get("quote") or "",
                        None,
                        "self_authored" if kind == "self" else "friend_authored",
                    ),
                )
        if before_publish is not None:
            before_publish()
        store.conn.commit()
    except Exception:
        store.conn.rollback()
        raise
    return get_run(store, run_id)


def _stale_fields(store: InsightStore, run: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    current_source = store.get_meta("source_revision")
    if current_source and run.get("source_revision") and run["source_revision"] != current_source:
        reasons.append("source_revision_changed")
    identity = load_identity(store)
    current_identity = identity["revision"] if identity else None
    if current_identity is not None and int(run.get("identity_revision") or 0) != int(current_identity):
        reasons.append("identity_revision_changed")
    stale = bool(reasons)
    run["stale"] = stale
    run["is_stale"] = stale
    run["stale_reason"] = ",".join(reasons) if reasons else None
    run["current_source_revision"] = current_source
    run["current_identity_revision"] = current_identity
    return run


def get_run(store: InsightStore, run_id: str) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM profile_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise InsightsError("run not found", "not_found")
    run = dict(row)
    run["scope"] = json.loads(run["scope_json"])
    run["coverage"] = json.loads(run["coverage_json"] or "{}")
    try:
        run["result"] = json.loads(run.get("result_json") or "{}")
    except json.JSONDecodeError:
        run["result"] = {}
    obs_rows = store.conn.execute("SELECT * FROM observations WHERE run_id = ?", (run_id,)).fetchall()
    observations = []
    for obs in obs_rows:
        item = dict(obs)
        item["evidence"] = json.loads(item["evidence_json"])
        item["caveats"] = json.loads(item["caveats_json"])
        item["synthetic"] = bool(item["synthetic"])
        observations.append(item)
    run["observations"] = observations
    return _stale_fields(store, run)


def add_correction(store: InsightStore, observation_id: str, action: str, user_text: str | None) -> dict[str, Any]:
    if action not in {"wrong_fact", "different_context", "exclude", "note"}:
        raise InsightsError("unsupported correction", "invalid_correction")
    obs = store.conn.execute("SELECT * FROM observations WHERE observation_id = ?", (observation_id,)).fetchone()
    if obs is None:
        raise InsightsError("observation not found", "not_found")
    cid = "cor_" + uuid.uuid4().hex[:12]
    evidence = json.loads(obs["evidence_json"] or "[]")
    excluded_uids = [item.get("record_uid") for item in evidence if isinstance(item, dict) and item.get("record_uid")]
    store.conn.execute(
        "INSERT INTO corrections(correction_id, observation_id, action, user_text, excluded_evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, observation_id, action, user_text, json.dumps(excluded_uids, ensure_ascii=False), utc_now()),
    )
    state = "excluded" if action == "exclude" else "corrected"
    store.conn.execute("UPDATE observations SET review_state = ? WHERE observation_id = ?", (state, observation_id))
    store.conn.commit()
    return {"correction_id": cid, "observation_id": observation_id, "action": action, "review_state": state}


def list_runs(store: InsightStore, *, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    sql = (
        "SELECT run_id, kind, subject_person_id, engine_id, status, processed_count, created_at, "
        "result_json, source_revision, identity_revision FROM profile_runs"
    )
    params: list[Any] = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    out = []
    for row in store.conn.execute(sql, params):
        item = dict(row)
        try:
            item["result"] = json.loads(item.get("result_json") or "{}")
        except json.JSONDecodeError:
            item["result"] = {}
        out.append(_stale_fields(store, item))
    return out
