"""
twitter_foryou_monitor.py — 抓取 Twitter「為你推薦」feed，逐推文 AI 分析，
每累積 50 篇生成一份排名總結報告並渲染成圖片。

支援多帳號（Multi-Session）：
  每個人用自己的 Twitter 帳號登入，各自的「為你推薦」feed 因演算法不同
  而看到不同內容，合併後資料多樣性大幅提升。

  # 1. 各自在本機登入（每人做一次，需要圖形介面）
  python twitter_foryou_monitor.py --login --profile-name han
  python twitter_foryou_monitor.py --login --profile-name alice

  # 2. 抓取時指定帳號
  python twitter_foryou_monitor.py --once --profile-name han

  # 3. 一次跑所有已登入的帳號
  python twitter_foryou_monitor.py --once --all-profiles

  # 4. 強制批次報告（合併所有帳號資料）
  python twitter_foryou_monitor.py --report

  # 5. Daemon 模式（每 15 分鐘全部帳號輪一次）
  python twitter_foryou_monitor.py --loop 15 --all-profiles

Profile 儲存位置：
  data/profiles/<profile-name>/browser-profile/  ← 各帳號的 Cookies/session
  data/foryou.db                                  ← 統一 SQLite（含 fetched_by 欄位）

Linux 無頭部署：
  日常抓取全程 headless，不需 DISPLAY。
  登入步驟（--login）需在本機（有圖形介面）做一次，
  然後把 profile 目錄 scp 到 Linux 伺服器即可。

環境變數（.env 或 export）：
  DEEPSEEK_API_KEY      — AI API key（必填，支援 DeepSeek / Groq 等 OpenAI-compatible）
  DEEPSEEK_MODEL        — 模型名稱，預設 deepseek-chat
  DEEPSEEK_API_BASE     — API endpoint，預設 https://api.deepseek.com/v1
  FORYOU_SCROLLS        — 每次滾動次數，預設 80（約 25-35 篇推文）
  FORYOU_BATCH_SIZE     — 累積幾篇觸發批次報告，預設 50
  FORYOU_DB             — SQLite 路徑（留空使用預設 data/foryou.db）
  TG_BOT_TOKEN          — 發送報告圖片到 Telegram（選填）
  TG_CHAT_ID            — Telegram 目標頻道 chat_id（選填）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── 路徑（所有資料存在 repo 根目錄的 data/ 下）────────────────────────────────
_ROOT         = Path(__file__).parent
_DATA_DIR     = _ROOT / "data"
_PROFILES_DIR = _DATA_DIR / "profiles"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_PROFILES_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(_ROOT / ".env")

log = logging.getLogger("foryou")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ── AI API 設定（DEEPSEEK_* 優先，也可用任何 OpenAI-compatible 服務）──────────
_DS_KEY   = (os.environ.get("DEEPSEEK_API_KEY")
             or os.environ.get("AI_API_KEY", ""))
_DS_MODEL = (os.environ.get("DEEPSEEK_MODEL")
             or os.environ.get("AI_MODEL", "deepseek-chat"))
_DS_BASE  = (os.environ.get("DEEPSEEK_API_BASE")
             or os.environ.get("AI_API_BASE", "https://api.deepseek.com/v1")).rstrip("/")

_SCROLLS    = int(os.environ.get("FORYOU_SCROLLS", "80"))
_BATCH_SIZE = int(os.environ.get("FORYOU_BATCH_SIZE", "50"))
_DB_PATH    = Path(os.environ.get("FORYOU_DB", str(_DATA_DIR / "foryou.db")))

_TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
_TG_CHAT  = os.environ.get("TG_CHAT_ID", "")


# ══════════════════════════════════════════════════════════════════════════════
# 1. Profile 管理
# ══════════════════════════════════════════════════════════════════════════════

def _profile_dir(name: str = "default") -> Path:
    d = _PROFILES_DIR / name / "browser-profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _has_session(profile_dir: Path) -> bool:
    return (
        (profile_dir / "Default" / "Network" / "Cookies").exists()
        or (profile_dir / "Default" / "Cookies").exists()
    )


def _all_profile_names() -> list[str]:
    names = []
    if _PROFILES_DIR.exists():
        for p in sorted(_PROFILES_DIR.iterdir()):
            if p.is_dir() and _has_session(p / "browser-profile"):
                names.append(p.name)
    return names


# ══════════════════════════════════════════════════════════════════════════════
# 2. AI API（OpenAI-compatible，支援 DeepSeek / Groq / 其他）
# ══════════════════════════════════════════════════════════════════════════════

def _ds_chat(prompt: str, max_tokens: int = 1024, _retries: int = 4) -> str:
    """呼叫 OpenAI-compatible API，429 自動退讓重試。"""
    if not _DS_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not set in .env")
    for attempt in range(_retries):
        # Qwen3 系列需要 /no_think 抑制思考塊，避免破壞 JSON 解析
        _prompt = prompt + "\n/no_think" if "qwen3" in _DS_MODEL.lower() else prompt
        resp = requests.post(
            f"{_DS_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {_DS_KEY}", "Content-Type": "application/json"},
            json={
                "model": _DS_MODEL,
                "messages": [{"role": "user", "content": _prompt}],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if resp.status_code == 429 and attempt < _retries - 1:
            wait = int(resp.headers.get("retry-after", 15)) + 2
            log.warning("API 429 rate limit — waiting %ds (attempt %d/%d)", wait, attempt + 1, _retries)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    resp.raise_for_status()
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# 3. SQLite
# ══════════════════════════════════════════════════════════════════════════════

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS foryou_tweets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT UNIQUE NOT NULL,
            user        TEXT,
            text        TEXT,
            tweet_time  TEXT,
            fetched_at  TEXT NOT NULL,
            score       INTEGER DEFAULT 0,
            signal      TEXT DEFAULT 'noise',
            coins       TEXT DEFAULT '[]',
            summary     TEXT,
            batched     INTEGER DEFAULT 0,
            fetched_by  TEXT DEFAULT 'default'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS batch_reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT NOT NULL,
            tweet_count INTEGER,
            report_json TEXT,
            image_path  TEXT
        )
    """)
    conn.commit()
    return conn


