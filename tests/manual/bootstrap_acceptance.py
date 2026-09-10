"""Opt-in offline installer integration. Installs only to an owned temp root.

Use pre-existing local compatible wheels, not a WeChat account or system venv:
python -m tests.manual.bootstrap_acceptance --wheel PATH --wheelhouse DIR --run-isolated-install
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',required=True,type=Path)
    parser.add_argument('--wheelhouse',required=True,type=Path)
    parser.add_argument('--run-isolated-install',action='store_true')
    args=parser.parse_args()
    if not args.run_isolated_install:
        parser.error('explicit --run-isolated-install is required')
    checkout=Path(__file__).resolve().parents[2]
    root=Path(tempfile.mkdtemp(prefix='wla-bootstrap-suite-')).resolve()
    support=root/'Installed application with spaces'
    logs=root/'logs';logs.mkdir(mode=0o700)
    base=[sys.executable,str(checkout/'scripts/bootstrap.py'),'install','--app-support',str(support),
          '--offline','--wheelhouse',str(args.wheelhouse.resolve()),'--no-launch']
    env=dict(os.environ,HTTP_PROXY='http://127.0.0.1:1',HTTPS_PROXY='http://127.0.0.1:1',NO_PROXY='')
    def run(label,flags,success=True):
        result=subprocess.run([*base,*flags],cwd=root,env=env,input='',capture_output=True,text=True,timeout=240)
        path=logs/(label+'.log');path.write_text(result.stdout+result.stderr);path.chmod(0o600)
        assert (result.returncode==0)==success, label
        return json.loads(result.stdout.splitlines()[-1]) if success else None
    source=['--source',str(args.wheel.resolve())]
    cancelled=run('declined',source)
    assert cancelled['status']=='cancelled' and not support.exists()
    first=run('first-install',[*source,'--yes'])
    assert first['status']=='installed'
    first_dir=support/'versions'/first['install_id']
    assert (support/'current').resolve()==first_dir
    retained=support/'private';retained.mkdir(mode=0o700)
    marker=retained/'synthetic-retention-marker.txt';marker.write_text('SYNTHETIC RETAIN THIS FILE')
    bad=root/'broken-0.0.0-py3-none-any.whl';bad.write_bytes(b'SYNTHETIC INVALID WHEEL')
    run('failed-update',['--source',str(bad),'--yes'],success=False)
    assert (support/'current').resolve()==first_dir
    second=run('second-install',[*source,'--yes'])
    second_dir=support/'versions'/second['install_id']
    assert second_dir!=first_dir and (support/'current').resolve()==second_dir
    assert second['previous_install_id']==first['install_id']
    # Venv scripts retain their real creation path; there was no venv directory move.
    result=subprocess.run([str(second_dir/'bin/pip'),'--version'],cwd=root,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0 and str(second_dir) in result.stdout
    launch=subprocess.run([str(checkout/'scripts/launch-macos.command'),'--help'],cwd=root,
                          env=dict(env,WLA_INSTALL_ROOT=str(support)),capture_output=True,text=True,timeout=30)
    assert launch.returncode==0 and 'wechat-export launch' in launch.stdout
    reverted=run('rollback',['--rollback','--yes'])
    assert reverted['install_id']==first['install_id'] and (support/'current').resolve()==first_dir
    assert second_dir.is_dir() and first_dir.is_dir()
    assert marker.read_text()=='SYNTHETIC RETAIN THIS FILE'
    receipts=[json.loads(p.read_text()) for p in support.glob('versions/*/install-receipt.json')]
    assert sorted(r['state'] for r in receipts)==['failed','ready','ready']
    for path in (first_dir,second_dir):
        report=json.loads((path/'smoke.log').read_text().splitlines()[-1])
        assert report['synthetic_records']==12 and report['provenance']
    report={'synthetic':True,'offline_local_wheels':True,'decline_no_write':True,'failed_update_preserved_current':True,
            'two_real_venvs_checked':True,'console_shebang_valid':True,'launcher_help_passed':True,
            'rollback_retains_versions_and_private_marker':True,'evidence_directory':str(root)}
    (root/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':
    main()
