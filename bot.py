"""美股利率快讯 + 中东局势社媒早期信号 · 微信推送（单文件版，供 GitHub Actions 内嵌运行）。"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("us-stock-news")

CONFIG = {
    "push": {
        "channel": "console",
        "serverchan": {"sendkey": ""},
        "pushplus": {"token": ""},
        "wecom": {"webhook": ""},
    },
    "sources": [
        {"name": "CNBC Markets",
         "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"},
        {"name": "MarketWatch",
         "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
        {"name": "WSJ Markets",
         "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
        {"name": "Federal Reserve",
         "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    ],
    "rate_keywords": [
        "rate", "rates", "interest rate", "federal reserve", "fomc", "powell",
        "treasury", "yield", "yields", "bond", "bonds", "inflation", "ecb",
        "rate cut", "rate hike", "降息", "加息", "利率", "美联储", "国债", "通胀",
        "收益率", "鲍威尔",
    ],
    "strong_keywords": [
        "rate cut", "rate hike", "interest rate", "fomc", "federal reserve",
        "powell", "treasury yield", "降息", "加息", "利率", "美联储", "鲍威尔",
    ],
    "fetch": {"max_items_per_source": 30, "lookback_hours": 24, "request_timeout": 15},
    "schedule": {"timezone": "Asia/Shanghai",
                 "times": ["07:30", "12:30", "21:30"], "run_immediately": False},
    "dedup": {"enabled": True, "seen_file": "data/seen.json", "max_kept": 2000},
    "social": {
        "enabled": True,
        "urgent_alert": True,
        "lookback_hours": 12,
        "early_window_hours": 3,
        "max_items_per_source": 20,
        "sources": [
            {"type": "reddit", "name": "Reddit r/worldnews", "subreddit": "worldnews"},
            {"type": "reddit", "name": "Reddit r/CombatFootage", "subreddit": "CombatFootage"},
            {"type": "reddit", "name": "Reddit r/geopolitics", "subreddit": "geopolitics"},
            {"type": "mastodon", "name": "Mastodon #MiddleEast",
             "instance": "mastodon.social", "tag": "MiddleEast"},
            {"type": "mastodon", "name": "Mastodon #Israel",
             "instance": "mastodon.social", "tag": "Israel"},
            {"type": "rss", "name": "Telegram(经RSSHub): 示例频道",
             "url": "https://rsshub.app/telegram/channel/breakingmideast"},
        ],
        "war_keywords": [
            "israel", "iran", "gaza", "hamas", "hezbollah", "houthis", "yemen",
            "lebanon", "syria", "idf", "irgc", "missile", "airstrike", "airstrikes",
            "drone attack", "invasion", "ceasefire", "war", "conflict",
            "strait of hormuz", "oil price", "crude", "middle east", "red sea",
            "中东", "以色列", "伊朗", "加沙", "哈马斯", "真主党", "胡塞", "黎巴嫩",
            "叙利亚", "导弹", "空袭", "战争", "停火", "霍尔木兹", "原油", "油价",
        ],
    },
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_SOCIAL = "us-stock-news/1.0 (geopolitical OSINT monitor)"


# ------------------------- 通用工具 -------------------------
def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(entry: dict):
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return datetime.fromtimestamp(time.mktime(val), tz=timezone.utc)
            except (ValueError, OverflowError):
                pass
    for key in ("published", "updated", "pubDate"):
        val = entry.get(key)
        if val:
            parsed = feedparser._parse_date(val)
            if parsed:
                return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    return None


def _parse_reddit_time(epoch):
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _fresh_minutes(published):
    if not published:
        return None
    return (datetime.now(timezone.utc) - published).total_seconds() / 60.0


def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    text_l = text.lower()
    hits: list[str] = []
    for k in keywords:
        kl = k.lower()
        if kl.isascii() and kl.replace(" ", "").isalpha():
            if re.search(rf"(?<![a-z]){re.escape(kl)}s?(?![a-z])", text_l):
                hits.append(k)
        else:
            if kl in text_l:
                hits.append(k)
    return hits


def relative_time(dt):
    if not dt:
        return "时间未知"
    age_s = (datetime.now(timezone.utc) - dt).total_seconds()
    if age_s < 0:
        age_s = 0
    if age_s < 3600:
        return f"{int(age_s // 60)} 分钟前"
    if age_s < 86400:
        return f"{int(age_s // 3600)} 小时前"
    return f"{int(age_s // 86400)} 天前"


# ------------------------- 新闻（RSS）-------------------------
def fetch_source(name: str, url: str, timeout: int, max_items: int):
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        return [], f"抓取失败: {exc}"

    items = []
    for e in feed.entries[:max_items]:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        summary = _strip_html(e.get("summary") or e.get("description") or "")
        if not title:
            continue
        items.append({
            "id": e.get("id") or link or title,
            "title": title,
            "link": link,
            "summary": summary,
            "source": name,
            "published": _parse_date(e),
        })
    return items, None


def collect_news(cfg: dict):
    rate_kw = cfg.get("rate_keywords", [])
    strong_kw = cfg.get("strong_keywords", [])
    fetch_cfg = cfg.get("fetch", {})
    lookback = fetch_cfg.get("lookback_hours", 24)
    now = datetime.now(timezone.utc)

    all_items, errors = [], []
    for src in cfg.get("sources", []):
        items, err = fetch_source(src["name"], src["url"],
                                  fetch_cfg.get("request_timeout", 15),
                                  fetch_cfg.get("max_items_per_source", 30))
        if err:
            errors.append(f"{src['name']}: {err}")
            continue
        all_items.extend(items)

    results = []
    for it in all_items:
        hay = f"{it['title']} {it['summary']}"
        hits = _match_keywords(hay, rate_kw)
        if not hits:
            continue
        pub = it.get("published")
        if lookback and pub:
            if (now - pub).total_seconds() / 3600.0 > lookback:
                continue
        it["matched"] = hits
        it["strong"] = bool(_match_keywords(hay, strong_kw))
        it["score"] = (2 if it["strong"] else 0) + len(hits)
        it["_sortkey"] = pub or now
        results.append(it)

    results.sort(key=lambda x: (x["score"], x["_sortkey"]), reverse=True)
    for it in results:
        it.pop("_sortkey", None)
    return results, errors


def fetch_recent_texts(cfg: dict) -> list[str]:
    texts = []
    fetch_cfg = cfg.get("fetch", {})
    timeout = fetch_cfg.get("request_timeout", 15)
    max_per = fetch_cfg.get("max_items_per_source", 30)
    for s in cfg.get("sources", []):
        items, _ = fetch_source(s.get("name", ""), s.get("url", ""), timeout, max_per)
        for it in items:
            txt = f"{it.get('title', '')} {it.get('summary', '')}".lower()
            if txt.strip():
                texts.append(txt)
    return texts


# ------------------------- 社媒 -------------------------
def fetch_reddit(subreddit: str, limit: int, timeout: int):
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA_SOCIAL})
        if r.status_code != 200:
            return [], f"Reddit r/{subreddit} 返回 HTTP {r.status_code}"
        data = r.json()
        if "error" in data:
            return [], f"Reddit r/{subreddit} 错误: {data.get('message')}"
        children = data.get("data", {}).get("children", [])
    except Exception as exc:  # noqa: BLE001
        return [], f"Reddit r/{subreddit} 抓取失败: {exc}"

    out = []
    for c in children:
        d = c.get("data", {})
        title = d.get("title", "")
        text = (d.get("selftext") or "")[:400]
        out.append({
            "title": title,
            "text": f"{title}\n{text}",
            "link": "https://www.reddit.com" + d.get("permalink", ""),
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "author": d.get("author", ""),
            "published": _parse_reddit_time(d.get("created_utc")),
            "source": f"Reddit r/{subreddit}",
        })
    return out, None


def fetch_mastodon(instance: str, tag: str, limit: int, timeout: int):
    url = f"https://{instance}/api/v1/timelines/tag/{tag}?limit={limit}"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA_SOCIAL})
        if r.status_code != 200:
            return [], f"Mastodon {instance} #{tag} 返回 HTTP {r.status_code}"
        statuses = r.json()
        if not isinstance(statuses, list):
            return [], f"Mastodon {instance} #{tag} 返回格式异常"
    except Exception as exc:  # noqa: BLE001
        return [], f"Mastodon {instance} #{tag} 抓取失败: {exc}"

    out = []
    for s in statuses:
        content = _strip_html(s.get("content", ""))[:400]
        acct = s.get("account", {}).get("acct", "")
        out.append({
            "title": content,
            "text": content,
            "link": s.get("url") or "",
            "url": s.get("url") or "",
            "author": acct,
            "published": _parse_iso(s.get("created_at")),
            "source": f"Mastodon @{instance} #{tag}",
        })
    return out, None


def fetch_rss_social(url: str, limit: int, timeout: int):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA_SOCIAL})
        feed = feedparser.parse(r.content)
    except Exception as exc:  # noqa: BLE001
        return [], f"RSS {url} 抓取失败: {exc}"

    out = []
    for e in feed.entries[:limit]:
        title = e.get("title", "")
        summary = _strip_html(e.get("summary", "") or e.get("description", ""))[:400]
        published = None
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                published = datetime.fromtimestamp(time.mktime(e[key]), tz=timezone.utc)
                break
        out.append({
            "title": title,
            "text": f"{title}\n{summary}",
            "link": e.get("link", ""),
            "url": e.get("link", ""),
            "author": e.get("author", ""),
            "published": published,
            "source": feed.feed.get("title", url) if hasattr(feed, "feed") else url,
        })
    return out, None


def collect_social(cfg: dict, mainstream_texts=None):
    s_cfg = cfg.get("social", {})
    if not s_cfg.get("enabled", False):
        return [], []

    war_kw = s_cfg.get("war_keywords", [])
    lookback = s_cfg.get("lookback_hours", 12)
    early_window = s_cfg.get("early_window_hours", 3)
    max_per = s_cfg.get("max_items_per_source", 20)
    timeout = cfg.get("fetch", {}).get("request_timeout", 15)

    mainstream_kw_set = set()
    if mainstream_texts:
        blob = " ".join(mainstream_texts).lower()
        for k in war_kw:
            kl = k.lower()
            if kl.isascii() and kl.replace(" ", "").isalpha():
                if re.search(rf"(?<![a-z]){re.escape(kl)}(?![a-z])", blob):
                    mainstream_kw_set.add(kl)
            elif kl in blob:
                mainstream_kw_set.add(kl)

    items, errors = [], []
    for src in s_cfg.get("sources", []):
        stype = src.get("type", "rss")
        if stype == "reddit":
            chunk, err = fetch_reddit(src.get("subreddit", ""), max_per, timeout)
        elif stype == "mastodon":
            chunk, err = fetch_mastodon(src.get("instance", ""), src.get("tag", ""), max_per, timeout)
        else:
            chunk, err = fetch_rss_social(src.get("url", ""), max_per, timeout)
        if err:
            errors.append(err)
            continue
        for it in chunk:
            mins = _fresh_minutes(it["published"])
            if lookback > 0 and mins is not None and mins > lookback * 60:
                continue
            matched = _match_keywords(it["text"], war_kw)
            if not matched:
                continue
            it["matched"] = matched
            it["kind"] = "social"
            it["id"] = it.get("url") or it.get("link") or it.get("title")
            fresh = (mins is not None and mins <= early_window * 60)
            unique_kw = set(m.lower() for m in matched) - mainstream_kw_set
            it["early"] = bool(fresh and unique_kw)
            it["fresh_minutes"] = mins
            items.append(it)

    items.sort(key=lambda x: (not x.get("early"),
                              -(x["published"].timestamp() if x.get("published") else 0)))
    return items, errors


# ------------------------- 推送 -------------------------
def _post(url: str, payload: dict, timeout: int = 15):
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        return r.ok, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _send_serverchan(sendkey: str, title: str, content: str):
    if not sendkey:
        return False, "未配置 serverchan.sendkey"
    return _post(f"https://sctapi.ftqq.com/{sendkey}.send",
                 {"title": title, "desp": content})


def _send_pushplus(token: str, title: str, content: str):
    if not token:
        return False, "未配置 pushplus.token"
    return _post("https://www.pushplus.plus/send",
                {"token": token, "title": title, "content": content, "template": "markdown"})


def _send_wecom(webhook: str, content: str):
    if not webhook:
        return False, "未配置 wecom.webhook"
    if len(content.encode("utf-8")) > 4000:
        content = content[:1800] + "\n\n…（内容过长已截断）"
    return _post(webhook, {"msgtype": "markdown", "markdown": {"content": content}})


def push(cfg: dict, title: str, content: str):
    push_cfg = cfg.get("push", {})
    channel = (push_cfg.get("channel") or "console").lower()

    if channel == "console":
        print("\n" + "=" * 50)
        print("【待推送内容】")
        print("标题:", title)
        print("-" * 50)
        print(content)
        print("=" * 50 + "\n")
        return True, "console 模式：已打印到屏幕（未真正推送）"

    if channel == "serverchan":
        return _send_serverchan(push_cfg.get("serverchan", {}).get("sendkey", ""), title, content)
    if channel == "pushplus":
        return _send_pushplus(push_cfg.get("pushplus", {}).get("token", ""), title, content)
    if channel == "wecom":
        return _send_wecom(push_cfg.get("wecom", {}).get("webhook", ""), content)

    for name, fn in (
        ("serverchan", lambda: _send_serverchan(push_cfg.get("serverchan", {}).get("sendkey", ""), title, content)),
        ("pushplus", lambda: _send_pushplus(push_cfg.get("pushplus", {}).get("token", ""), title, content)),
        ("wecom", lambda: _send_wecom(push_cfg.get("wecom", {}).get("webhook", ""), content)),
    ):
        ok, msg = fn()
        if ok:
            return True, f"通过 {name} 推送成功"
    return False, "所有已配置渠道均不可用，请检查密钥"


# ------------------------- 去重 -------------------------
class SeenStore:
    def __init__(self, path: str, max_kept: int = 2000):
        from pathlib import Path
        self.path = Path(path)
        self.max_kept = max_kept
        self.seen = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.seen, ensure_ascii=False), encoding="utf-8")

    def add(self, key: str):
        self.seen[key] = time.time()
        if len(self.seen) > self.max_kept:
            oldest = sorted(self.seen.items(), key=lambda kv: kv[1])[:len(self.seen) - self.max_kept]
            for k, _ in oldest:
                self.seen.pop(k, None)
        self._save()

    def filter_new(self, items: list[dict]) -> list[dict]:
        fresh = []
        for it in items:
            key = it.get("id") or it.get("link") or it.get("title")
            if key not in self.seen:
                fresh.append(it)
                self.add(key)
        return fresh


# ------------------------- 排版 -------------------------
def _beijing_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


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


def format_message(items: list[dict], errors: list[str], social_enabled: bool = False):
    now = _beijing_now().strftime("%Y-%m-%d %H:%M")
    title = f"📈 美股利率快讯 · 中东局势（{now}）"
    rate_items = [i for i in items if i.get("kind") != "social"]
    social_items = [i for i in items if i.get("kind") == "social"]
    lines = [f"⏰ **{now}（北京时间）** · 利率 **{len(rate_items)}** 条 · 社媒信号 **{len(social_items)}** 条", ""]
    lines.append("## 📈 利率相关快讯")
    if rate_items:
        lines += _render_items(rate_items)
    else:
        lines.append("🤷 最近时间窗内没有命中「利率相关」的新闻。\n")
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


def format_early_alert(items: list[dict]):
    now = _beijing_now().strftime("%Y-%m-%d %H:%M")
    title = f"⚡ 中东局势·早于主流的社媒信号（{now}）"
    lines = ["⚠️ **检测到可能早于主流媒体报道的中东局势信号，请关注（或影响油价/避险/利率）**", ""]
    lines += _render_items(items)
    lines.append("_由 us-stock-news 自动推送 · 此为主流尚未覆盖的早期信号_")
    return title, "\n".join(lines)


# ------------------------- 主流程 -------------------------
def run_once(cfg: dict) -> None:
    log.info("开始抓取新闻源…")
    news_items, news_errors = collect_news(cfg)
    for it in news_items:
        it.setdefault("kind", "rate")

    social_enabled = cfg.get("social", {}).get("enabled", False)
    social_items, social_errors = [], []
    if social_enabled:
        log.info("抓取社媒舆情（Reddit / Mastodon / Telegram）…")
        mainstream_texts = fetch_recent_texts(cfg)
        social_items, social_errors = collect_social(cfg, mainstream_texts)
        log.info("社媒命中 %d 条（其中早期信号 %d 条）；源异常 %d 个。",
                 len(social_items), sum(1 for i in social_items if i.get("early")),
                 len(social_errors))

    errors = news_errors + social_errors

    store = None
    if social_enabled:
        for it in social_items:
            if "early" not in it:
                ew = cfg.get("social", {}).get("early_window_hours", 3)
                it["early"] = bool(it.get("matched") and it.get("fresh_minutes") is not None
                                   and it["fresh_minutes"] <= ew * 60)
        store = SeenStore(cfg["dedup"]["seen_file"], cfg["dedup"].get("max_kept", 2000))
        early = [i for i in social_items if i.get("early")]
        if early and cfg.get("social", {}).get("urgent_alert", True):
            etitle, econtent = format_early_alert(early)
            ok, msg = push(cfg, etitle, econtent)
            log.info("早期信号即时预警：%s", "成功" if ok else f"失败({msg})")
            for i in early:
                store.add(i.get("id") or i.get("link") or i.get("title"))
            social_items = [i for i in social_items if not i.get("early")]

    combined = news_items + social_items

    if cfg.get("dedup", {}).get("enabled", True):
        if store is None:
            store = SeenStore(cfg["dedup"]["seen_file"], cfg["dedup"].get("max_kept", 2000))
        before = len(combined)
        combined = store.filter_new(combined)
        log.info("去重后待推送 %d 条（去除已推送 %d 条）。", len(combined), before - len(combined))

    title, content = format_message(combined, errors, social_enabled)

    if not combined:
        log.info("没有新内容，跳过推送。")
        return

    ok, msg = push(cfg, title, content)
    if ok:
        log.info("推送成功：%s", msg)
    else:
        log.error("推送失败：%s", msg)


def detect_config() -> dict:
    cfg = CONFIG
    sendkey = os.environ.get("SERVERCHAN_SENDKEY")
    pptoken = os.environ.get("PUSHPLUS_TOKEN")
    wecom = os.environ.get("WECOM_WEBHOOK")
    if sendkey:
        cfg["push"]["channel"] = "serverchan"
        cfg["push"]["serverchan"]["sendkey"] = sendkey
    elif pptoken:
        cfg["push"]["channel"] = "pushplus"
        cfg["push"]["pushplus"]["token"] = pptoken
    elif wecom:
        cfg["push"]["channel"] = "wecom"
        cfg["push"]["wecom"]["webhook"] = wecom
    else:
        cfg["push"]["channel"] = "console"
        print("⚠️ 未检测到推送密钥，将以 console 模式运行（不真正推送）。"
              "请在仓库 Secrets 填写 SERVERCHAN_SENDKEY / PUSHPLUS_TOKEN / WECOM_WEBHOOK。")
    return cfg


if __name__ == "__main__":
    run_once(detect_config())