def _seen_urls() -> set[str]:
    with _db() as conn:
        rows = conn.execute("SELECT url FROM foryou_tweets").fetchall()
        return {r["url"] for r in rows}


def _save_tweet(tweet: dict, analysis: dict, profile_name: str = "default") -> None:
    coins_json = json.dumps(analysis.get("coins", []), ensure_ascii=False)
    with _db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO foryou_tweets
              (url, user, text, tweet_time, fetched_at, score, signal, coins, summary, fetched_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            tweet["link"],
            tweet.get("user", ""),
            tweet.get("text", "")[:800],
            tweet.get("time", ""),
            datetime.now(timezone.utc).isoformat(),
            analysis.get("score", 0),
            analysis.get("signal", "noise"),
            coins_json,
            analysis.get("summary", ""),
            profile_name,
        ))
        conn.commit()


def _unbatched_count() -> int:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM foryou_tweets WHERE batched=0").fetchone()[0]


def _get_unbatched(limit: int = 50) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM foryou_tweets WHERE batched=0 ORDER BY score DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def _mark_batched(ids: list[int]) -> None:
    with _db() as conn:
        conn.executemany(
            "UPDATE foryou_tweets SET batched=1 WHERE id=?",
            [(i,) for i in ids]
        )
        conn.commit()


