"""Source identity and schema-derived roles; never infer safe exclusion by name."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from wechat_export.fsutil import sha256_file

SCHEMA_VERSION = 'wechat-canonical/1'
LEDGER_VERSION = 'wechat-source-ledger/1'


def snapshot_inventory(root: Path, check=lambda: None) -> dict:
    parts = []
    for path in sorted(root.rglob('*')):
        check()
        if path.is_symlink():
            raise ValueError('snapshot_symlink')
        if path.is_file() and path.name.endswith(('.db', '.db-wal', '.db-shm')):
            from wechat_export.scratch import NAMESPACE
            if NAMESPACE in path.relative_to(root).parts:
                raise ValueError('scratch_is_not_a_snapshot_source')
            before = path.stat()
            digest = sha256_file(path)
            after = path.stat()
            if (before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise ValueError('snapshot_changed_during_inventory')
            parts.append({'path':path.relative_to(root).as_posix(), 'size':after.st_size,'sha256':digest})
    encoded=json.dumps(parts,sort_keys=True,separators=(',',':')).encode()
    return {'files':parts,'sha256':hashlib.sha256(encoded).hexdigest()}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _fts_schema(conn, tables):
    """Recreate declarations in memory to identify *expected* shadow schemas.

    Name suffixes (and even PRAGMA table_list's shadow label) alone can label a
    user-created table as a shadow of a contentless FTS table. Never exclude it
    based on that heuristic. No rows or application SQL are executed here.
    """
    virtual = [(name, sql) for name, sql in tables if sql and
               re.match(r'^\s*CREATE\s+VIRTUAL\s+TABLE\b', sql, re.I) and
               re.search(r'\bUSING\s+fts[345]\s*\(', sql, re.I)]
    if not virtual:
        return set(), []
    clone = sqlite3.connect(':memory:')
    try:
        for _, sql in virtual:
            clone.execute(sql)
        expected = {row[0] for row in clone.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if not row[0].startswith('sqlite_')}
        actual = {name for name, _ in tables}
        proven = set()
        for name in expected & actual:
            original_columns = conn.execute('PRAGMA table_info(' + _quote(name) + ')').fetchall()
            expected_columns = clone.execute('PRAGMA table_info(' + _quote(name) + ')').fetchall()
            if original_columns == expected_columns:
                proven.add(name)
        return proven, [name for name, _ in virtual]
    except sqlite3.Error:
        return set(), [name for name, _ in virtual]
    finally:
        clone.close()


def inspect_database_role(path: Path) -> dict:
    """Schema and counts only. Unknown tables remain explicit coverage gaps."""
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        tables = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        fts, virtual = _fts_schema(conn, tables)
        messages, classified, unknown = [], [], []
        for name, _ in tables:
            if name.startswith('sqlite_'):
                continue
            cols = {str(row[1]).lower() for row in conn.execute('PRAGMA table_info(' + _quote(name) + ')')}
            if name.startswith('Msg_'):
                item = {'table': name, 'role': 'message_source',
                        'rows': conn.execute('SELECT count(*) FROM ' + _quote(name)).fetchone()[0],
                        'schema_supported': {'message_content', 'create_time', 'local_id'} <= cols}
                messages.append(item)
            elif name in fts:
                item = {'table': name, 'role': 'derived_fts', 'rows': None}
            elif name == 'Name2Id' and cols & {'user_name', 'username'}:
                item = {'table': name, 'role': 'identity_map', 'rows': None}
            elif name.lower() in {'contact', 'chat_room'} and cols & {'username', 'user_name'}:
                item = {'table': name, 'role': 'contact_metadata', 'rows': None}
            else:
                item = {'table': name, 'role': 'unclassified', 'columns': sorted(cols),
                        'rows': conn.execute('SELECT count(*) FROM ' + _quote(name)).fetchone()[0],
                        'message_like_columns': bool(cols & {'message_content', 'create_time', 'content', 'body', 'payload'})}
                unknown.append(item)
            classified.append(item)
        other_objects = [{'name': name, 'type': kind, 'table': table,
                          'sql_sha256': hashlib.sha256((sql or '').encode()).hexdigest()}
                         for name, kind, table, sql in conn.execute(
                             "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE type IN ('view','trigger') ORDER BY name")]
        common = {'tables': classified, 'unclassified_tables': unknown,
                  'unclassified_schema_objects': other_objects,
                  'schema_coverage_complete': not unknown and not other_objects,
                  'unclassified_rows': sum(t['rows'] for t in unknown)}
        if messages:
            return dict(common, role='message_source', proof='message_tables_present', message_tables=messages,
                        input_rows=sum(t['rows'] for t in messages))
        user_names = {name for name, _ in tables if not name.startswith('sqlite_')}
        if virtual and user_names and user_names <= fts and not other_objects:
            return dict(common, role='derived_fts_only', proof='recreated_fts_shadow_schemas_only', virtual_table_count=len(virtual))
        if any(t['role'] == 'contact_metadata' for t in classified):
            return dict(common, role='contacts', proof='supported_contact_metadata_present')
        return dict(common, role='auxiliary_unclassified', proof='no_supported_message_table', table_count=len(tables))
    finally:
        conn.close()


def database_accounting(results: list[dict], *, authenticated: bool) -> dict:
    """Keep snapshot-wide status and schema gaps separate from message counts."""
    from collections import Counter
    statuses = Counter(r.get('status', 'unknown') for r in results)
    unknown = []
    skipped = []
    other_objects = []
    for result in results:
        role = result.get('schema_evidence') or {}
        for table in role.get('unclassified_tables') or []:
            unknown.append({'path': result.get('path'), **table})
        for item in role.get('unclassified_schema_objects') or []:
            other_objects.append({'path': result.get('path'), **item})
        for table in result.get('skipped_tables') or []:
            skipped.append(table)
    return {'schema': 'wechat-database-accounting/1', 'scope': 'selected_snapshot',
            'authenticated': authenticated, 'database_count': len(results), 'status_counts': dict(sorted(statuses.items())),
            'failed_databases': sum(statuses[s] for s in ('failed', 'cancelled', 'not_started', 'not_processed_due_to_abort')),
            'excluded_derived_databases': sum(v for k, v in statuses.items() if k.startswith('excluded_')),
            'unclassified_table_count': len(unknown), 'unclassified_rows': sum(t['rows'] for t in unknown),
            'unclassified_tables': unknown, 'skipped_table_count': len(skipped), 'skipped_tables': skipped,
            'unclassified_schema_object_count': len(other_objects), 'unclassified_schema_objects': other_objects,
            'schema_coverage_complete': not unknown and not skipped and not other_objects,
            'normalization_status_counts': dict(Counter(r.get('normalization_status', 'unverified') for r in results)),
            'databases': [{'path': r.get('path'), 'status': r.get('status'),
                           'role': (r.get('schema_evidence') or {}).get('role')} for r in results]}
