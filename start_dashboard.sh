#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Finder 双击 .command 时不会读取交互式 zsh 配置，因此在这里显式加载 nvm。
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
  # shellcheck source=/dev/null
  . "$NVM_DIR/nvm.sh"
  nvm use --silent default >/dev/null 2>&1 \
    || nvm use --silent 22.14.0 >/dev/null 2>&1 \
    || true
fi

if ! command -v opencli >/dev/null 2>&1; then
  echo "提示：未找到 opencli；看板与本地生成仍可使用，但小红书实时采集功能不可用。" >&2
  echo "需要连接功能时请安装 OpenCLI、Browser Bridge，并运行 opencli doctor。" >&2
fi

if command -v codex >/dev/null 2>&1; then
  export CODEX_CLI_PATH="$(command -v codex)"
else
  echo "提示：未找到 codex；DeepSeek/Base URL 路线仍可使用。" >&2
fi

bash "$ROOT/setup_environment.sh"

if [[ "${XHS_OPEN_BROWSER:-0}" == "1" ]]; then
  echo "正在启动运营看板：http://127.0.0.1:5000"
  echo "保持本窗口开启；需要停止时按 Control-C。"
  (
    sleep 2
    open -a "Google Chrome" "http://127.0.0.1:5000" \
      || open "http://127.0.0.1:5000"
  ) >/dev/null 2>&1 &
fi

exec "$ROOT/.venv/bin/python" "$ROOT/app.py"