def _save_batch_report(tweet_count: int, report: dict, image_path: str) -> None:
    with _db() as conn:
        conn.execute("""
            INSERT INTO batch_reports (created_at, tweet_count, report_json, image_path)
            VALUES (?,?,?,?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            tweet_count,
            json.dumps(report, ensure_ascii=False),
            image_path,
        ))
        conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 4. Playwright 抓取「為你推薦」
# ══════════════════════════════════════════════════════════════════════════════

_SCAN_JS = r"""
(() => {
  if (!window._twMap) window._twMap = new Map();
  document.querySelectorAll('[data-testid="tweet"]').forEach(t => {
    const userEl = t.querySelector('[data-testid="User-Name"]');
    const user   = userEl ? userEl.innerText.split('\n').slice(0,2).join(' ') : '';
    const timeEl = t.querySelector('time');
    const time   = timeEl ? timeEl.getAttribute('datetime') : '';
    const text   = t.querySelector('[data-testid="tweetText"]')?.innerText || '';
    const link   = t.querySelector('a[href*="/status/"]')?.href || '';
    const ctx    = t.closest('[data-testid="cellInnerDiv"]')
                    ?.querySelector('[data-testid="socialContext"]');
    const isRT   = ctx ? /retweet|转推|已转帖/i.test(ctx.innerText) : false;
    if (isRT || !time || !link || !text.trim()) return;
    const key = link;
    if (!window._twMap.has(key)) {
      window._twMap.set(key, { user, time, text: text.slice(0,500), link });
    }
  });
  return { total: window._twMap.size, tweets: Array.from(window._twMap.values()) };
})()
"""


def _linux_args() -> list[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-setuid-sandbox",
    ]


def _cleanup_stale(profile_dir: Path) -> None:
    try:
        subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
        time.sleep(0.5)
    except Exception:
        pass
    for name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        try:
            (profile_dir / name).unlink()
        except FileNotFoundError:
            pass


def run_login(profile_dir: Path, profile_name: str = "default") -> None:
    """首次登入（需要圖形介面，在本機執行一次）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("pip install playwright && python -m playwright install chromium")

    print(f"[{profile_name}] Opening browser — please log in to Twitter/X...")
    print(f"  Profile: {profile_dir}")
    _cleanup_stale(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile_dir), headless=False,
            viewport={"width": 1280, "height": 900},
            args=_linux_args(),
        )
        try:
            page = ctx.new_page()
            page.goto("https://x.com/login", wait_until="domcontentloaded")
            print("  Waiting for login (up to 10 min)...")
            print("  Enter your credentials in the browser, then wait — session saves automatically.")
            try:
                page.wait_for_url(
                    re.compile(r"(twitter|x)\.com/home"),
                    timeout=600_000,
                )
                page.wait_for_timeout(4000)
                ctx.storage_state()
                page.wait_for_timeout(2000)
                print(f"  [OK] [{profile_name}] Login successful! Session saved.")
                print(f"  To deploy on Linux, copy this directory to the server:")
                print(f"  {profile_dir}")
            except Exception as e:
                print(f"  [WARN] Login did not complete ({e}). Re-run --login.")
        finally:
            ctx.close()
    _cleanup_stale(profile_dir)


