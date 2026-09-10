#!/bin/zsh
set -eu
ROOT="${0:A:h:h}"
BASE="${WLA_INSTALL_ROOT:-$HOME/Library/Application Support/wechat-local-archive}"
BASE="${BASE:A}"
if [[ -L "$BASE/current" ]]; then
  TARGET="${BASE}/current"
  TARGET="${TARGET:A}"
  if [[ "${TARGET:h}" != "$BASE/versions" ]]; then
    echo '当前安装指针无效，未启动。' >&2
    exit 1
  fi
  ID="${TARGET:t}"
  if [[ ${#ID} -ne 32 || "$ID" == *[^0-9a-f]* ]]; then
    echo '当前安装版本标识无效，未启动。' >&2
    exit 1
  fi
  PY="$TARGET/bin/python"
elif [[ -e "$BASE/current" ]]; then
  echo 'current 被其他文件占用，未启动。' >&2
  exit 1
else
  PY="$BASE/venv/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  echo '请先运行 install-macos.command。' >&2
  exit 1
fi
exec "$PY" -I "$ROOT/scripts/bootstrap.py" launch --app-support "$BASE" -- "$@"
