#!/usr/bin/env python3
"""
view_report.py — 在 localhost 上瀏覽最新的 For You 推文報告。

用法：
  python view_report.py          # 開啟最新報告（port 8765）
  python view_report.py --port 9090
"""

import argparse
import base64
import http.server
import json
import sqlite3
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent
_DB   = _ROOT / "data" / "foryou.db"

_SIGNAL_BADGE = {
    "bullish": ("📈", "#22c55e"),
    "bearish": ("📉", "#ef4444"),
    "neutral": ("➡️", "#94a3b8"),
    "noise":   ("🔇", "#475569"),
}

_MOOD_COLOR = {
    "極度貪婪": "#f59e0b",
    "貪婪":    "#22c55e",
    "中性":    "#64748b",
    "恐慌":    "#f97316",
    "極度恐慌": "#ef4444",
}


def _load_data() -> dict:
    if not _DB.exists():
        return {"error": f"DB not found at {_DB}\nRun: python twitter_foryou_monitor.py --once --profile-name <name>"}
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row

    report_row = conn.execute(
        "SELECT * FROM batch_reports ORDER BY id DESC LIMIT 1"
    ).fetchone()
    tweets = conn.execute(
        "SELECT * FROM foryou_tweets ORDER BY score DESC, fetched_at DESC LIMIT 100"
    ).fetchall()
    conn.close()

    report = {}
    image_b64 = ""
    report_time = ""

    if report_row:
        report = json.loads(report_row["report_json"])
        report_time = report_row["created_at"]
        img_path = Path(report_row["image_path"] or "")
        if img_path.exists():
            image_b64 = base64.b64encode(img_path.read_bytes()).decode()

    return {
        "report":      report,
        "image_b64":   image_b64,
        "report_time": report_time,
        "tweets":      [dict(t) for t in tweets],
    }


def _build_html(data: dict) -> str:
    if "error" in data:
        return f"<body style='background:#0f172a;color:#f87171;font-family:monospace;padding:40px'><pre>{data['error']}</pre></body>"

    report         = data["report"]
    image_b64      = data["image_b64"]
    report_time    = data["report_time"]
    tweets         = data["tweets"]

    now_str        = datetime.now().strftime("%Y-%m-%d %H:%M")
    mood           = report.get("market_mood", "—")
    mood_c         = _MOOD_COLOR.get(mood, "#64748b")
    top_coins      = " · ".join(f"<code>${c}</code>" for c in report.get("top_coins", [])) or "—"
    summary        = report.get("period_summary", "—")
    noise_r        = report.get("noise_ratio", "—")
    hot_narratives = report.get("hot_narratives", [])

    img_html = ""
    if image_b64:
        img_html = f'<img src="data:image/png;base64,{image_b64}" style="max-width:100%;border-radius:12px;margin-bottom:24px;" />'

    rows = ""
    for t in tweets:
        sig, sig_c = _SIGNAL_BADGE.get(t.get("signal", "noise"), ("—", "#475569"))
        score      = t.get("score", 0)
        score_c    = "#22c55e" if score >= 50 else "#f59e0b" if score >= 20 else "#475569"
        user       = (t.get("user") or "—")[:30]
        summary_c  = (t.get("summary") or "—")[:120]
        coins      = ", ".join(f"${c}" for c in json.loads(t.get("coins") or "[]")) or "—"
        url        = t.get("url", "#")
        rows += f"""
        <tr>
          <td style="color:{score_c};font-weight:700;text-align:center">{score}</td>
          <td style="color:{sig_c}">{sig} {t.get('signal','')}</td>
          <td style="color:#94a3b8;font-size:12px">{user}</td>
          <td style="font-size:13px;color:#cbd5e1">{summary_c}</td>
          <td style="font-size:12px;color:#7dd3fc">{coins}</td>
          <td><a href="{url}" target="_blank" style="color:#7dd3fc;font-size:11px">🔗</a></td>
        </tr>"""

    report_dt = ""
    if report_time:
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(report_time.replace("Z", "+00:00"))
            report_dt = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            report_dt = report_time

    narratives_html = ""
    if hot_narratives:
        pills = "".join(
            f"<span style='background:#1e3a5f;color:#7dd3fc;padding:4px 12px;border-radius:20px;font-size:13px'>{n}</span>"
            for n in hot_narratives
        )
        narratives_html = f"""
        <div class='card'>
          <div style='font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px'>🔥 熱點 Narrative</div>
          <div style='display:flex;flex-wrap:wrap;gap:8px'>{pills}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>ForYou Monitor · 報告</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ background:#0f172a; color:#e2e8f0; font-family:'Inter',system-ui,sans-serif; padding:24px; }}
    h1   {{ font-size:22px; font-weight:700; margin-bottom:4px; }}
    .sub {{ color:#64748b; font-size:13px; margin-bottom:24px; }}
    .card {{ background:#1e293b; border-radius:12px; padding:20px; margin-bottom:20px; }}
    .stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:20px; }}
    .stat {{ background:#1e293b; border-radius:10px; padding:16px; }}
    .stat label {{ font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:.05em; }}
    .stat .val   {{ font-size:20px; font-weight:700; margin-top:4px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th    {{ text-align:left; padding:10px 12px; color:#64748b; font-weight:500; font-size:11px;
             text-transform:uppercase; border-bottom:1px solid #334155; }}
    td    {{ padding:10px 12px; border-bottom:1px solid #1e293b; vertical-align:top; }}
    tr:hover td {{ background:#1e293b88; }}
    code  {{ background:#0f172a; padding:2px 6px; border-radius:4px; font-size:12px; }}
  </style>
</head>
<body>
  <h1>🐦 ForYou Monitor · 報告</h1>
  <div class="sub">生成時間：{report_dt or now_str} &nbsp;|&nbsp; 查看時間：{now_str}</div>

  <div class="stat-grid">
    <div class="stat"><label>市場情緒</label><div class="val" style="color:{mood_c}">{mood}</div></div>
    <div class="stat"><label>熱門幣種</label><div class="val" style="font-size:14px;margin-top:6px">{top_coins}</div></div>
    <div class="stat"><label>噪音比例</label><div class="val">{noise_r}</div></div>
    <div class="stat"><label>推文總數</label><div class="val">{len(tweets)}</div></div>
  </div>

  {"<div class='card'><div style='color:#94a3b8;font-size:13px;line-height:1.6'>📝 " + summary + "</div></div>" if summary and summary != "—" else ""}
  {narratives_html}
  {f"<div style='margin-bottom:24px'>{img_html}</div>" if img_html else ""}

  <div class="card" style="padding:0;overflow:hidden">
    <table>
      <thead>
        <tr>
          <th style="width:60px">Score</th><th style="width:110px">Signal</th>
          <th style="width:140px">User</th><th>Summary</th>
          <th style="width:120px">Coins</th><th style="width:40px">Link</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</body>
</html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        html = _build_html(_load_data()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"
    print(f"📊 ForYou Monitor Report → {url}")
    print("   Ctrl+C to stop\n")

    server = http.server.HTTPServer(("", args.port), _Handler)
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