def collect_foryou(profile_dir: Path, max_scrolls: int = _SCROLLS) -> list[dict]:
    """以 headless 模式抓取 Twitter「為你推薦」feed。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("pip install playwright && python -m playwright install chromium")

    if not _has_session(profile_dir):
        raise RuntimeError(f"No session found. Run --login first.\nProfile: {profile_dir}")

    _cleanup_stale(profile_dir)
    seen = _seen_urls()

    log.info("啟動 headless Chromium，滾動 %d 次...", max_scrolls)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            viewport={"width": 1280, "height": 900},
            ignore_default_args=["--enable-automation"],
            args=_linux_args(),
        )
        page = ctx.new_page()
        page.on("console", lambda _: None)

        page.goto("https://twitter.com/home", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        if re.search(r"/login|/i/flow", page.url):
            ctx.close()
            raise RuntimeError("Session 過期，請重新 --login")

        try:
            tabs = page.locator('[role="tab"]')
            for i in range(tabs.count()):
                label = (tabs.nth(i).inner_text() or "").strip()
                if any(k in label.lower() for k in ["for you", "為你", "推薦"]):
                    tabs.nth(i).click()
                    page.wait_for_timeout(2000)
                    break
        except Exception:
            pass

        stale = 0
        prev  = 0
        for i in range(max_scrolls):
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(450)
            if i % 5 == 4:
                page.wait_for_timeout(1200)
            if i % 25 == 24:
                r = page.evaluate(_SCAN_JS)
                n = r.get("total", 0)
                log.info("scroll %d/%d — %d tweets in DOM", i+1, max_scrolls, n)
                stale = stale + 1 if n == prev else 0
                if stale >= 3:
                    log.info("Feed 停止更新，提前結束")
                    break
                prev = n

        final  = page.evaluate(_SCAN_JS)
        all_tw = final.get("tweets", [])
        ctx.close()

    _cleanup_stale(profile_dir)

    new_tweets = [t for t in all_tw if t.get("link") not in seen]
    log.info("收到 %d 篇 DOM 推文，%d 篇為新增", len(all_tw), len(new_tweets))
    return new_tweets


# ══════════════════════════════════════════════════════════════════════════════
# 5. 逐推文分析
# ══════════════════════════════════════════════════════════════════════════════

# 明確非幣圈 → 本地直接過濾，不消耗 API quota
_NOISE_PATTERNS = [
    "抽奖", "抽獎", "转发关注", "轉發關注", "送礼", "送禮", "签到", "簽到",
    "空投交互日报", "空投交互日報", "薅羊毛", "打卡任务", "打卡任務",
    "参与方式", "參與方式", "关注转发", "關注轉發",
    "减肥", "減肥", "健身房", "相亲", "相親", "龙虾", "龍蝦", "披萨", "披薩",
    "街上没有", "流浪汉", "流浪漢", "小龙虾", "小龍蝦",
]

# 命中即快速放行，跳過噪音檢查
_CRYPTO_FASTPASS = [
    "BTC", "ETH", "SOL", "比特币", "比特幣", "以太坊", "以太", "鏈上", "链上",
    "DEX", "DeFi", "NFT", "合約", "合约", "現貨", "现货", "做多", "做空",
    "清算", "爆倉", "爆仓", "牛市", "熊市", "USDT", "Layer", "質押", "质押",
    "流動性", "流动性", "主網", "主网", "空投", "airdrop", "代幣", "代币",
    "上幣", "上币", "交易所", "幣圈", "币圈", "加密", "Web3", "web3",
    "Solana", "Bitcoin", "Ethereum", "Hyperliquid", "Binance", "coinbase",
]


def _is_obvious_noise(text: str) -> bool:
    t = text.lower()
    for kw in _CRYPTO_FASTPASS:
        if kw.lower() in t:
            return False
    for kw in _NOISE_PATTERNS:
        if kw.lower() in t:
            return True
    return False


_TWEET_PROMPT = """\
你是幣圈 alpha 獵手。判斷這條 Twitter 推文是否包含幣圈相關資訊。

推文作者：{user}
推文內容：{text}

【核心原則】：只要跟加密貨幣、區塊鏈、DeFi、NFT、Web3、交易所、項目方、KOL 觀點、市場動態有任何關聯，都應給予 ≥ 20 分。
0-14 分只留給：生活雜感、美食、抽獎打卡、與加密完全無關的內容。

只輸出 JSON：
{{
  "score": 0到100的整數,
  "signal": "bullish" 或 "bearish" 或 "neutral" 或 "noise",
  "coins": ["提到的幣種代號，沒有則空陣列"],
  "summary": "一句話，繁體中文，最多30字，說明核心信息或為何無關"
}}

評分標準：
- 70-100：第一手 alpha（新項目/合作上線、鏈上異常數據、重大事件、早期信號）
- 50-69：有具體幣圈觀點（技術分析、市場判斷、項目評測、KOL 看法）
- 30-49：幣圈相關討論（項目介紹、生態動態、行業資訊、工具推薦）
- 15-29：幣圈邊緣（加密從業者日常但含行業資訊）
- 0-14：完全無關（生活/抽獎/打卡/美食/情感/廣告）
"""


def analyze_tweet(tweet: dict) -> dict:
    """逐推文分析：先本地預篩，再呼叫 AI。"""
    text = (tweet.get("text") or "")
    if _is_obvious_noise(text):
        return {"score": 0, "signal": "noise", "coins": [], "summary": "非幣圈內容，本地過濾"}
    try:
        prompt = _TWEET_PROMPT.format(
            user=tweet.get("user", "unknown"),
            text=text[:500],
        )
        raw = _ds_chat(prompt, max_tokens=256)
        m   = re.search(r"\{[\s\S]*?\}", raw)
        if m:
            return json.loads(m.group())
    except Exception as e:
        log.warning("analyze_tweet failed: %s", e)
    return {"score": 0, "signal": "noise", "coins": [], "summary": "分析失敗"}


# ══════════════════════════════════════════════════════════════════════════════
# 6. 批次總結報告
# ══════════════════════════════════════════════════════════════════════════════

_BATCH_PROMPT = """\
你是幣圈頂級分析師。以下是從 Twitter「為你推薦」收集的 {count} 條推文分析，以及本批次關鍵字熱度排名：

