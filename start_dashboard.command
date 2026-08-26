#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export XHS_OPEN_BROWSER=1
exec bash "$ROOT/start_dashboard.sh"
