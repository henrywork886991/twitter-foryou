# ForYou Monitor

自動滑動 Twitter「為你推薦」feed，用 AI 分析每條推文的幣圈信號價值，
每累積 50 篇生成一份排名報告並截圖，可推送到 Telegram。

支援多帳號——每個人用自己的帳號登入，演算法差異天然產生多樣化信號源。

## 效果預覽

- 每次抓取：~25-35 篇新推文，耗時約 2-3 分鐘
- 每份報告：Top 10 信號排名 + 市場情緒 + 熱點幣種 + Narrative 分析
- 噪音過濾：本地預篩 + AI 評分，只保留幣圈相關內容

## 安裝

```bash
git clone https://github.com/henrywork886991/twitter-foryou.git
cd twitter-foryou
bash install.sh
```

`install.sh` 自動完成：建立 `.venv` → 安裝 Python 依賴 → 安裝 Playwright Chromium → 建立 `.env`

## 設定

編輯 `.env`，填入 AI API key（必填）：

```bash
# 支援 DeepSeek / Groq / 任何 OpenAI-compatible 服務
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_API_BASE=https://api.deepseek.com/v1

# 選填：報告圖片推送到 Telegram
TG_BOT_TOKEN=
TG_CHAT_ID=
```

## 使用流程

### Step 1 — 啟用虛擬環境

```bash
source .venv/bin/activate
```

### Step 2 — 登入 Twitter（每人做一次，需要圖形介面）

```bash
python twitter_foryou_monitor.py --login --profile-name <你的名字>
```

瀏覽器視窗開啟後登入 Twitter/X，看到 timeline 後自動儲存 session。

### Step 3 — 確認登入成功

```bash
python twitter_foryou_monitor.py --list-profiles
```

### Step 4 — 開始抓取

```bash
# 單次（測試用）
python twitter_foryou_monitor.py --once --profile-name <你的名字>

# 所有帳號
python twitter_foryou_monitor.py --once --all-profiles

# Daemon 模式（每 15 分鐘自動抓，建議間隔）
python twitter_foryou_monitor.py --loop 15 --all-profiles

# 強制立即生成報告（不等 50 篇）
python twitter_foryou_monitor.py --report
```

### Step 5 — 查看報告

```bash
python view_report.py
# 開啟 http://localhost:8765
```

## 部署到 Linux 伺服器

在本機登入後，把 session 上傳到伺服器：

```bash
scp -r data/profiles/<名字> user@server:/path/to/twitter-foryou/data/profiles/
```

伺服器上跑 daemon（headless 全程不需圖形介面）：

```bash
source .venv/bin/activate
nohup python twitter_foryou_monitor.py --loop 15 --all-profiles >> logs/foryou.log 2>&1 &
```

## 資料位置

| 路徑 | 說明 |
|------|------|
| `data/foryou.db` | SQLite 資料庫（推文 + 報告） |
| `data/profiles/<名字>/` | 各帳號的 Twitter session |
| `data/reports/*.png` | 批次報告截圖 |

## 環境變數完整列表

| 變數 | 必填 | 說明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | ✅ | AI API key |
| `DEEPSEEK_MODEL` | | 模型名稱，預設 `deepseek-chat` |
| `DEEPSEEK_API_BASE` | | API endpoint，預設 DeepSeek |
| `TG_BOT_TOKEN` | | Telegram Bot Token |
| `TG_CHAT_ID` | | Telegram 頻道 ID |
| `FORYOU_SCROLLS` | | 每次滾動次數，預設 `80` |
| `FORYOU_BATCH_SIZE` | | 觸發報告的推文數，預設 `50` |
| `FORYOU_DB` | | 自訂 DB 路徑 |
