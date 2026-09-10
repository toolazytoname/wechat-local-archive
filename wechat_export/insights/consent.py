"""One-time, short-lived cloud analysis approval tickets bound to a frozen scope."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from wechat_export.insights.store import InsightStore, InsightsError, utc_now

TTL_SECONDS = 300


def require_json_true(value: Any, field: str = "approve_remote") -> None:
    if value is True:
        return
    raise InsightsError("approval must be an explicit JSON true", "needs_consent")


def scope_hash(kind: str, scope: dict[str, Any], record_uids: list[str], engine_id: str, endpoint: str | None, model: str | None) -> str:
    payload = {
        "kind": kind,
        "scope": scope,
        "record_uids": list(record_uids),
        "engine_id": engine_id,
        "endpoint": endpoint or "",
        "model": model or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_ticket(
    store: InsightStore,
    *,
    kind: str,
    engine_id: str,
    endpoint: str | None,
    model: str | None,
    scope: dict[str, Any],
    record_uids: list[str],
    source_revision: str,
    identity_revision: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    ticket_id = "tkt_" + uuid.uuid4().hex
    digest = scope_hash(kind, scope, record_uids, engine_id, endpoint, model)
    expires = (now + timedelta(seconds=TTL_SECONDS)).isoformat(timespec="seconds")
    store.conn.execute(
        """
        INSERT INTO consent_tickets(
          ticket_id, kind, engine_id, endpoint, model, scope_hash, record_uids,
          source_revision, identity_revision, created_at, expires_at, used
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            ticket_id,
            kind,
            engine_id,
            endpoint,
            model,
            digest,
            json.dumps(list(record_uids), ensure_ascii=False),
            source_revision,
            identity_revision,
            utc_now(),
            expires,
        ),
    )
    store.conn.commit()
    return {
        "ticket_id": ticket_id,
        "scope_hash": digest,
        "expires_at": expires,
        "upload_count": len(record_uids),
        "engine_id": engine_id,
        "model": model,
        "endpoint": endpoint,
    }


def consume_ticket(
    store: InsightStore,
    ticket_id: str,
    *,
    kind: str,
    engine_id: str,
    endpoint: str | None,
    model: str | None,
    scope: dict[str, Any],
    record_uids: list[str],
    source_revision: str,
    identity_revision: int,
) -> dict[str, Any]:
    if not ticket_id or not isinstance(ticket_id, str):
        raise InsightsError("cloud analysis needs a separate per-task approval", "needs_consent")
    row = store.conn.execute("SELECT * FROM consent_tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise InsightsError("consent ticket is not valid", "needs_consent")
    now = datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(row["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except ValueError:
        raise InsightsError("consent ticket is not valid", "needs_consent") from None
    if now > expires:
        raise InsightsError("consent ticket expired", "needs_consent")
    expected = scope_hash(kind, scope, record_uids, engine_id, endpoint, model)
    if row["scope_hash"] != expected or row["kind"] != kind or row["engine_id"] != engine_id:
        raise InsightsError("consent ticket does not match this scope", "needs_consent")
    if (row["endpoint"] or "") != (endpoint or "") or (row["model"] or "") != (model or ""):
        raise InsightsError("consent ticket does not match this scope", "needs_consent")
    if row["source_revision"] != source_revision:
        raise InsightsError("consent ticket does not match this archive revision", "needs_consent")
    if int(row["identity_revision"] or 0) != int(identity_revision):
        raise InsightsError("consent ticket does not match this identity revision", "needs_consent")
    stored_uids = json.loads(row["record_uids"] or "[]")
    if stored_uids != list(record_uids):
        raise InsightsError("consent ticket does not match this scope", "needs_consent")
    # Atomic consume: two connections can both observe used=0; only one UPDATE wins.
    cursor = store.conn.execute(
        "UPDATE consent_tickets SET used = 1 WHERE ticket_id = ? AND used = 0",
        (ticket_id,),
    )
    store.conn.commit()
    if cursor.rowcount != 1:
        raise InsightsError("consent ticket was already used", "needs_consent")
    payload = dict(row)
    payload["used"] = 1
    return payload
