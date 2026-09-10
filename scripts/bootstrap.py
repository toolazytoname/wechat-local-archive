#!/usr/bin/env python3
"""Consent-gated local installation; immutable venv paths and atomic activation.

Standard library only. Does not install system tools, touch WeChat, remove old
versions, read archives, or change protection settings. Failed installs remain
private/inactive; an existing current or legacy installation is preserved.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ID = re.compile(r'^[0-9a-f]{32}$')
DEFAULT_ROOT = Path.home() / 'Library/Application Support/wechat-local-archive'
ROOT = Path(__file__).resolve().parents[1]


class InstallError(RuntimeError):
    pass


def private_directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise InstallError('安装目录不是本用户所有的安全目录；未修改其权限。')


def checked_root(path: Path, *, create=False) -> Path:
    path = path.expanduser().absolute()
    # Do not hide a symlink by resolving before inspecting its final component.
    if path.is_symlink():
        raise InstallError('安装目录不能是符号链接。')
    if create:
        private_directory(path)
    elif not path.is_dir():
        raise InstallError('尚未安装，请先运行 install-macos.command。')
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise InstallError('安装目录权限不安全；未修改目录。')
    return path.resolve()


def stamp(path: Path):
    st = path.lstat()
    return st.st_dev, st.st_ino


@contextlib.contextmanager
def installation_lock(root: Path):
    fd = os.open(root / '.install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_mode & 0o077:
            raise InstallError('安装锁文件不安全。')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError('已有安装或回退任务正在运行。') from None
        yield
    finally:
        os.close(fd)


def read_receipt(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid() or st.st_nlink != 1:
            raise InstallError('安装回执不安全。')
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise InstallError('安装回执过大。')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise InstallError('安装回执无效。')
    return value


def write_receipt(path: Path, value: dict):
    fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def resolve_version(root: Path, install_id: str) -> Path:
    if not isinstance(install_id, str) or not ID.fullmatch(install_id):
        raise InstallError('安装版本标识无效。')
    versions = root / 'versions'
    if versions.is_symlink():
        raise InstallError('版本目录不能是符号链接。')
    target = versions / install_id
    if target.is_symlink() or not target.is_dir() or target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o077:
        raise InstallError('安装版本目录无效。')
    receipt = read_receipt(target / 'install-receipt.json')
    if receipt.get('install_id') != install_id or receipt.get('state') != 'ready':
        raise InstallError('此版本未通过安装自检。')
    if not (target / 'bin/python').is_file():
        raise InstallError('此版本的 Python 不可用。')
    return target


def active_id(root: Path) -> str | None:
    pointer = root / 'current'
    if pointer.is_symlink():
        relative = os.readlink(pointer)
        parts = Path(relative).parts
        if len(parts) != 2 or parts[0] != 'versions' or not ID.fullmatch(parts[1]):
            raise InstallError('当前版本指针无效；不会改用其他目录。')
        resolve_version(root, parts[1])
        return parts[1]
    if pointer.exists():
        raise InstallError('current 已被其他文件占用，未覆盖。')
    legacy = root / 'venv'
    if legacy.is_symlink():
        raise InstallError('旧版环境不能是符号链接。')
    if (legacy / 'bin/python').is_file():
        return 'legacy'
    return None


def activate(root: Path, install_id: str, expected):
    if active_id(root) != expected:
        raise InstallError('当前安装已被其他操作修改，未切换。')
    resolve_version(root, install_id)
    pointer = root / ('.current-' + uuid.uuid4().hex)
    try:
        pointer.symlink_to(Path('versions') / install_id)
        os.replace(pointer, root / 'current')
    finally:
        pointer.unlink(missing_ok=True)


def consent(yes: bool, message: str) -> bool:
    print(message, flush=True)
    if yes:
        print('已收到明确的 --yes 确认。', flush=True)
        return True
    if not sys.stdin.isatty():
        print('未收到确认：未安装、未联网、未切换版本。交互运行或明确传入 --yes。')
        return False
    return input('确认继续？输入 yes，其余输入取消：').strip().lower() == 'yes'


def file_digest(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


SMOKE = r'''
import json, pathlib, sys
import wechat_export, zstandard
from Crypto.Cipher import AES
from wechat_export.compatibility_registry import read_registry
from wechat_export.runtime import demo_export_dir
from wechat_export.archive_index import build_index
from wechat_export.export_service import QuerySpec, write_slice
package = pathlib.Path(wechat_export.__file__).resolve().parent
assert pathlib.Path(sys.prefix).resolve() in package.parents
for name in ('index.html','styles.css','app.js','setup.js','interactions.js'):
    assert (package/'static'/name).is_file(), name
assert not read_registry().get('invalid')
root = demo_export_dir()
assert root is not None and pathlib.Path(sys.argv[1]).resolve() in root.resolve().parents
build_index(root)
result = write_slice(None,root,QuerySpec.from_mapping({'scope':{'kind':'all'},'format':'jsonl','mode':'analysis'}),source='canonical')
assert result['count'] == 12
assert result['source_kind']=='live-db' and result['backup2_coverage']=='unverified'
assert result['selection_accounting']['candidate_count']==12
print(json.dumps({'installed_package':True,'synthetic_records':12,'assets':True,'provenance':True}))
'''


def run_step(command: list[str], *, log: Path, cwd: Path, env: dict, timeout: int):
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=timeout)


def install(args, *, runner=run_step) -> dict:
    if sys.version_info < (3, 11):
        raise InstallError('需要 Python 3.11 或更高版本；不会自动安装 Python 或修改系统。')
    source = Path(args.source).expanduser().resolve()
    if not source.exists() or (source.is_file() and source.suffix != '.whl'):
        raise InstallError('安装来源必须是本机项目目录或 wheel 文件。')
    wheelhouse = Path(args.wheelhouse).expanduser().resolve() if args.wheelhouse else None
    if args.offline and (not wheelhouse or not wheelhouse.is_dir()):
        raise InstallError('离线安装需要本机 --wheelhouse 目录。')
    if args.offline and not source.is_file():
        raise InstallError('离线安装只接受已构建的本地 wheel，不执行源码构建。')
    local_wheels = sorted(wheelhouse.glob('*.whl')) if args.offline else []
    if args.offline and not local_wheels:
        raise InstallError('本机 wheelhouse 没有依赖 wheel 文件。')
    source_hash = file_digest(source) if source.is_file() else None
    network = '仅使用指定本地 wheelhouse，不查询包索引。' if args.offline else 'pip 可能联网下载 Python 构建工具和依赖。'
    if not consent(args.yes, f'将在 {args.app_support} 创建独立版本环境。\n来源：{source}\n{network}\n只安装本工具；不安装 Homebrew/SQLCipher/Xcode，不修改微信或系统权限。失败保留旧安装。'):
        return {'status': 'cancelled'}
    root = checked_root(Path(args.app_support), create=True)
    with installation_lock(root):
        original = active_id(root)
        root_stamp = stamp(root)
        private_directory(root / 'versions')
        versions_stamp = stamp(root / 'versions')
        install_id = uuid.uuid4().hex
        target = root / 'versions' / install_id
        target.mkdir(mode=0o700)
        receipt = {'schema':1, 'install_id':install_id, 'state':'installing', 'previous_install_id':original,
                   'source_kind':'wheel' if source_hash else 'local_source_tree', 'source_sha256':source_hash,
                   'python_version':list(sys.version_info[:3]), 'offline':args.offline}
        receipt_path = target / 'install-receipt.json'
        write_receipt(receipt_path, receipt)
        env = dict(os.environ)
        env.pop('PYTHONHOME', None); env.pop('PYTHONPATH', None)
        env['PYTHONNOUSERSITE'] = '1'
        env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
        env['PIP_CONFIG_FILE'] = os.devnull
        env['WECHAT_EXPORT_DATA_ROOT'] = str(target / 'smoke-data')
        step = 'creating_environment'
        try:
            print('创建新环境（旧版仍可运行）…', flush=True)
            runner([sys.executable, '-I', '-m', 'venv', str(target)], log=target/'venv.log', cwd=root, env=env, timeout=180)
            python = str(target / 'bin/python')
            step = 'installing_package'
            command = [python, '-I', '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check', '--no-input']
            if args.offline:
                # Explicit wheel paths + no dependency resolution: a wheel's direct
                # URL dependency cannot make pip download behind --no-index.
                command += ['--no-index', '--no-deps', *[str(p) for p in local_wheels if p.resolve()!=source]]
            command.append(str(source))
            print('安装到新环境…', flush=True)
            runner(command, log=target/'pip.log', cwd=root, env=env, timeout=1200)
            step = 'dependency_check'
            runner([python, '-I', '-m', 'pip', '--isolated', 'check'],
                   log=target/'dependency-check.log', cwd=root, env=env, timeout=60)
            step = 'installed_smoke'
            print('运行隔离的合成档案自检…', flush=True)
            runner([python, '-I', '-c', SMOKE, str(target/'smoke-data')],
                   log=target/'smoke.log', cwd=root, env=env, timeout=120)
            if source_hash and file_digest(source) != source_hash:
                raise InstallError('安装期间 wheel 文件发生变化，未激活。')
            if stamp(root) != root_stamp or stamp(root/'versions') != versions_stamp:
                raise InstallError('安装目录被替换，未激活。')
            receipt.update(state='ready', smoke='passed')
            write_receipt(receipt_path,receipt)
            step = 'activation'
            activate(root,install_id,original)
            return {'status':'installed','install_id':install_id,'previous_install_id':original,
                    'old_versions_removed':False,'root':str(root)}
        except BaseException as exc:
            # Atomic pointer replacement is the commit point. An interrupt just
            # afterward must not mark the now-active, validated receipt failed.
            if step == 'activation':
                try:
                    if active_id(root) == install_id:
                        return {'status':'installed','install_id':install_id,'previous_install_id':original,
                                'old_versions_removed':False,'root':str(root),'activation_interrupted_after_commit':True}
                except (OSError, ValueError, InstallError):
                    raise InstallError('版本切换状态无法确认；保留已验证回执和旧环境，请检查后重试。') from None
            # Local build logs stay private; no arbitrary exception text in the public result.
            receipt.update(state='failed', failed_step=step, error_type=type(exc).__name__)
            write_receipt(receipt_path,receipt)
            if isinstance(exc, KeyboardInterrupt):
                raise InstallError('安装已取消；旧环境未删除。') from None
            raise InstallError(f'安装失败（{step}）；未激活新环境。日志保留在新版本目录，旧环境未删除。') from None


def rollback(args) -> dict:
    root = checked_root(Path(args.app_support))
    if not consent(args.yes, '将切换到当前安装回执记录的上一版本。不会删除任何环境或档案。'):
        return {'status':'cancelled'}
    with installation_lock(root):
        current = active_id(root)
        if not current or current == 'legacy':
            raise InstallError('没有可回退的受管安装。')
        previous = read_receipt(resolve_version(root,current)/'install-receipt.json').get('previous_install_id')
        if previous == 'legacy':
            legacy = root/'venv'
            if legacy.is_symlink() or not (legacy/'bin/python').is_file():
                raise InstallError('原有旧版环境不可用，未切换。')
            (root/'current').unlink()
        elif previous:
            activate(root,previous,current)
        else:
            raise InstallError('这是首个安装，没有上一版本。')
        return {'status':'rolled_back','install_id':previous,'old_versions_removed':False}


def launch(args):
    root = checked_root(Path(args.app_support))
    current = active_id(root)
    if not current:
        raise InstallError('尚未安装，请先运行 install-macos.command。')
    target = root/'venv' if current == 'legacy' else resolve_version(root,current)
    env = dict(os.environ)
    env.pop('PYTHONHOME', None); env.pop('PYTHONPATH', None)
    env['WECHAT_EXPORT_APP_SUPPORT'] = str(root)
    forwarded = args.launch_args
    if forwarded[:1] == ['--']:
        forwarded = forwarded[1:]
    os.chdir(Path.home())
    os.execve(target/'bin/python', [str(target/'bin/python'), '-I', '-m', 'wechat_export', 'launch', *forwarded], env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action',required=True)
    add = commands.add_parser('install')
    bundled = sorted((ROOT/'dist').glob('wechat_export-*.whl'))
    default_source = ROOT if (ROOT/'pyproject.toml').is_file() else (bundled[0] if len(bundled)==1 else ROOT)
    add.add_argument('--source', default=str(default_source))
    add.add_argument('--app-support', default=str(DEFAULT_ROOT))
    add.add_argument('--yes', action='store_true')
    add.add_argument('--offline', action='store_true')
    add.add_argument('--wheelhouse')
    add.add_argument('--no-launch', action='store_true')
    add.add_argument('--rollback', action='store_true')
    start = commands.add_parser('launch')
    start.add_argument('--app-support', default=str(DEFAULT_ROOT))
    start.add_argument('launch_args',nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.action == 'launch':
            launch(args)
            return 0
        result = rollback(args) if args.rollback else install(args)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if result['status']=='cancelled':
            return 0
        if not args.no_launch:
            args.launch_args=[]
            launch(args)
        return 0
    except (InstallError, OSError, ValueError) as exc:
        print('未完成：'+(str(exc) if isinstance(exc,InstallError) else type(exc).__name__),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