關鍵字熱度（反覆被提及，代表新興 narrative）：
{hot_keywords}

推文列表：
{tweets_json}

請生成批次洞察報告，只輸出 JSON：
{{
  "period_summary": "2句話，本批次整體市場情緒與最核心的 narrative",
  "market_mood": "極度貪婪/貪婪/中性/恐慌/極度恐慌",
  "top_coins": ["本批次討論最多的幣種，最多5個"],
  "hot_narratives": ["2-3個正在發酵的敘事/主題，根據關鍵字熱度判斷"],
  "top10": [
    {{
      "rank": 1,
      "url": "推文原始連結",
      "user": "發文者",
      "signal": "bullish/bearish/neutral",
      "score": 分數,
      "summary": "為何值得關注，最多25字"
    }}
  ],
  "key_insights": ["3條重要洞察，每條一句話，優先挑有具體數字/早期信號的"],
  "noise_ratio": "非幣圈推文佔比，如 35%"
}}
"""


def _count_hot_keywords(tweets: list[dict]) -> str:
    """本地計算關鍵字熱度，不消耗 API。"""
    coin_pat = re.compile(r'\$([A-Z]{2,8})|(?<!\w)([A-Z]{2,8})(?!\w)')
    concept_pat = re.compile(
        r'(?:Hyperliquid|Solana|Ethereum|Bitcoin|Binance|Uniswap|Aave|'
        r'Pendle|Berachain|Base|Arbitrum|Optimism|Polygon|Avalanche|'
        r'DeFi|NFT|RWA|Layer2|L2|AI Agent|Meme|Pump\.fun|'
        r'質押|流動性|做多|做空|清算|爆倉|牛市|熊市|主網|空投)',
        re.IGNORECASE
    )
    counter: dict[str, int] = {}
    all_text = " ".join(
        (t.get("text") or "") + " " + (t.get("summary") or "")
        for t in tweets
    )
    for m in coin_pat.finditer(all_text):
        kw = (m.group(1) or m.group(2)).upper()
        if kw in {"RT", "PM", "AM", "DM", "AI", "OK", "GM", "GN", "VC"}:
            continue
        counter[kw] = counter.get(kw, 0) + 1
    for m in concept_pat.finditer(all_text):
        kw = m.group(0)
        counter[kw] = counter.get(kw, 0) + 1

    top = sorted(counter.items(), key=lambda x: -x[1])[:15]
    return ", ".join(f"{k}({v}次)" for k, v in top if v >= 2) or "暫無明顯熱點"


def generate_batch_report(tweets: list[dict]) -> dict:
    slim = [
        {
            "url":     t["url"],
            "user":    t["user"],
            "score":   t["score"],
            "signal":  t["signal"],
            "coins":   json.loads(t["coins"]) if isinstance(t["coins"], str) else t["coins"],
            "summary": t["summary"],
        }
        for t in tweets
    ]
    hot_keywords = _count_hot_keywords(tweets)
    prompt = _BATCH_PROMPT.format(
        count=len(slim),
        hot_keywords=hot_keywords,
        tweets_json=json.dumps(slim[:50], ensure_ascii=False),
    )
    try:
        raw = _ds_chat(prompt, max_tokens=2048)
        m   = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group())
    except Exception as e:
        log.error("generate_batch_report failed: %s", e)
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# 7. HTML 報告 → Playwright 截圖
# ══════════════════════════════════════════════════════════════════════════════

_SIGNAL_COLOR = {"bullish": "#22c55e", "bearish": "#ef4444", "neutral": "#94a3b8", "noise": "#475569"}
_SIGNAL_LABEL = {"bullish": "📈 看漲", "bearish": "📉 看跌", "neutral": "➡ 中性", "noise": "灰 噪音"}
_MOOD_COLOR   = {
    "極度貪婪": "#f59e0b", "貪婪": "#22c55e", "中性": "#64748b",
    "恐慌": "#f97316",    "極度恐慌": "#ef4444",
}


def _render_report_html(report: dict, tweet_count: int) -> str:
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")
    mood    = report.get("market_mood", "中性")
    mood_c  = _MOOD_COLOR.get(mood, "#64748b")
    coins   = " · ".join(f"${c}" for c in report.get("top_coins", []))
    summary = report.get("period_summary", "")
    insights = report.get("key_insights", [])
    noise   = report.get("noise_ratio", "N/A")
    top10   = report.get("top10", [])

    rows = ""
    for item in top10:
        sc       = item.get("signal", "neutral")
        clr      = _SIGNAL_COLOR.get(sc, "#94a3b8")
        lbl      = _SIGNAL_LABEL.get(sc, sc)
        url      = item.get("url", "#")
        rank     = item.get("rank", "")
        score    = item.get("score", "")
        note     = item.get("summary", "")
        raw_user = (item.get("user") or "")
        user_tag = raw_user.split("@")[-1].replace("\n", " ").strip()[:18]
        rows += (
            f'<tr>'
            f'<td class="rank">#{rank}</td>'
            f'<td class="score">{score}</td>'
            f'<td><span class="pill" style="background:{clr}22;color:{clr};border:1px solid {clr}44">{lbl}</span></td>'
            f'<td class="user">@{user_tag}</td>'
            f'<td class="note">{note}</td>'
            f'<td><a href="{url}" class="link">&#8594;</a></td>'
            f'</tr>'
        )

    ins_html = "".join(f'<li>{i}</li>' for i in insights)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Space Grotesk',sans-serif;background:#030304;color:#f8fafc;width:900px;padding:32px}}
  .header{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:24px}}
  .logo{{font-size:22px;font-weight:700;background:linear-gradient(135deg,#f7931a,#ffd600);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .meta{{font-family:'JetBrains Mono',monospace;font-size:11px;color:#475569;text-align:right;line-height:1.8}}
  .mood-badge{{display:inline-block;padding:4px 14px;border-radius:999px;font-size:13px;font-weight:700;background:{mood_c}22;color:{mood_c};border:1px solid {mood_c}44;margin-bottom:16px}}
  .summary{{font-size:14px;color:#94a3b8;line-height:1.7;margin-bottom:20px;padding:14px 18px;background:#0f1115;border-left:3px solid #f7931a;border-radius:0 8px 8px 0}}
  .stats{{display:flex;gap:16px;margin-bottom:24px}}
  .stat{{background:#0f1115;border:1px solid #1e293b;border-radius:10px;padding:12px 18px;flex:1;text-align:center}}
  .stat-num{{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;color:#f8fafc}}
  .stat-label{{font-size:10px;color:#475569;margin-top:3px;text-transform:uppercase;letter-spacing:.5px}}
  h2{{font-size:13px;font-weight:700;color:#475569;letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;font-family:'JetBrains Mono',monospace}}
  table{{width:100%;border-collapse:collapse;margin-bottom:24px}}
  th{{font-size:10px;color:#475569;font-family:'JetBrains Mono',monospace;letter-spacing:.5px;text-align:left;padding:6px 10px;border-bottom:1px solid #1e293b;text-transform:uppercase}}
  td{{padding:9px 10px;border-bottom:1px solid #0f1520;font-size:13px;vertical-align:middle}}
  td.rank{{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;color:#f7931a;width:36px}}
  td.score{{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:#f8fafc;width:40px}}
  td.user{{color:#64748b;font-family:'JetBrains Mono',monospace;font-size:11px;width:140px}}
  td.note{{color:#94a3b8;font-size:12px;line-height:1.5}}
  .pill{{padding:3px 9px;border-radius:999px;font-size:10px;font-weight:700;white-space:nowrap}}
  a.link{{color:#f7931a;font-size:13px;text-decoration:none}}
  .insights{{background:#0f1115;border:1px solid #1e293b;border-radius:10px;padding:16px 20px;margin-bottom:20px}}
  .insights li{{font-size:13px;color:#94a3b8;line-height:1.8;list-style:none;padding-left:16px;position:relative}}
  .insights li::before{{content:"→";position:absolute;left:0;color:#f7931a}}
  .coins{{font-family:'JetBrains Mono',monospace;font-size:12px;color:#64748b;margin-bottom:8px}}
  .footer{{font-size:10px;color:#334155;font-family:'JetBrains Mono',monospace;text-align:center;margin-top:20px}}
</style>
</head>
<body>
  <div class="header">
    <div>
      <div class="logo">ForYou Monitor</div>
      <div style="font-size:12px;color:#475569;margin-top:4px">Twitter For You · 批次報告</div>
    </div>
    <div class="meta">
      生成時間：{now}<br>
      分析推文：{tweet_count} 篇<br>
      噪音佔比：{noise}
    </div>
  </div>

  <div class="mood-badge">{mood}</div>
  <div class="summary">{summary}</div>

  <div class="stats">
    <div class="stat"><div class="stat-num">{tweet_count}</div><div class="stat-label">分析推文</div></div>
    <div class="stat"><div class="stat-num">{len(top10)}</div><div class="stat-label">精選信號</div></div>
    <div class="stat"><div class="stat-num">{noise}</div><div class="stat-label">噪音比例</div></div>
    <div class="stat"><div class="stat-num">{len(report.get('top_coins',[]))}</div><div class="stat-label">熱點幣種</div></div>
  </div>

  <div class="coins">熱點幣種：{coins if coins else '暫無'}</div>

  <h2>Top 10 信號排名</h2>
  <table>
    <thead>
      <tr><th>排名</th><th>分數</th><th>方向</th><th>帳號</th><th>核心觀點</th><th></th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>

  <h2>關鍵洞察</h2>
  <div class="insights"><ul>{ins_html}</ul></div>

  <div class="footer">ForYou Monitor · Twitter Alpha Intelligence</div>
</body>
</html>"""


