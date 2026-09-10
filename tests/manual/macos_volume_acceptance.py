"""Opt-in real macOS mounted-image tests; only self-created synthetic data.

Run: python -m tests.manual.macos_volume_acceptance --run-synthetic-mounts
Never targets an existing disk, partition, WeChat directory, key or archive.
"""
from __future__ import annotations
import argparse
import json
import os
import platform
import plistlib
import subprocess
import tempfile
from pathlib import Path
from tests.test_read_pipeline import ReadPipelineTests
from wechat_export.output_locations import OutputLocations
from wechat_export.runtime import resolve_runtime, resolve_source_id
from wechat_export.storage_plan import estimate_storage
from wechat_export.read_pipeline import process_snapshot, PipelineError
from wechat_export.fsutil import sha256_file


class OwnedImage:
    def __init__(self, root: Path, name: str, size: str):
        self.root = root.resolve()
        self.image = self.root / (name + '.sparseimage')
        self.mount = self.root / 'mount'
        self.attached = False
        subprocess.run(['hdiutil', 'create', '-size', size, '-fs', 'APFS', '-volname', 'WLA-Synthetic',
                        '-type', 'SPARSE', str(self.image)], check=True, capture_output=True, timeout=120)

    def attach(self):
        self.mount.mkdir(exist_ok=True)
        if any(self.mount.iterdir()):
            raise RuntimeError('refuse_mount_over_nonempty_directory')
        self.attached = True  # Also clean up if attach times out after mounting.
        result = subprocess.run(['hdiutil', 'attach', str(self.image), '-mountpoint', str(self.mount),
                                 '-noautoopen', '-plist'], capture_output=True, check=True, timeout=90)
        self.attached = True
        entries = plistlib.loads(result.stdout)['system-entities']
        assert any(e.get('mount-point') == str(self.mount) for e in entries)
        assert self.mount.stat().st_dev != self.root.stat().st_dev
        return self.mount

    def detach(self, *, force=False):
        if not self.attached:
            return
        # Recheck the OS association before detaching, including during cleanup.
        images = plistlib.loads(subprocess.check_output(['hdiutil', 'info', '-plist'], timeout=20))['images']
        owned = [i for i in images if Path(i.get('image-path', '')).resolve() == self.image]
        if not owned:
            self.attached = False
            return
        assert len(owned) == 1
        assert any(e.get('mount-point') == str(self.mount) for e in owned[0]['system-entities'])
        subprocess.run(['hdiutil', 'detach', str(self.mount), *(['-force'] if force else [])],
                       capture_output=True, check=True, timeout=60)
        self.attached = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-synthetic-mounts', action='store_true')
    args = parser.parse_args()
    if not args.run_synthetic_mounts or platform.system() != 'Darwin':
        parser.error('explicit --run-synthetic-mounts on macOS is required')
    root = Path(tempfile.mkdtemp(prefix='wla-volume-suite-')).resolve()
    fixture = ReadPipelineTests()
    images = []
    checks = {}
    try:
        fixture.setUp()
        runtime = resolve_runtime(fixture.cfg.data_root)
        locations = OutputLocations(runtime)
        source_hashes = {p.relative_to(fixture.snapshot).as_posix(): sha256_file(p)
                         for p in fixture.snapshot.rglob('*.db')}
        small = OwnedImage(root, 'small', '256m'); images.append(small)
        selected = small.attach()
        token = locations.register_native_selection(selected)['destination_id']
        parent = locations.resolve(token)
        estimate = estimate_storage(source=fixture.snapshot, work_root=fixture.cfg.data_root, output_root=parent)
        assert not estimate['same_filesystem'] and not estimate['estimate_satisfied']
        try:
            process_snapshot(snapshot=fixture.snapshot, passphrase_file=fixture.key, cfg=fixture.cfg,
                             run_id='small-space', snapshot_id='synthetic', output_parent=parent,
                             validate_destination=lambda: locations.resolve(token))
        except PipelineError as exc:
            assert str(exc) == 'insufficient_estimated_space'
        else:
            raise AssertionError('small volume should block')
        assert not (fixture.cfg.work_root / 'small-space').exists()
        assert not (parent / 'small-space').exists()
        checks['real_small_volume_blocks_before_decryption'] = True
        small.detach()
        assert not next(i for i in locations.list() if i['destination_id'] == token)['available']
        checks['detached_location_unavailable'] = True

        large = OwnedImage(root, 'large', '4g'); images.append(large)
        selected = large.attach()
        # A replacement volume at the same mount path must not inherit authority.
        try:
            locations.resolve(token)
        except (OSError, ValueError):
            checks['same_mount_path_replacement_rejected'] = True
        else:
            raise AssertionError('old token authorized replacement volume')
        token = locations.register_native_selection(selected)['destination_id']
        parent = locations.resolve(token)
        assert parent.stat().st_dev != fixture.cfg.work_root.parent.stat().st_dev
        out = process_snapshot(snapshot=fixture.snapshot, passphrase_file=fixture.key, cfg=fixture.cfg,
                               run_id='cross-volume', snapshot_id='synthetic', output_parent=parent,
                               validate_destination=lambda: locations.resolve(token))
        manifest = json.loads((out / 'manifest.json').read_text())
        assert manifest['record_count'] == 5 and manifest['source_kind'] == 'live-db'
        assert manifest['backup2_coverage'] == 'unverified'
        assert out.stat().st_dev == selected.stat().st_dev
        assert not (fixture.cfg.exports_root / 'cross-volume').exists()
        assert resolve_source_id(locations.source_id(token, out.name), runtime) == out
        for name in ('coverage.json', 'attachment-ledger.jsonl', 'source-ledger.json', 'archive.sqlite'):
            assert (out / name).is_file()
        checks['real_cross_device_codec_to_index_and_export'] = True

        def disconnect(state, *_):
            if state == 'indexing':
                large.detach(force=True)
                checks['forced_disconnect_in_staging'] = True
        try:
            process_snapshot(snapshot=fixture.snapshot, passphrase_file=fixture.key, cfg=fixture.cfg,
                             run_id='interrupted', snapshot_id='synthetic', output_parent=parent,
                             progress=disconnect, validate_destination=lambda: locations.resolve(token))
        except (OSError, ValueError, RuntimeError):
            assert checks.get('forced_disconnect_in_staging')
        else:
            raise AssertionError('disconnected export published')
        # No fallback creation underneath a now-unmounted path on the host disk.
        assert not selected.exists() or not any(selected.iterdir())
        failure = json.loads((fixture.cfg.work_root / 'interrupted/source-ledger.json').read_text())
        assert failure['state'] == 'failed' and not failure['records_complete']
        assert source_hashes == {p.relative_to(fixture.snapshot).as_posix(): sha256_file(p)
                                 for p in fixture.snapshot.rglob('*.db')}
        checks['no_host_disk_fallback_and_sources_unchanged'] = True
        selected = large.attach()
        parent = selected / 'WeChat Local Archives'
        assert (parent / 'cross-volume/manifest.json').is_file()
        assert not (parent / 'interrupted/manifest.json').exists()
        checks['prior_completed_export_survives_reconnect'] = True
        report = {'synthetic': True, 'filesystem': 'APFS_disk_image', 'physical_external_disk_tested': False,
                  'checks': checks, 'record_count': 5}
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(dict(report, evidence_directory=str(root))))
    finally:
        cleanup_errors = []
        for image in reversed(images):
            try:
                image.detach(force=True)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        fixture.doCleanups()
        if cleanup_errors:
            raise RuntimeError('owned_image_cleanup_failed:' + ','.join(cleanup_errors))


if __name__ == '__main__':
    main()
