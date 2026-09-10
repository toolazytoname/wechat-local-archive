"""Run with installed-venv/python -I; synthetic only and outside the checkout."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import wechat_export
from wechat_export.runtime import demo_export_dir

checkout = Path(__file__).resolve().parents[1]
assert not Path(wechat_export.__file__).resolve().is_relative_to(checkout), 'Imported checkout, not wheel'
assert sys.flags.isolated, 'Run with python -I'
from wechat_export.compatibility_registry import read_registry, REGISTRY_SCHEMA
registry = read_registry()
assert registry['schema'] == REGISTRY_SCHEMA and not registry.get('invalid'), 'Compatibility registry missing from wheel'
assert registry['entries'] == [], 'Preview must not silently declare real compatibility'
with tempfile.TemporaryDirectory(prefix='wla-installed-smoke-') as td:
    os.environ['WECHAT_EXPORT_DATA_ROOT'] = td
    root = demo_export_dir()
    assert root.resolve().is_relative_to(Path(td).resolve())
    rows = [json.loads(line) for line in (root / 'all/messages.jsonl').read_text().splitlines()]
    assert len(rows) == 12
    def command(*args):
        p = subprocess.run([sys.executable, '-I', '-m', 'wechat_export', *args],
                           cwd=td, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(p.stdout)
    args = ('slice', '--export-dir', str(root), '--all', '--mode', 'analysis')
    preview = command(*args, '--preview')
    exported = command(*args, '--source-revision', preview['source_binding']['source_revision'])
    assert exported['count'] == preview['count'] == 12
    assert exported['source_kind'] == 'live-db'
    assert exported['backup2_coverage'] == 'unverified'
    assert len(Path(exported['path']).read_text().splitlines()) == 12
    # Native registration is tested through its trusted local boundary here;
    # do not pop a real macOS chooser during CI or an unattended smoke.
    from wechat_export.output_locations import OutputLocations
    from wechat_export.runtime import resolve_runtime, resolve_source_id
    import shutil
    locations = OutputLocations(resolve_runtime(td))
    selected = Path(td) / 'synthetic-output'; selected.mkdir()
    item = locations.register_native_selection(selected)
    external = locations.resolve(item['destination_id']) / 'installed-demo'
    (external / 'all').mkdir(parents=True)
    shutil.copyfile(root / 'all/messages.jsonl', external / 'all/messages.jsonl')
    shutil.copyfile(root / 'manifest.json', external / 'manifest.json')
    source_id = locations.source_id(item['destination_id'], external.name)
    assert resolve_source_id(source_id, resolve_runtime(td)) == external.resolve()
    external_args = ('slice', '--export-dir', str(external), '--all', '--mode', 'analysis')
    external_preview = command(*external_args, '--preview')
    external_export = command(*external_args, '--source-revision', external_preview['source_binding']['source_revision'])
    assert external_export['count'] == 12
    assert Path(external_export['path']).is_relative_to(external)
    print('Installed wheel smoke: 12 synthetic messages, revision-bound export, registered external output, provenance OK')