def render_report_image(report: dict, tweet_count: int) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("pip install playwright && python -m playwright install chromium")

    html_content = _render_report_html(report, tweet_count)
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_dir = _DATA_DIR / "reports"
    img_dir.mkdir(parents=True, exist_ok=True)
    img_path = str(img_dir / f"foryou_report_{ts}.png")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_linux_args())
        page = browser.new_page(viewport={"width": 900, "height": 1200})
        page.set_content(html_content, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.screenshot(path=img_path, full_page=True)
        browser.close()

    log.info("報告圖片已儲存：%s", img_path)
    return img_path


# ══════════════════════════════════════════════════════════════════════════════
# 8. Telegram 發送（選填）
# ══════════════════════════════════════════════════════════════════════════════

def send_to_telegram(image_path: str, caption: str) -> None:
    if not _TG_TOKEN or not _TG_CHAT:
        log.info("TG_BOT_TOKEN 或 TG_CHAT_ID 未設定，跳過 Telegram 發送")
        return
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{_TG_TOKEN}/sendPhoto",
                data={"chat_id": _TG_CHAT, "caption": caption, "parse_mode": "HTML"},
                files={"photo": f},
                timeout=30,
            )
        resp.raise_for_status()
        log.info("已發送圖片到 Telegram")
    except Exception as e:
        log.warning("Telegram 發送失敗：%s", e)


