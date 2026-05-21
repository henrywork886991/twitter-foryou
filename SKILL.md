---
name: twitter-foryou
description: >
  自動抓取 Twitter「為你推薦」feed，AI 評分篩選幣圈 alpha 信號，
  生成批次報告圖片並可推送 Telegram。
  Use when asked about:
  幫我生成推特日報, 推特日報, 抓推特信號, Twitter 報告, For You 報告,
  幫我滑推特, 幣圈推特熱點, 推特 alpha, 生成推特報告, 推特信號分析,
  Twitter For You, foryou, 推特日報生成, 幫我看推特
---

# Twitter For You Monitor — Skill 使用指南

## 前置條件確認

在執行任何操作前，先確認：

```bash
bash run.sh --list-profiles
```

- **有 profile** → 直接執行抓取流程
- **無 profile** → 提示使用者先登入（見「首次登入」）

---

## 主要流程（每次調用）

### 1. 抓取推文

```bash
# 所有已登入帳號一起跑（推薦）
bash run.sh --once --all-profiles

# 或指定單一帳號
bash run.sh --once --profile-name <名字>
```

### 2. 生成報告

抓取後系統會自動判斷：累積達 50 篇即自動生成報告。
如需立即生成（不等 50 篇）：

```bash
bash run.sh --report
```

### 3. 查看報告

```bash
# 啟動 localhost 報告頁面
source .venv/bin/activate && python view_report.py
# 或直接開啟 http://localhost:8765
```

---

## 首次登入（每人只做一次）

> ⚠️ 需要圖形介面（在本機執行，不在 Linux 伺服器上）

```bash
bash run.sh --login --profile-name <你的名字>
```

瀏覽器視窗開啟後登入 Twitter/X，看到 timeline 後自動儲存 session，關閉視窗即完成。

---

## Daemon 模式（背景持續執行）

```bash
# 每 15 分鐘自動抓取所有帳號（推薦間隔）
nohup bash run.sh --loop 15 --all-profiles >> logs/foryou.log 2>&1 &

# 查看 log
tail -f logs/foryou.log
```

---

## 輸出位置

| 輸出 | 路徑 |
|------|------|
| SQLite 資料庫 | `data/foryou.db` |
| 報告截圖 | `data/reports/foryou_report_<時間>.png` |
| Telegram 推送 | 設定 `.env` 裡的 `TG_BOT_TOKEN` + `TG_CHAT_ID` |

---

## 自然語言觸發範例

使用者說 → Claude 執行：

| 說法 | 對應操作 |
|------|----------|
| 幫我生成推特日報 | `--once --all-profiles` → `--report` |
| 抓一下推特信號 | `--once --all-profiles` |
| 強制出報告 | `--report` |
| 我要登入推特 | `--login --profile-name <詢問名字>` |
| 查看目前有哪些帳號 | `--list-profiles` |
| 開啟報告頁面 | `python view_report.py` |
| 開啟自動模式 | `--loop 15 --all-profiles` |
