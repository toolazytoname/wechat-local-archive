"""Maintainer-reviewed support declarations bound to exact observed code.

No entry is created from a successful test or a local key file. The shipped
registry is intentionally empty pending two independently reviewed real runs.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from wechat_export import PARSER_VERSION
from wechat_export.build_fingerprint import SCHEMA

DRIVER_ID = 'macos-copy-kdf/1'
CODEC_ID = 'sqlcipher4-4096-pbkdf2-sha512-256000/1'
REGISTRY_SCHEMA = 'wechat-compatibility-registry/1'
HASH = re.compile(r'^[0-9a-f]{64}$')
STAGE_KEYS = ('key_acquisition_verified', 'codec_verified', 'export_verified')


def environment_binding(env: dict) -> dict | None:
    fingerprint = env.get('wechat_fingerprint') or {}
    if (fingerprint.get('schema') != SCHEMA or fingerprint.get('complete') is not True or
            not HASH.fullmatch(str(fingerprint.get('sha256', ''))) or
            fingerprint.get('bundle_identifier') != 'com.tencent.xinWeChat' or
            fingerprint.get('wechat_version') != env.get('wechat_version') or
            fingerprint.get('wechat_build') != env.get('wechat_build') or
            not isinstance(env.get('mac_ver'), str) or not env['mac_ver'] or
            env.get('machine') not in {'arm64', 'aarch64'}):
        return None
    return {'wechat_version': env['wechat_version'], 'wechat_build': env['wechat_build'],
            'machine': env['machine'], 'mac_ver': env['mac_ver'],
            'bundle_sha256': fingerprint['sha256'], 'fingerprint_schema': SCHEMA,
            'driver_id': DRIVER_ID, 'codec_id': CODEC_ID, 'parser_version': PARSER_VERSION}


def read_registry() -> dict:
    path = Path(__file__).with_name('compatibility-registry.json')
    try:
        with path.open('rb') as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError()
        registry = json.loads(data)
        if registry.get('schema') != REGISTRY_SCHEMA or not isinstance(registry.get('entries'), list):
            raise ValueError()
        return registry
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return {'schema': REGISTRY_SCHEMA, 'entries': [], 'invalid': True}


def reviewed_support(env: dict, *, registry: dict | None = None) -> dict:
    binding = environment_binding(env)
    result = {'verified': False, 'binding': binding, 'evidence_ids': [], 'reason': 'no_matching_reviewed_evidence'}
    if not binding:
        return dict(result, reason='fingerprint_unavailable_or_mismatched')
    if (env.get('wechat_codesign') or {}).get('verified') is not True:
        return dict(result, reason='signature_not_verified')
    registry = registry if registry is not None else read_registry()
    if registry.get('schema') != REGISTRY_SCHEMA or registry.get('invalid'):
        return dict(result, reason='invalid_compatibility_registry')
    entries = registry.get('entries')
    if not isinstance(entries, list) or len(entries) > 1000:
        return dict(result, reason='invalid_compatibility_registry')
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('binding') != binding or entry.get('release_reviewed') is not True:
            continue
        evidence = entry.get('evidence')
        if not isinstance(evidence, list) or not 2 <= len(evidence) <= 100:
            continue
        environments, ids, reports = set(), set(), set()
        valid = True
        for item in evidence:
            if not isinstance(item, dict):
                valid = False; break
            identity = item.get('environment_id')
            evidence_id = item.get('evidence_id')
            report = item.get('report_sha256')
            stages = item.get('stages') or {}
            if (not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', identity) or
                    not isinstance(evidence_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', evidence_id) or
                    not isinstance(report, str) or not HASH.fullmatch(report) or
                    item.get('binding') != binding or item.get('synthetic') is not False or
                    item.get('fresh_user_first_read') is not True or item.get('human_sample_accepted') is not True or
                    not isinstance(stages, dict) or any(stages.get(k) is not True for k in STAGE_KEYS)):
                valid = False; break
            environments.add(identity); ids.add(evidence_id); reports.add(report)
        if valid and min(len(environments), len(ids), len(reports)) >= 2:
            return dict(result, verified=True, reason=None, evidence_ids=sorted(ids))
    return result
