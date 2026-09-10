"""Local-only release inventory/rule scan. Findings never contain matched content.

This is a fail-closed release guard, not proof that arbitrary text is fictional.
Review public text/media and supply local identity needles separately. Never crawl
ignored runtime archives. No network, Git mutation, or history rewriting here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

PRIVATE_ROOTS = {'data', 'wiki', 'private-notes', 'private', 'raw', 'WeChat Local Archives', '.wla-scratch-v1', '.wla-scratch-v1.init.lock'}
MAX_BLOB = 32 * 1024 * 1024
RULES = {
    'private-key-block': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'credential-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-(?:proj-)?[A-Za-z0-9_-]{40,})\b'),
    'literal-database-key': re.compile(rb'''["']?(?:enc_key_hex|raw_key_hex)["']?\s*[:=]\s*["'][a-fA-F0-9]{64}["']'''),
    'operator-home-path': re.compile(rb'/Users/(?!<|\{|\$|example(?:/|\b)|USER(?:/|\b))[A-Za-z0-9_.-]+/'),
}
TEXT_SUFFIXES = {'.py', '.md', '.json', '.jsonl', '.toml', '.txt', '.yml', '.yaml', '.js', '.cjs', '.mjs',
                 '.html', '.css', '.svg', '.c', '.h', '.command', '.sh', '.gitignore'}


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL)


def private_path(path: str) -> bool:
    p = Path(path)
    return bool(set(p.parts) & PRIVATE_ROOTS) or p.name.startswith('.env') or p.suffix in {'.raw', '.key', '.pem', '.p12'}


def safe_path(path: str, needles: tuple[bytes, ...]) -> str:
    if private_path(path) or any(n in path.encode() for n in needles) or any(rule.search(path.encode()) for rule in RULES.values()):
        return '<redacted-path:' + hashlib.sha256(path.encode()).hexdigest()[:12] + '>'
    return path


def classify(path: str, data: bytes, needles: tuple[bytes, ...], assets: dict) -> list[str]:
    findings = [name for name, pattern in RULES.items() if pattern.search(data) or pattern.search(path.encode())]
    if any(n in data or n in path.encode() for n in needles):
        findings.append('local-sensitive-needle')
    try:
        data.decode('utf-8')
        text = b'\0' not in data
    except UnicodeDecodeError:
        text = False
    structured_chat = Path(path).suffix.lower() in {'.jsonl', '.csv'} or (
        Path(path).suffix.lower() == '.json' and b'"record_uid"' in data and b'"timestamp_utc"' in data)
    if structured_chat or not text or (Path(path).suffix.lower() not in TEXT_SUFFIXES and Path(path).name not in {'LICENSE', '.gitignore'}):
        entry = assets.get(path, {})
        versions = [entry, *entry.get('reviewed_versions', [])]
        digest = hashlib.sha256(data).hexdigest()
        if not any(v.get('review') and v.get('sha256') == digest for v in versions):
            findings.append('unreviewed-chat-data' if structured_chat else 'unreviewed-opaque-asset')
    return findings


def scan(repo: Path, *, scope='worktree', needles=(), assets=None) -> dict:
    """Enumerate every selected Git file. Ignored files are never crawled/read."""
    repo = repo.resolve()
    needles = tuple(n for n in needles if n)
    assets = assets or {}
    findings = []
    inventory = []
    revisions = []
    scanned_bytes = 0
    def inspect(path, revision, mode, load, size):
        nonlocal scanned_bytes
        item = {'path': safe_path(path, needles), 'revision': revision}
        if private_path(path):
            rules = ['private-runtime-path']
        elif mode == '120000':
            rules = ['symlink-not-reviewed']
        elif mode == '160000':
            rules = ['submodule-not-reviewed']
        elif size > MAX_BLOB:
            rules = ['oversize-not-reviewed']
        else:
            data = load()
            if len(data) != size:
                raise RuntimeError('audit_source_changed')
            scanned_bytes += len(data)
            inventory.append({**item, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
            rules = classify(path, data, needles, assets)
        if rules:
            findings.append({**item, 'rules': rules})
    def current_paths():
        return sorted(set(git(repo, 'ls-files', '-z').split(b'\0') +
                          git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')) - {b''})
    if scope == 'worktree':
        paths = current_paths()
        for raw in paths:
            name = raw.decode(); p = repo / name
            if not p.exists() and not p.is_symlink():
                continue  # tracked deletion is not a proposed release file
            # Check ancestors too: never follow a tracked directory changed to a symlink.
            if any(parent.is_symlink() for parent in [p, *p.parents] if parent != repo and parent.is_relative_to(repo)):
                inspect(name, None, '120000', lambda: b'', 0)
                continue
            if p.is_dir():
                inspect(name, None, '160000', lambda: b'', 0)
                continue
            before = p.stat()
            inspect(name, None, '100644', p.read_bytes, before.st_size)
            after = p.stat()
            if any(getattr(before, field) != getattr(after, field) for field in
                   ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
                raise RuntimeError('audit_source_changed')
        if paths != current_paths():
            raise RuntimeError('audit_source_changed')
    elif scope in {'head', 'history'}:
        revisions = git(repo, 'rev-list', '--all').decode().split() if scope == 'history' else [git(repo, 'rev-parse', 'HEAD').decode().strip()]
        for revision in revisions:
            for entry in git(repo, 'ls-tree', '-r', '-z', revision).split(b'\0'):
                if not entry: continue
                metadata, raw = entry.split(b'\t', 1)
                mode, kind, oid = metadata.decode().split()
                size = int(git(repo, 'cat-file', '-s', oid)) if kind == 'blob' else 0
                inspect(raw.decode(), revision, mode, lambda oid=oid: git(repo, 'cat-file', 'blob', oid), size)
        current_revisions = git(repo, 'rev-list', '--all').decode().split() if scope == 'history' else [git(repo, 'rev-parse', 'HEAD').decode().strip()]
        if revisions != current_revisions:
            raise RuntimeError('audit_source_changed')
    else:
        raise ValueError('invalid_audit_scope')
    return {'scope': scope, 'revision_count': len(revisions), 'scanned_files': len(inventory),
            'scanned_bytes': scanned_bytes, 'findings': findings, 'rule_scan_passed': not findings,
            'privacy_certified': False, 'local_needles_used': bool(needles),
            'inventory_sha256': hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
            'limitations': ['Rules do not prove arbitrary text is fictional.',
                            'Opaque assets require exact-hash human review.',
                            'History covers local reachable refs, not remote caches/forks or deleted objects.']}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--scope', choices=['worktree', 'head', 'history'], default='worktree')
    p.add_argument('--needles-file', type=Path, help='optional private UTF-8 JSON list; never copied into report')
    args = p.parse_args(argv)
    try:
        needles = ()
        if args.needles_file:
            if args.needles_file.is_symlink() or args.needles_file.stat().st_mode & 0o077:
                raise ValueError('private_needle_file_permissions')
            with args.needles_file.open('rb') as stream: blob = stream.read(65537)
            if len(blob) > 65536: raise ValueError('private_needle_file_too_large')
            values = json.loads(blob)
            if not isinstance(values, list) or not all(isinstance(v, str) and len(v) >= 4 for v in values):
                raise ValueError('invalid_needle_file')
            needles = tuple(v.encode() for v in values)
        asset_file = args.repo / 'docs/public-asset-review.json'
        assets = json.loads(asset_file.read_text()) if asset_file.exists() else {}
        result = scan(args.repo, scope=args.scope, needles=needles, assets=assets)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if result['rule_scan_passed'] else 1
    except Exception as exc:
        # Even exception messages can include operator paths or matched values.
        print(json.dumps({'rule_scan_passed': False, 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
