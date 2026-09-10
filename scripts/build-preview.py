#!/usr/bin/env python3
"""Build an explicit-file, deterministic local preview zip. Never uploads."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def pack(files: dict[str,bytes], output: Path) -> str:
    prefix='wechat-local-archive-preview/'
    for name in files:
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('invalid bundle member')
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}
    manifest={'schema':'wechat-preview-bundle/1','release_certified':False,
              'source_kind':'live-db','backup2_coverage':'unverified','files':hashes}
    files=dict(files)
    files['bundle-manifest.json']=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    files['SHA256SUMS']=(''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name,data in sorted(files.items()))).encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.preview-',dir=output.parent);os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for name,data in sorted(files.items()):
                info=zipfile.ZipInfo(prefix+name,date_time=(1980,1,1,0,0,0))
                info.create_system=3
                info.external_attr=(0o100755 if name.startswith('scripts/') and name.endswith(('.py','.command')) else 0o100644)<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data,compresslevel=9)
        digest=hashlib.sha256(Path(tmp).read_bytes()).hexdigest()
        # Exclusive final publication: never replace a different prior artifact.
        if output.exists():
            if output.is_symlink() or hashlib.sha256(output.read_bytes()).hexdigest()!=digest:
                raise FileExistsError('different preview artifact already exists')
        else:
            os.link(tmp,output)
        return digest
    finally:
        Path(tmp).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'dist/wechat-local-archive-preview.zip')
    args=parser.parse_args()
    sys.path.insert(0,str(ROOT))
    from wechat_export.privacy_audit import scan
    assets=json.loads((ROOT/'docs/public-asset-review.json').read_text())
    audit=scan(ROOT,scope='worktree',assets=assets)
    if not audit['rule_scan_passed']:
        raise RuntimeError('public worktree privacy rules failed; no bundle created')
    wheel=args.wheel.resolve()
    if not wheel.name.startswith('wechat_export-') or wheel.suffix!='.whl' or wheel.stat().st_size>32*1024*1024:
        raise ValueError('invalid wheel')
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('invalid wheel member')
            if name.startswith('wechat_export/'):
                if archive.read(name)!=(ROOT/name).read_bytes():
                    raise ValueError('wheel does not match worktree')
            elif not (name.startswith('wechat_export-') and '.dist-info/' in name):
                raise ValueError('unexpected wheel member')
        registry=json.loads(archive.read('wechat_export/compatibility-registry.json'))
        if registry.get('entries'):
            raise ValueError('this preview packager requires explicit review before packaging nonempty compatibility declarations')
    sources={'README.md':'scripts/preview-readme.md','LICENSE':'LICENSE',
             'docs/install-guide.md':'docs/install-guide.md',
             'docs/consumer-guide.md':'docs/consumer-guide.md',
             'docs/engineering-notes.md':'docs/engineering-notes.md',
             **{f'scripts/{name}':f'scripts/{name}' for name in ('bootstrap.py','install-macos.command','launch-macos.command','create-macos-shortcut.py')}}
    files={name:(ROOT/source).read_bytes() for name,source in sources.items()}
    files['dist/'+wheel.name]=wheel.read_bytes()
    digest=pack(files,args.output)
    print(json.dumps({'status':'local_preview_built','sha256':digest,'file_count':len(files)+2,
                      'release_certified':False,'uploaded':False}))


if __name__=='__main__':
    main()
