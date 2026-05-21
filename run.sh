#!/usr/bin/env bash
# run.sh — 包裝 venv 啟用，Claude Code skill 統一入口
# 用法（與 twitter_foryou_monitor.py 完全相同）：
#   bash run.sh --once --profile-name han
#   bash run.sh --loop 15 --all-profiles
#   bash run.sh --report
#   bash run.sh --login --profile-name han
#   bash run.sh --list-profiles

set -e
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$REPO_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ 虛擬環境不存在，請先執行：bash install.sh"
    exit 1
fi

exec "$VENV_PYTHON" "$REPO_DIR/twitter_foryou_monitor.py" "$@"
