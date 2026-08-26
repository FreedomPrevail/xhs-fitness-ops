#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PYTHON="$VENV/bin/python"

cd "$ROOT"

if [[ ! -x "$PYTHON" ]]; then
  command -v python3 >/dev/null 2>&1 || {
    echo "未找到 Python 3。请先安装 Python 3.10 或更高版本。" >&2
    exit 2
  }
  echo "正在创建项目专用 Python 环境……"
  python3 -m venv "$VENV"
fi

if ! "$PYTHON" -c "import flask, yaml, PIL, jieba, jinja2, playwright, pptx, keyring" >/dev/null 2>&1; then
  echo "正在安装项目依赖（首次启动需要联网）……"
  "$PYTHON" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt"
fi

echo "环境检查完成：$PYTHON"
