"""Double-click launcher: private singleton receipt, loopback health, no WeChat ops."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request


def _identity(pid):
    p=subprocess.run(['ps','-p',str(pid),'-o','lstart=,command='],capture_output=True,text=True,timeout=5)
    if p.returncode != 0: return None
    text=p.stdout.strip()
    # macOS venv Python execs its framework binary after Popen. Bind start time
    # and the exact worker arguments, not that transient interpreter spelling.
    marker=' -I -m wechat_export.desktop --worker '
    if marker in text:
        start=subprocess.run(['ps','-p',str(pid),'-o','lstart='],capture_output=True,text=True,timeout=5)
        if start.returncode != 0: return None
        return start.stdout.strip()+'|'+text[text.index(marker):]
    return text


def health(receipt):
    try:
        port=int(receipt['port'])
        if not 1 <= port <= 65535:return False
        request=urllib.request.Request(f'http://127.0.0.1:{port}/api/desktop-health',headers={'X-Desktop-Token':receipt['token']})
        # Explicitly bypass environment proxy settings for the local-only probe.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=2) as response:
            data=json.load(response)
        return data.get('desktop_pid')==receipt['pid'] and data.get('desktop_ready') is True
    except (OSError,ValueError,KeyError,TypeError):return False


def launch(config_path: Path, *, open_browser=True, wait_seconds=180):
    config_path=config_path.expanduser().resolve(strict=True)
    config=json.loads(config_path.read_text())
    archive=Path(config['archive']).expanduser().resolve(strict=True) if config.get('archive') else None
    runtime=Path(config['runtime']).expanduser().resolve()
    runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    state=runtime/'desktop';state.mkdir(mode=0o700,exist_ok=True)
    if state.is_symlink() or state.stat().st_mode & 0o077:raise ValueError('desktop_state_must_be_private')
    receipt_path=state/'service.json'
    with (state/'launch.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('正在启动，请稍等片刻，不必重复点击。') from None
        receipt=None
        if receipt_path.is_file():
            receipt=json.loads(receipt_path.read_text())
            if receipt.get('config') != str(config_path) or receipt.get('archive') != str(archive):
                raise RuntimeError('当前入口绑定了另一份档案，请先使用原入口。')
            if not health(receipt):
                identity=_identity(receipt['pid'])
                if identity and identity==receipt.get('process_identity'):
                    raise RuntimeError('服务仍在运行但暂未就绪。请稍后重试；没有重复启动或结束它。')
                receipt=None
        if receipt is None:
            from wechat_export.runtime import pick_loopback_port
            port=pick_loopback_port(int(config.get('port',8768)),span=50)
            token=secrets.token_urlsafe(32)
            env=dict(os.environ,WECHAT_EXPORT_DATA_ROOT=str(runtime),WLA_DESKTOP_TOKEN=token)
            env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
            args=[sys.executable,'-I','-m','wechat_export.desktop','--worker','--config',str(config_path),'--port',str(port)]
            with (state/'service.log').open('ab') as log:
                process=subprocess.Popen(args,cwd=Path.home(),env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            receipt={'schema':'wechat-desktop/1','pid':process.pid,'port':port,'token':token,
                     'process_identity':_identity(process.pid),'config':str(config_path),'archive':str(archive)}
            temp=state/'service.json.tmp';temp.write_text(json.dumps(receipt));temp.chmod(0o600);os.replace(temp,receipt_path)
            deadline=time.monotonic()+wait_seconds
            while not health(receipt):
                if process.poll() is not None:raise RuntimeError('启动失败，日志已保存在本机；未操作微信。')
                if time.monotonic()>=deadline:raise RuntimeError('档案仍在准备，服务继续运行。稍后再次双击即可，不会重复启动。')
                time.sleep(.3)
        url=f"http://127.0.0.1:{receipt['port']}/?home=1"
        if open_browser:
            subprocess.run(['open',url],check=True,timeout=15)
        return {'url':url,'pid':receipt['pid'],'ready':True}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--worker',action='store_true');p.add_argument('--port',type=int);p.add_argument('--no-open',action='store_true')
    a=p.parse_args();os.umask(0o077)
    if a.worker:
        from wechat_export.archive_server import serve
        from wechat_export.runtime import resolve_runtime
        config=json.loads(a.config.read_text())
        runtime=resolve_runtime(config['runtime'])
        serve(Path(config['archive']) if config.get('archive') else None,port=a.port,runtime=runtime)
    else:
        try:print(json.dumps(launch(a.config,open_browser=not a.no_open),ensure_ascii=False))
        except (OSError,ValueError,RuntimeError) as exc:
            print('未能打开聊天档案：'+str(exc),file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
