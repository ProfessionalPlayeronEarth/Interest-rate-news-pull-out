"""主程序：抓取利率快讯 -> 去重 -> 格式化 -> 推送到微信。

用法：
  python -m src.main --once          # 立即跑一次（用于测试）
  python -m src.main --dry-run       # 跑一次但只打印、不推送、不记去重
  python -m src.main --test          # 发一条测试消息，验证推送渠道是否打通
  python -m src.main --schedule      # 按 config.yaml 的 times 定时循环运行
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import schedule

# 保证无论在哪个目录运行都能找到 src 包
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from src.config import load_config          # noqa: E402
from src.news import collect_news, fetch_recent_texts, relative_time  # noqa: E402
from src.social import collect_social        # noqa: E402
from src.pusher import push                 # noqa: E402
from src.store import SeenStore             # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("us-stock-news")


def _beijing_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(
        __import__("zoneinfo", fromlist=["ZoneInfo"]).ZoneInfo("Asia/Shanghai")
    )


def _render_items(items: list[dict], start_idx: int = 1) -> list[str]:
    lines = []
    for i, it in enumerate(items, start_idx):
        star = "⭐ " if it.get("strong") else ""
        early = "⚡ " if it.get("early") else ""
        rel = relative_time(it.get("published"))
        author = f" @{it['author']}" if it.get("author") else ""
        lines.append(f"{early}{star}**{i}. {it['title']}**")
        lines.append(f"> 🏷 {it['source']}{author} · {rel}")
        if it.get("link"):
            lines.append(f"> 🔗 {it['link']}")
        lines.append("")
    return lines


def format_message(items: list[dict], errors: list[str], social_enabled: bool = False) -> tuple[str, str]:
    """返回 (title, content)。items 含 kind='rate' 与 kind='social'。"""
    now = _beijing_now().strftime("%Y-%m-%d %H:%M")
    title = f"📈 美股利率快讯 · 中东局势（{now}）"

    rate_items = [i for i in items if i.get("kind") != "social"]
    social_items = [i for i in items if i.get("kind") == "social"]
    total = len(rate_items) + len(social_items)

    lines = [f"⏰ **{now}（北京时间）** · 利率 **{len(rate_items)}** 条 · 社媒信号 **{len(social_items)}** 条", ""]

    # 第一段：利率快讯
    lines.append("## 📈 利率相关快讯")
    if rate_items:
        lines += _render_items(rate_items)
    else:
        lines.append("🤷 最近时间窗内没有命中「利率相关」的新闻。\n")

    # 第二段：社媒早期信号
    if social_enabled:
        lines.append("## ⚡ 中东局势 · 社媒早期信号")
        if social_items:
            early_n = sum(1 for i in social_items if i.get("early"))
            if early_n:
                lines.append(f"> 其中 **{early_n}** 条疑似「早于主流媒体」的未覆盖信号\n")
            lines += _render_items(social_items)
        else:
            lines.append("🤷 社媒未监测到中东战争相关的新发帖。\n")

    if errors:
        lines.append("⚠️ 部分源抓取异常：")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("_由 us-stock-news 自动推送_")
    return title, "\n".join(lines)


def format_early_alert(items: list[dict]) -> tuple[str, str]:
    now = _beijing_now().strftime("%Y-%m-%d %H:%M")
    title = f"⚡ 中东局势·早于主流的社媒信号（{now}）"
    lines = [
        f"⚠️ **检测到可能早于主流媒体报道的中东局势信号，请关注（或影响油价/避险/利率）**",
        "",
    ]
    lines += _render_items(items)
    lines.append("_由 us-stock-news 自动推送 · 此为主流尚未覆盖的早期信号_")
    return title, "\n".join(lines)


def run_once(cfg: dict, dry_run: bool = False) -> None:
    log.info("开始抓取新闻源…")
    news_items, news_errors = collect_news(cfg)
    for it in news_items:
        it.setdefault("kind", "rate")

    social_enabled = cfg.get("social", {}).get("enabled", False)
    social_items: list[dict] = []
    social_errors: list[str] = []
    if social_enabled:
        log.info("抓取社媒舆情（Reddit / Mastodon / Telegram）…")
        mainstream_texts = fetch_recent_texts(cfg)
        social_items, social_errors = collect_social(cfg, mainstream_texts)
        log.info("社媒命中 %d 条（其中早期信号 %d 条）；源异常 %d 个。",
                 len(social_items), sum(1 for i in social_items if i.get("early")),
                 len(social_errors))

    errors = news_errors + social_errors

    # ---- 早期信号即时预警（独立于定时摘要）----
    store = None
    if social_enabled and not dry_run:
        # 兜底：上游若未标记 early，则按「命中战争词 + 发帖新鲜」判定为早期信号，避免漏报
        for it in social_items:
            if "early" not in it:
                it["early"] = bool(it.get("matched") and (it.get("fresh_minutes") is not None
                                                           and it["fresh_minutes"] <= cfg.get("social", {}).get("early_window_hours", 3) * 60))
        store = SeenStore(cfg["dedup"]["seen_file"], cfg["dedup"].get("max_kept", 2000))
        early = [i for i in social_items if i.get("early")]
        if early and cfg.get("social", {}).get("urgent_alert", True):
            etitle, econtent = format_early_alert(early)
            ok, msg = push(cfg, etitle, econtent)
            log.info("早期信号即时预警：%s", "成功" if ok else f"失败({msg})")
            for i in early:
                store.add(i.get("id") or i.get("link") or i.get("title"))
            # 已预警的不再进摘要，避免重复
            social_items = [i for i in social_items if not i.get("early")]

    combined = news_items + social_items

    if not dry_run and cfg.get("dedup", {}).get("enabled", True):
        if store is None:
            store = SeenStore(cfg["dedup"]["seen_file"], cfg["dedup"].get("max_kept", 2000))
        before = len(combined)
        combined = store.filter_new(combined)
        log.info("去重后待推送 %d 条（去除已推送 %d 条）。", len(combined), before - len(combined))

    title, content = format_message(combined, errors, social_enabled)

    if dry_run:
        print("\n===== DRY RUN（不推送、不记去重）=====\n")
        print(title)
        print("-" * 40)
        print(content)
        if combined:
            print("\n--- 命中关键词（便于调参）---")
            for it in combined:
                tag = "社媒" if it.get("kind") == "social" else "利率"
                flag = " ⚡早期" if it.get("early") else ""
                print(f"  [{tag}{flag}] {it['title'][:46]}  =>  {it.get('matched')}")
        return

    if not combined:
        log.info("没有新内容，跳过推送。")
        return

    ok, msg = push(cfg, title, content)
    if ok:
        log.info("推送成功：%s", msg)
    else:
        log.error("推送失败：%s", msg)


def send_test(cfg: dict) -> None:
    title = "✅ 推送测试"
    content = ("如果你在微信里看到这条消息，说明**推送渠道已打通** ✅\n\n"
               "接下来把 `config.yaml` 里的 `push.channel` 设成对应渠道，"
               "并填好 token/webhook，项目就会每天自动给你推美股利率快讯啦。\n\n"
               "_us-stock-news_")
    ok, msg = push(cfg, title, content)
    print(("成功：" if ok else "失败：") + msg)


def run_schedule(cfg: dict) -> None:
    tz = cfg.get("schedule", {}).get("timezone", "Asia/Shanghai")
    os.environ["TZ"] = tz
    times = cfg.get("schedule", {}).get("times", ["07:30", "12:30", "21:30"])

    def job():
        run_once(cfg)

    if cfg.get("schedule", {}).get("run_immediately"):
        log.info("run_immediately=true，先跑一次…")
        job()

    for t in times:
        schedule.every().day.at(t).do(job)
        log.info("已设定每日 %s（%s）推送。", t, tz)

    log.info("调度器启动，按 Ctrl+C 退出。")
    while True:
        schedule.run_pending()
        import time
        time.sleep(30)


def main() -> None:
    ap = argparse.ArgumentParser(description="美股利率快讯 · 微信推送")
    ap.add_argument("--config", help="配置文件路径（默认 config.yaml）")
    ap.add_argument("--once", action="store_true", help="立即跑一次")
    ap.add_argument("--dry-run", action="store_true", help="跑一次但只打印不推送")
    ap.add_argument("--test", action="store_true", help="发一条测试消息验证渠道")
    ap.add_argument("--schedule", action="store_true", help="按配置定时循环运行")
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.test:
        send_test(cfg)
    elif args.dry_run:
        run_once(cfg, dry_run=True)
    elif args.schedule:
        run_schedule(cfg)
    else:
        # 默认：跑一次
        run_once(cfg)


if __name__ == "__main__":
    main()
