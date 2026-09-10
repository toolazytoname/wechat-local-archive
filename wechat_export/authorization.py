"""Task-bound authorization. Consent flags alone never enable live WeChat steps."""

from __future__ import annotations

from typing import Any
import time

CONSENT_KEYS = (
    "confirm_preservation",
    "confirm_debug_copy",
    "confirm_key_capture",
    "confirm_enter_wechat",
)

LIVE_GRANT_PHRASE = "ALLOW_LIVE_WECHAT_STEPS"
LIVE_GRANT_SCOPE = "this_job_live_wechat_steps"


def consents_complete(payload: dict[str, Any]) -> bool:
    return all(payload.get(key) is True for key in CONSENT_KEYS)


def environment_allows_adapter(adapter: dict[str, Any]) -> tuple[bool, str | None]:
    """4.x arm64 candidate is an allowed environment. Unknown version is not."""
    if not adapter.get("candidate"):
        return False, adapter.get("reason") or "unsupported_environment"
    return True, None


def live_grant_valid(job_id: str, payload: dict[str, Any]) -> bool:
    grant = payload.get("live_grant") or {}
    return (
        grant.get("scope") == LIVE_GRANT_SCOPE
        and grant.get("job_id") == job_id
        and grant.get("phrase_accepted") is True
        and not grant.get("consumed")
        and isinstance(grant.get("expires_at"), (int, float))
        and time.time() < grant["expires_at"]
    )


def live_operations_permitted(
    *,
    job_id: str,
    payload: dict[str, Any],
    adapter: dict[str, Any],
    preflight: dict[str, Any],
) -> tuple[bool, str | None]:
    """All three gates: allowed environment, this-job grant, preflight.

    The adapter's public key_capture_allowed flag is intentionally ignored as a
    global switch — it stays false. Live work is permitted only per job.
    """
    if payload.get("synthetic"):
        return False, "synthetic_job"
    allowed, reason = environment_allows_adapter(adapter)
    if not allowed:
        return False, reason
    if not consents_complete(payload):
        return False, "consents_incomplete"
    if not live_grant_valid(job_id, payload):
        return False, "live_grant_missing"
    if not preflight.get("ok"):
        return False, preflight.get("reason") or "preflight_failed"
    return True, None


def make_live_grant(job_id: str, phrase: str) -> dict[str, Any]:
    if phrase != LIVE_GRANT_PHRASE:
        raise ValueError("live grant phrase mismatch")
    return {
        "scope": LIVE_GRANT_SCOPE,
        "job_id": job_id,
        "phrase_accepted": True,
        "consumed": False,
        "expires_at": time.time() + 900,
    }
