#!/usr/bin/env python3
"""Create a local double-click app using this installed Python. No system changes."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile


def applescript_string(text):
    return '"'+text.replace('\\','\\\\').replace('"','\\"')+'"'


def create(config, output):
    config=config.expanduser().resolve(strict=True)
    # Validate configuration before creating an entry. Never embed chat/key data.
    values=json.loads(config.read_text())
    if not isinstance(values.get('runtime'),str):raise ValueError('runtime_required')
    output=output.expanduser().absolute()
    if output.exists() or output.is_symlink():raise FileExistsError('shortcut_already_exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    command=shlex.join([sys.executable,'-I','-m','wechat_export.desktop','--config',str(config)])
    source=('on run\nwith timeout of 240 seconds\ntry\ndo shell script '+applescript_string(command)+'\n'
            'on error problem\ndisplay dialog "暂时未能打开聊天档案。" & return & problem buttons {"好"} default button "好" with icon caution\n'
            'end try\nend timeout\nend run\n')
    with tempfile.TemporaryDirectory(prefix='.wechat-shortcut-',dir=output.parent) as td:
        target=Path(td)/output.name
        subprocess.run(['osacompile','-o',str(target),'-e',source],check=True,capture_output=True,timeout=60)
        info=target/'Contents/Info.plist';p=plistlib.loads(info.read_bytes())
        p['CFBundleName']='微信聊天档案';p['CFBundleDisplayName']='微信聊天档案'
        p['CFBundleIdentifier']='local.wechat.archive.desktop'
        info.write_bytes(plistlib.dumps(p))
        # Sign only the launcher we just generated, never Tencent's application.
        subprocess.run(['codesign','--force','--sign','-','--timestamp=none',str(target)],check=True,capture_output=True,timeout=60)
        subprocess.run(['codesign','--verify','--deep','--strict',str(target)],check=True,capture_output=True,timeout=30)
        output.mkdir(mode=0o700)
        os.replace(target,output)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(create(a.config,a.output))

if __name__=='__main__':main()
