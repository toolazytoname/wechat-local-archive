"""Native-user-selected output locations. Browser paths are never accepted.

This private registry is local convenience state, not a credential vault. Every
lookup revalidates device/inode; a detached/replaced volume is not recreated.
"""
from __future__ import annotations

import fcntl
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from wechat_export.http_security import HttpGuardError

TOKEN = re.compile(r'^[0-9a-f]{32}$')
SUBDIRECTORY = 'WeChat Local Archives'
_CHOOSER_LOCK = threading.Lock()
# Fixed script: no request values are interpolated or executed.
CHOOSER_SCRIPT = '''try
return POSIX path of (choose folder with prompt "选择聊天档案保存位置（将在此处新建 WeChat Local Archives）。不要选择同步到云端的文件夹。")
on error number -128
return ""
end try'''


def choose_native_folder() -> Path | None:
    if platform.system() != 'Darwin':
        raise HttpGuardError('原生目录选择仅支持 macOS。', 409, 'native_picker_unavailable')
    if not _CHOOSER_LOCK.acquire(blocking=False):
        raise HttpGuardError('已有目录选择窗口，请先完成或取消。', 409, 'picker_busy')
    try:
        try:
            result = subprocess.run(['/usr/bin/osascript', '-e', CHOOSER_SCRIPT],
                                    capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            raise HttpGuardError('目录选择超时，未改变保存位置。', 408, 'picker_timeout') from None
        if result.returncode:
            raise HttpGuardError('无法打开系统目录选择窗口，未改变保存位置。', 409, 'picker_failed')
        value = result.stdout.removesuffix('\n')
        if not value:
            return None
        if len(value) > 4096 or '\x00' in value or not Path(value).is_absolute():
            raise HttpGuardError('系统返回的目录无效。', 400, 'invalid_destination')
        return Path(value)
    finally:
        _CHOOSER_LOCK.release()


def directory_identity(path: Path) -> dict:
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError('destination_symlink_or_not_directory')
    return {'path': str(path.resolve(strict=True)), 'device': st.st_dev, 'inode': st.st_ino}


def check_identity(value: dict) -> Path:
    path = Path(value['path'])
    if directory_identity(path) != value:
        raise ValueError('destination_replaced')
    return path


class OutputLocations:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = runtime.private_root / 'output-locations.json'
        self.lock_path = runtime.private_root / 'output-locations.lock'

    def _read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {'schema': 1, 'locations': {}}
        with os.fdopen(fd, 'rb') as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid() or st.st_nlink != 1:
                raise ValueError('destination_registry_not_private')
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError('destination_registry_oversize')
        value = json.loads(data)
        if value.get('schema') != 1 or not isinstance(value.get('locations'), dict):
            raise ValueError('invalid_destination_registry')
        return value

    def register_native_selection(self, selected: Path) -> dict:
        """Trusted caller only; never expose a POST body -> this method path."""
        if selected.is_symlink():
            raise ValueError('destination_symlink')
        selected = selected.resolve(strict=True)
        directory_identity(selected)
        # Refuse source/private trees and obvious system-owned locations.
        protected = [self.runtime.private_root.resolve(),
                     (Path.home() / 'Library/Containers/com.tencent.xinWeChat').resolve(),
                     (self.runtime.data_root / 'raw').resolve(),
                     (self.runtime.data_root / 'work').resolve(),
                     Path('/Applications'), Path('/System'), Path('/Library')]
        if any(selected.is_relative_to(p) for p in protected):
            raise ValueError('protected_destination')
        root = selected / SUBDIRECTORY
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        binding = directory_identity(root)
        st = root.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError('destination_requires_private_permissions')
        # Also exclude descendants when the user chose a folder inside another
        # Git checkout. Never overwrite an existing ignore file or follow a link.
        ignore = root / '.gitignore'
        try:
            ignore_fd = os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            ignore_fd = os.open(ignore, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(ignore_fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                    raise ValueError('destination_ignore_invalid')
                if stream.read(4097).strip() != b'*':
                    raise ValueError('destination_ignore_conflict')
        else:
            with os.fdopen(ignore_fd, 'w') as stream:
                stream.write('*\n')
        # Probe only an owned disposable file, never chmod the selected folder.
        fd, name = tempfile.mkstemp(prefix='.wla-write-check-', dir=root)
        os.close(fd); os.unlink(name)
        lockfd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            st = os.fstat(lockfd)
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid() or st.st_nlink != 1:
                raise ValueError('destination_registry_lock_invalid')
            fcntl.flock(lockfd, fcntl.LOCK_EX)
            data = self._read()
            for token, item in data['locations'].items():
                if item == binding:
                    return self.describe(token)
            if len(data['locations']) >= 64:
                raise ValueError('destination_registry_full')
            token = uuid.uuid4().hex
            data['locations'][token] = binding
            fd, name = tempfile.mkstemp(prefix='.output-locations-', dir=self.runtime.private_root)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(data, stream)
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)
            return self.describe(token)
        finally:
            os.close(lockfd)

    def binding(self, token='default') -> dict:
        if token == 'default':
            return directory_identity(self.runtime.exports_root.resolve())
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError('invalid_destination_id')
        item = self._read()['locations'].get(token)
        if not isinstance(item, dict):
            raise ValueError('unknown_destination_id')
        check_identity(item)
        return item

    def resolve(self, token='default') -> Path:
        return check_identity(self.binding(token))

    def describe(self, token='default') -> dict:
        root = self.resolve(token)
        return {'destination_id': token, 'path': str(root),
                'name': '默认本机数据目录' if token == 'default' else root.parent.name,
                'free_bytes': shutil.disk_usage(root).free, 'available': True}

    def list(self) -> list[dict]:
        result = [self.describe()]
        for token, item in self._read()['locations'].items():
            try:
                result.append(self.describe(token))
            except (OSError, ValueError):
                result.append({'destination_id': token, 'name': Path(item.get('path', '')).parent.name,
                               'available': False, 'reason': '位置离线或目录已被替换，请重新选择。'})
        return result

    @staticmethod
    def source_id(token: str, name: str) -> str:
        return f'export:{name}' if token == 'default' else f'external:{token}:{name}'
