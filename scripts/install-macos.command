#!/bin/zsh
set -eu
ROOT="${0:A:h:h}"
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo '需要 Python 3.11+。请先安装或通过 PYTHON 指定；本脚本不会自动安装系统工具。' >&2
  exit 1
fi
exec "$PYTHON" "$ROOT/scripts/bootstrap.py" install "$@"