# ══════════════════════════════════════════════════════════════════════════════
# 9. 主流程
# ══════════════════════════════════════════════════════════════════════════════

def run_once(profile_dir: Path, profile_name: str = "default") -> None:
    log.info("[%s] 開始抓取 For You...", profile_name)
    try:
        tweets = collect_foryou(profile_dir, _SCROLLS)
    except Exception as e:
        log.error("[%s] collect_foryou 失敗：%s", profile_name, e)
        return

    if not tweets:
        log.info("[%s] 無新增推文", profile_name)
        return

    log.info("[%s] 開始分析 %d 篇新推文...", profile_name, len(tweets))
    saved = 0
    for tw in tweets:
        if not tw.get("link"):
            continue
        analysis = analyze_tweet(tw)
        _save_tweet(tw, analysis, profile_name)
        saved += 1
        log.info("[%s][%d/%d] score=%s signal=%s %s",
                 profile_name, saved, len(tweets),
                 analysis.get("score"), analysis.get("signal"),
                 (tw.get("text") or "")[:50])
        time.sleep(0.5)

    log.info("[%s] 已儲存 %d 篇新推文到 DB", profile_name, saved)

    total_unbatched = _unbatched_count()
    log.info("待批次推文總數：%d / %d", total_unbatched, _BATCH_SIZE)
    if total_unbatched >= _BATCH_SIZE:
        force_batch_report()


