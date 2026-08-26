#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

bash "$ROOT/setup_environment.sh"
echo
echo "首次使用前，请先在 Canva Developer Portal 创建 integration："
echo "  Scope: design:content:write"
echo "  Redirect: http://127.0.0.1:8765/oauth/callback"
echo
read -r -p "请输入 Canva Client ID: " CANVA_SETUP_CLIENT_ID
if [[ -z "$CANVA_SETUP_CLIENT_ID" ]]; then
  echo "Client ID 不能为空。" >&2
  exit 2
fi

"$ROOT/.venv/bin/python" -m canva_connect configure --client-id "$CANVA_SETUP_CLIENT_ID"
"$ROOT/.venv/bin/python" -m canva_connect login
echo
echo "Canva 已连接。之后可在运营看板点击“发送到 Canva”。"
read -r -p "按回车键关闭窗口。" _
