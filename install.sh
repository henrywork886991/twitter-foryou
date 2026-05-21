#!/usr/bin/env bash
# install.sh — 一次性環境安裝
# 執行方式：bash install.sh

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"

echo "================================================"
echo "  ForYou Monitor — setup"
echo "================================================"
echo ""

# ── 偵測 python ───────────────────────────────────────────────────────────────
if command -v python3 &>/dev/null; then
    SYS_PYTHON=python3
elif command -v python &>/dev/null; then
    SYS_PYTHON=python
else
    echo "❌ Python 3 not found. Please install it first."
    exit 1
fi

# ── 1. 建立虛擬環境 ──────────────────────────────────────────────────────────
echo "[1/4] Setting up virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    $SYS_PYTHON -m venv "$VENV_DIR"
    echo "      ✅ Created .venv"
else
    echo "      ℹ️  .venv already exists — skipped"
fi

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# ── 2. Python 依賴 ──────────────────────────────────────────────────────────
echo ""
echo "[2/4] Installing Python dependencies..."
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r "$REPO_DIR/requirements.txt"
echo "      ✅ requests, python-dotenv, playwright"

# ── 3. Playwright Chromium ──────────────────────────────────────────────────
echo ""
echo "[3/4] Installing Playwright Chromium browser..."

OS="$(uname -s 2>/dev/null || echo Windows)"
if [ "$OS" = "Linux" ]; then
    echo "      Detected Linux — installing system dependencies (may need sudo)..."
    "$PYTHON" -m playwright install-deps chromium
fi

"$PYTHON" -m playwright install chromium
echo "      ✅ Chromium installed"

# ── 4. .env 檢查 ─────────────────────────────────────────────────────────────
echo ""
echo "[4/4] Checking .env..."
ENV_FILE="$REPO_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cp "$REPO_DIR/.env.example" "$ENV_FILE"
    echo "      ✅ Created .env from .env.example"
    echo "      ⚠️  Please fill in DEEPSEEK_API_KEY in .env"
else
    if grep -q "DEEPSEEK_API_KEY=sk-" "$ENV_FILE" 2>/dev/null; then
        echo "      ✅ DEEPSEEK_API_KEY found"
    else
        echo "      ⚠️  DEEPSEEK_API_KEY not set — edit .env before running"
    fi
fi

# ── 完成 ─────────────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  Setup complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. 啟用虛擬環境："
echo "     source .venv/bin/activate"
echo ""
echo "  2. 填入 API key："
echo "     edit .env  →  DEEPSEEK_API_KEY=sk-..."
echo ""
echo "  3. 登入 Twitter（每人做一次，需圖形介面）："
echo "     python twitter_foryou_monitor.py --login --profile-name <你的名字>"
echo ""
echo "  4. 確認 session："
echo "     python twitter_foryou_monitor.py --list-profiles"
echo ""
echo "  5. 開始抓取："
echo "     python twitter_foryou_monitor.py --once --profile-name <你的名字>"
echo ""
echo "  6. 查看報告（localhost:8765）："
echo "     python view_report.py"
echo ""