def run_all_profiles() -> None:
    names = _all_profile_names()
    if not names:
        log.warning("沒有已登入的 profile，請先執行 --login --profile-name <名稱>")
        return
    log.info("找到 %d 個已登入 profile：%s", len(names), names)
    for name in names:
        run_once(_profile_dir(name), name)


def force_batch_report() -> None:
    tweets = _get_unbatched(_BATCH_SIZE)
    if not tweets:
        log.info("沒有待批次的推文")
        return

    log.info("生成批次報告，共 %d 篇推文...", len(tweets))
    report = generate_batch_report(tweets)
    if not report:
        log.error("批次報告生成失敗")
        return

    try:
        img_path = render_report_image(report, len(tweets))
    except Exception as e:
        log.error("圖片渲染失敗：%s", e)
        img_path = ""

    _save_batch_report(len(tweets), report, img_path)
    ids = [t["id"] for t in tweets]
    _mark_batched(ids)

    mood     = report.get("market_mood", "")
    summary  = report.get("period_summary", "")[:200]
    top_coin = ", ".join(f"${c}" for c in report.get("top_coins", [])[:5])
    caption  = (
        f"🤖 <b>ForYou Monitor · 批次報告</b>\n"
        f"市場情緒：{mood}\n"
        f"熱點幣種：{top_coin}\n\n"
        f"{summary}"
    )
    if img_path:
        send_to_telegram(img_path, caption)

    log.info("批次報告完成，圖片：%s", img_path or "（渲染失敗）")


def run_loop(interval_min: int, profile_dir: Path) -> None:
    log.info("啟動 daemon 模式，每 %d 分鐘執行一次 (Ctrl+C 停止)", interval_min)
    while True:
        try:
            run_once(profile_dir)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("run_once 異常：%s", e)
        log.info("等待 %d 分鐘...", interval_min)
        time.sleep(interval_min * 60)


# ══════════════════════════════════════════════════════════════════════════════
# 10. CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Twitter For You Monitor — AI 幣圈信號抓取（多帳號版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  python twitter_foryou_monitor.py --login --profile-name han
  python twitter_foryou_monitor.py --list-profiles
  python twitter_foryou_monitor.py --once --profile-name han
  python twitter_foryou_monitor.py --once --all-profiles
  python twitter_foryou_monitor.py --loop 15 --all-profiles
  python twitter_foryou_monitor.py --report
        """,
    )
    ap.add_argument("--once",          action="store_true")
    ap.add_argument("--loop",          type=int, metavar="MIN")
    ap.add_argument("--report",        action="store_true")
    ap.add_argument("--login",         action="store_true")
    ap.add_argument("--all-profiles",  action="store_true")
    ap.add_argument("--list-profiles", action="store_true")
    ap.add_argument("--profile-name",  default="default", metavar="NAME")
    ap.add_argument("--scrolls",       type=int, default=_SCROLLS)
    args = ap.parse_args()

    if args.list_profiles:
        names = _all_profile_names()
        if names:
            print(f"已登入 profile（共 {len(names)} 個）：")
            for n in names:
                print(f"  · {n}")
        else:
            print("尚無已登入的 profile，請先執行 --login --profile-name <名稱>")
        return

    profile = _profile_dir(args.profile_name)

    if args.login:
        run_login(profile, args.profile_name)
    elif args.report:
        force_batch_report()
    elif args.loop:
        if args.all_profiles:
            log.info("Daemon 模式（所有帳號），每 %d 分鐘", args.loop)
            while True:
                try:
                    run_all_profiles()
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    log.error("run_all_profiles 異常：%s", e)
                time.sleep(args.loop * 60)
        else:
            run_loop(args.loop, profile)
    elif args.all_profiles:
        run_all_profiles()
    else:
        run_once(profile, args.profile_name)


if __name__ == "__main__":
    main()
