#!/usr/bin/env bash
# V4 daily generation workflow. A1/A2 account reads are separate, operator-triggered actions.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$ROOT/data/run_$(date +%F).log"
PYTHON="$ROOT/.venv/bin/python"
cd "$ROOT"

mkdir -p "$ROOT/data"
if [[ ! -x "$PYTHON" ]]; then
  echo "未找到项目环境。请先双击 start_dashboard.command，或执行 bash setup_environment.sh。" >&2
  exit 2
fi
echo "[$(date +%H:%M:%S)] local daily workflow started" | tee -a "$LOG"
"$PYTHON" -m personal_ops.cli "$@" >>"$LOG" 2>&1
STATUS=$?
echo "[$(date +%H:%M:%S)] local daily workflow finished status=$STATUS" | tee -a "$LOG"
exit "$STATUS"
