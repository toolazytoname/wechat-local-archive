"""Metadata-only space estimates, never a promise that expansion is bounded."""
from __future__ import annotations
import os
import shutil
import stat
from pathlib import Path

GIB = 1024 ** 3


def tree_bytes(root: Path, limit=100000) -> int:
    if root.is_symlink() or not root.is_dir():
        raise ValueError('storage_source_unavailable')
    total = count = 0
    def onerror(exc):
        raise exc
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        for name in dirs:
            if (Path(parent) / name).is_symlink():
                raise ValueError('storage_source_symlink')
        for name in files:
            count += 1
            if count > limit:
                raise ValueError('storage_estimate_limit')
            st = (Path(parent) / name).lstat()
            if not stat.S_ISREG(st.st_mode):
                raise ValueError('storage_source_not_regular')
            total += st.st_size
    return total


def estimate_storage(*, source: Path, work_root: Path, output_root: Path, app_bytes=0) -> dict:
    source_bytes = tree_bytes(source)
    work = 6 * source_bytes + app_bytes + GIB
    output = 6 * source_bytes + GIB
    same = work_root.stat().st_dev == output_root.stat().st_dev
    work_free = shutil.disk_usage(work_root).free
    output_free = shutil.disk_usage(output_root).free
    enough = work_free >= (work + output if same else work) and output_free >= (work + output if same else output)
    return {'source_bytes': source_bytes, 'work_estimate_bytes': work,
            'output_estimate_bytes': output, 'work_free_bytes': work_free,
            'output_free_bytes': output_free, 'same_filesystem': same,
            'estimate_satisfied': enough, 'estimate_only': True,
            'note': '按源数据库大小的倍数预留，并非上限保证；解析膨胀、已有文件和其他程序写入会影响实际空间。移动硬盘只保存最终档案，快照、密钥及解密工作仍留在本机。'}
