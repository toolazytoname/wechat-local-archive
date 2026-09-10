"""Compatibility stages. Verified builds stay empty until evidenced on that build."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wechat_export.adapters.macos_xwechat import evaluate as evaluate_macos

STAGES = (
    "environment_detected",
    "adapter_candidate",
    "key_acquisition_verified",
    "codec_verified",
    "export_verified",
)


def evaluate_environment(env: dict[str, Any], *, private_root: Path | None = None) -> dict[str, Any]:
    """Return stage flags. Archive import is never blocked by WeChat support."""
    adapter = evaluate_macos(env)
    stages = {
        "environment_detected": bool(env.get("platform")),
        "adapter_candidate": bool(adapter.get("candidate")),
        "key_acquisition_verified": False,
        "codec_verified": False,
        "export_verified": False,
    }
    supported = adapter.get("candidate") and adapter.get("build_verified")
    from wechat_export.compatibility_registry import STAGE_KEYS
    if supported:
        for key in STAGE_KEYS:
            stages[key] = True
    evidence = _this_machine_evidence(env, private_root)
    return {
        "stages": stages,
        "this_machine_evidence": evidence,
        "adapter": adapter,
        "supported_for_guided_read": bool(supported),
        "archive_import_allowed": True,
        "read_blocked_reason": None
        if supported
        else (
            "no_verified_build"
            if adapter.get("candidate")
            else adapter.get("reason") or "unsupported_environment"
        ),
        "notes": [
            "A matching version string is not enough to mark a build verified.",
            "key_acquisition_verified / codec_verified / export_verified stay false until evidence is tied to this exact build.",
            "Existing local archives can be opened regardless of WeChat support.",
            "this_machine_evidence is local only and does not mark a new-user first read complete.",
        ],
    }


def _this_machine_evidence(env: dict, private_root: Path | None = None) -> dict[str, Any] | None:
    # Never implicitly read a source checkout's private directory from an
    # unrelated runtime/installed package. Old unbound reports confer no stages.
    if private_root is None:
        return None
    from wechat_export.compatibility_registry import environment_binding
    path = private_root / 'compatibility-evidence.json'
    if path.is_symlink() or not path.is_file():
        return None
    try:
        with path.open('rb') as stream:
            blob = stream.read(65537)
        if len(blob) > 65536:
            return None
        payload = json.loads(blob)
        binding = environment_binding(env)
        if not binding or not isinstance(payload, dict) or payload.get('binding') != binding:
            return None
        return {'binding_matches': True, 'new_user_first_read': False,
                'changes_published_support': False,
                'note': 'Local report only; public support needs independently reviewed evidence.'}
    except (OSError, ValueError, RecursionError):
        return None
