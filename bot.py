"""美股利率快讯 + 中东局势社媒早期信号 · 微信推送（单文件版，供 GitHub Actions 内嵌运行）。"""
from __future__ import annotations

import json
import logging
import math
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
        # —— 利率 / 货币政策（原有）——
        "rate", "rates", "interest rate", "federal reserve", "fomc", "powell", "warsh",
        "treasury", "yield", "yields", "bond", "bonds", "inflation", "ecb",
        "rate cut", "rate hike", "降息", "加息", "利率", "美联储", "国债", "通胀",
        "收益率", "鲍威尔", "沃什",
        # —— 关键经济数据发布（新增：直接驱动利率的硬数据）——
        "cpi", "pce", "ppi", "gdp", "retail sales", "durable goods", "pmi",
        "nonfarm", "non-farm", "payroll", "nfp", "adp", "jobs report",
        "employment report", "consumer price", "producer price",
        "消费者物价", "生产者物价", "个人消费支出", "非农", "非农就业",
        "就业报告", "小非农", "国内生产总值", "采购经理指数", "零售销售",
    ],
    "strong_keywords": [
        "rate cut", "rate hike", "interest rate", "fomc", "federal reserve",
        "powell", "warsh", "treasury yield", "降息", "加息", "利率", "美联储", "鲍威尔", "沃什",
        # 数据发布里最重磅的也标为强相关（置顶 + ⭐）
        "cpi", "pce", "nonfarm", "non-farm", "payroll", "jobs report",
        "消费者物价", "非农", "非农就业", "就业报告",
    ],
    "fetch": {"max_items_per_source": 30, "lookback_hours": 24, "request_timeout": 15},
    "schedule": {"timezone": "Asia/Shanghai",
                 "times": ["07:30", "12:30", "21:30"], "run_immediately": False},
    "dedup": {"enabled": True, "seen_file": "data/seen.json", "max_kept": 2000},
    "translate": True,          # 是否把英文标题/摘要翻译成中文（失败则回退原文）
    "panic_index": {"enabled": True},  # 每日「恐慌指数」舆情量化开关
    "market": {"enabled": True, "gold_symbol": "GC=F", "oil_symbol": "BZ=F"},  # 金价/国际油价
    "fed_funds": {"enabled": True, "symbol": "ZQ=F",
                  "target_low": 3.50, "target_high": 3.75},  # 30天联邦基金期货(CME FedWatch 底层真实数据)
    "rate_prob": {"enabled": True},    # 当日「加息概率」量化+定性分析开关
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


# ------------------------- 翻译（英文 -> 中文）-------------------------
def _translate_gtx(text: str) -> str:
    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text[:500]}
    r = requests.get(url, params=params, timeout=8, headers={"User-Agent": UA})
    r.raise_for_status()
    data = r.json()
    parts = [seg[0] for seg in data[0] if seg and seg[0]]
    return "".join(parts).strip()


def _translate_mymemory(text: str) -> str:
    url = "https://api.mymemory.translated.net/get"
    params = {"q": text[:500], "langpair": "en|zh-CN"}
    r = requests.get(url, params=params, timeout=8, headers={"User-Agent": UA})
    r.raise_for_status()
    data = r.json()
    if data.get("responseStatus") == 200 and data.get("responseData", {}).get("translatedText"):
        return data["responseData"]["translatedText"].strip()
    return ""


def translate_to_zh(text: str) -> str:
    """把英文翻译成中文。依次尝试 Google / MyMemory 免费接口；都失败则原样回退，绝不中断推送。"""
    if not text or not text.strip():
        return text
    for fn in (_translate_gtx, _translate_mymemory):
        try:
            out = fn(text)
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            log.warning("翻译通道 %s 失败：%s", fn.__name__, exc)
        time.sleep(0.3)  # 轻微限流，避免免费接口被封
    return text


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


# ------------------------- 恐慌指数（舆情量化）-------------------------
FEAR_WORDS = [
    # 英文
    "recession", "crash", "selloff", "sell-off", "panic", "default", "turmoil",
    "plunge", "crisis", "volatile", "volatility", "rate cut", "rate hike",
    "war", "invasion", "missile", "airstrike", "ceasefire broken", "default risk",
    "oil spike", "stagflation", "bear market", "flight to safety",
    # 中文
    "崩盘", "暴跌", "恐慌", "衰退", "危机", "避险", "战争", "空袭", "导弹",
    "动荡", "违约", "滞胀", "熊市", "抛售", "失业率",
]


def compute_panic_index(news_items: list[dict], social_items: list[dict]) -> dict:
    """根据当日抓取到的新闻 + 社媒信号，量化一个 0-100 的「市场恐慌指数」。

    维度与权重（各维度封顶后相加，最终 clamp 到 100）：
      · 利率快讯条数        最多 8 条  ×4  = 32
      · 其中强相关(⭐)条数  最多 5 条  ×5  = 25
      · 社媒地缘信号条数    最多 6 条  ×3  = 18
      · 其中早期(⚡)信号    最多 4 条  ×6  = 24
      · 命中恐惧词种类      最多 6 种  ×3  = 18
    """
    rate_n = len(news_items)
    strong_n = sum(1 for i in news_items if i.get("strong"))
    social_n = len(social_items)
    early_n = sum(1 for i in social_items if i.get("early"))

    blob = " ".join(
        (i.get("title", "") + " " + i.get("summary", "") + " " + i.get("text", "")).lower()
        for i in news_items + social_items
    )
    fear_hits = sum(1 for w in FEAR_WORDS if w.lower() in blob)

    score = 0
    score += min(rate_n, 8) * 4
    score += min(strong_n, 5) * 5
    score += min(social_n, 6) * 3
    score += min(early_n, 4) * 6
    score += min(fear_hits, 6) * 3
    score = max(0, min(100, score))

    if score <= 20:
        level = "平静"
    elif score <= 40:
        level = "关注"
    elif score <= 60:
        level = "警惕"
    elif score <= 80:
        level = "紧张"
    else:
        level = "极度恐慌"

    return {
        "score": score, "level": level,
        "rate_n": rate_n, "strong_n": strong_n,
        "social_n": social_n, "early_n": early_n, "fear_hits": fear_hits,
    }


def _panic_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


# ------------------------- 市场数据（金价 / 国际油价）-------------------------
def fetch_yahoo(symbol: str, host: str = "query1", timeout: int = 10):
    url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None or prev is None:
        raise ValueError("雅虎返回字段缺失")
    return float(price), float(prev)


def fetch_market_data(cfg: dict) -> dict | None:
    """抓取金价(COMEX)与布伦特原油价格及涨跌幅；任一源失败则单独标记，不中断。"""
    mcfg = cfg.get("market", {})
    if not mcfg.get("enabled", True):
        return None
    out = {}
    for key, sym in (("gold", mcfg.get("gold_symbol", "GC=F")),
                     ("oil", mcfg.get("oil_symbol", "BZ=F"))):
        price = prev = None
        for host in ("query1", "query2"):
            try:
                price, prev = fetch_yahoo(sym, host, timeout=10)
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("市场数据 %s 获取失败(%s): %s", sym, host, exc)
        if price is not None and prev:
            out[key] = {"price": price, "prev": prev,
                        "chg_pct": (price - prev) / prev * 100, "ok": True}
        else:
            out[key] = {"ok": False}
    return out


def fetch_fed_funds(cfg: dict) -> dict | None:
    """抓取 30天联邦基金期货(ZQ=F) → 市场隐含联邦基金利率(EFFR)。

    隐含利率 = 100 - 期货价格（CME FedWatch 官方方法）。
    这是比纯启发式更权威的「真实利率信号」：FedWatch 工具本身就是由
    30天联邦基金期货定价算出来的，我们直接取这层底层真实数据。
    失败不影响其余推送。
    """
    fcfg = cfg.get("fed_funds", {})
    if not fcfg.get("enabled", True):
        return None
    sym = fcfg.get("symbol", "ZQ=F")
    price = prev = None
    for host in ("query1", "query2"):
        try:
            price, prev = fetch_yahoo(sym, host, timeout=10)
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("联邦基金期货 %s 获取失败(%s): %s", sym, host, exc)
    if price is None or prev is None:
        return {"ok": False}
    implied = 100.0 - price                  # 市场隐含当月平均 EFFR(%)
    implied_prev = 100.0 - prev
    chg_bp = (implied - implied_prev) * 100  # 日变化(基点)
    return {"ok": True, "price": price, "implied_effr": implied, "chg_bp": chg_bp,
            "target_low": fcfg.get("target_low", 3.5),
            "target_high": fcfg.get("target_high", 3.75)}


# ------------------------- 当日「加息概率」量化 + 定性 -------------------------
HAWKISH_TERMS = ["rate hike", "加息", "inflation", "cpi", "pce", "tighten",
                 "surge", "hot", "wage", "hawkish", "鹰派", "超预期"]
DOVISH_TERMS = ["rate cut", "降息", "cooling", "slowdown", "recession", "missed",
                "weak", "soft landing", "layoff", "dovish", "鸽派", "低于预期"]


def _news_policy_score(news_items: list[dict]) -> float:
    """对利率新闻做多空打分：+1=全面鹰派(加息)，-1=全面鸽派(降息)。"""
    total_w = 0.0
    signed = 0.0
    for it in news_items:
        hay = (it.get("title", "") + " " + it.get("summary", "")).lower()
        h = len(_match_keywords(hay, HAWKISH_TERMS))
        d = len(_match_keywords(hay, DOVISH_TERMS))
        w = 1.0 if it.get("strong") else 0.5
        signed += (h - d) * w
        total_w += w
    if total_w == 0:
        return 0.0
    return max(-1.0, min(1.0, signed / total_w))


def analyze_rate_prob(news_items: list[dict], panic: dict | None,
                     market: dict | None, fed: dict | None = None) -> dict:
    news_s = _news_policy_score(news_items)

    oil_s = gold_s = 0.0
    oil_txt = gold_txt = ""
    if market:
        o = market.get("oil")
        if o and o.get("ok"):
            oil_s = max(-1.0, min(1.0, o["chg_pct"] / 3.0))   # 油价涨→通胀→偏加息
            oil_txt = f"国际油价{o['chg_pct']:+.1f}%（{'通胀压力↑' if o['chg_pct'] > 0 else '通胀缓解↓'}）"
        g = market.get("gold")
        if g and g.get("ok"):
            gold_s = max(-1.0, min(1.0, -g["chg_pct"] / 3.0))  # 金价涨→避险/降息预期→偏鸽
            gold_txt = f"金价{g['chg_pct']:+.1f}%（{'避险/降息预期↑' if g['chg_pct'] > 0 else '风险偏好回升'}）"

    # 真实利率期货信号：市场隐含联邦基金利率的「日变化」驱动（上行→偏加息）。
    # 这是 CME FedWatch 的底层数据，比金价/油价启发式更贴近真实市场定价。
    fed_s = 0.0
    fed_txt = ""
    if fed and fed.get("ok"):
        chg = fed.get("chg_bp", 0.0)
        fed_s = max(-1.0, min(1.0, chg / 5.0))   # 每 ±5bp 记满格
        direction = "上行" if chg >= 0 else "下行"
        fed_txt = (f"联邦基金期货隐含利率{fed['implied_effr']:.2f}%（{direction}{abs(chg):.1f}bp，"
                   f"目标区间 {fed['target_low']:.2f}–{fed['target_high']:.2f}%）")

    panic_s = 0.0
    ps = panic["score"] if panic else 0
    if ps >= 70:
        panic_s = -0.15   # 极端恐慌通常伴随宽松/救市预期，略压低加息概率
    elif ps <= 20:
        panic_s = 0.05

    # 综合偏置 bias∈[-1,1]，权重：新闻0.40 / 油价0.25 / 金价0.10 / 利率期货0.15 / 恐慌0.10
    bias = (0.40 * news_s + 0.25 * oil_s + 0.10 * gold_s
            + 0.15 * fed_s + 0.10 * panic_s)
    bias = max(-1.0, min(1.0, bias))
    # logistic 映射到 0-100%，bias=0→50%
    prob = round(100 / (1 + math.exp(-bias * 3.0)))

    if prob >= 65:
        label = "偏加息（鹰派）"
    elif prob >= 55:
        label = "中性偏鹰"
    elif prob > 45:
        label = "中性"
    elif prob > 35:
        label = "中性偏鸽"
    else:
        label = "偏降息（鸽派）"

    drivers = [f"新闻面{'偏鹰' if news_s > 0.1 else ('偏鸽' if news_s < -0.1 else '中性')}（{news_s:+.2f}）"]
    if oil_txt:
        drivers.append(oil_txt)
    if gold_txt:
        drivers.append(gold_txt)
    if fed_txt:
        drivers.append("真实利率期货→" + fed_txt)
    drivers.append(f"恐慌指数{ps}" + ("（极端情绪，略压低加息预期）" if ps >= 70
                                      else ("（市场平静）" if ps <= 20 else "（关注）")))
    summary = "；".join(drivers) + f"。综合研判：当日加息概率约 {prob}%，{label}。"

    return {"prob": prob, "bias": round(bias, 3), "label": label, "summary": summary,
            "news_s": round(news_s, 3), "oil_s": round(oil_s, 3), "gold_s": round(gold_s, 3)}


# ------------------------- 排版 -------------------------
def _beijing_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def _render_items(items: list[dict], start_idx: int = 1, translate: bool = True) -> list[str]:
    lines = []
    for i, it in enumerate(items, start_idx):
        star = "⭐ " if it.get("strong") else ""
        early = "⚡ " if it.get("early") else ""
        rel = relative_time(it.get("published"))
        author = f" @{it['author']}" if it.get("author") else ""
        orig = it.get("title") or ""
        title_disp = translate_to_zh(orig) if (translate and orig) else orig
        lines.append(f"{early}{star}**{i}. {title_disp}**")
        if translate and orig and title_disp != orig:
            lines.append(f"> _原文：{orig}_")
        lines.append(f"> 🏷 {it['source']}{author} · {rel}")
        summ = it.get("summary")
        if summ:
            summ_disp = translate_to_zh(summ[:200]) if translate else summ[:200]
            if summ_disp:
                lines.append(f"> 📝 {summ_disp}")
        if it.get("link"):
            lines.append(f"> 🔗 {it['link']}")
        lines.append("")
    return lines


def format_message(items: list[dict], errors: list[str], social_enabled: bool = False,
                   translate: bool = True, panic: dict | None = None,
                   market: dict | None = None, rate_prob: dict | None = None,
                   fed: dict | None = None):
    now = _beijing_now().strftime("%Y-%m-%d %H:%M")
    title = f"📈 美股利率快讯 · 中东局势（{now}）"
    rate_items = [i for i in items if i.get("kind") != "social"]
    social_items = [i for i in items if i.get("kind") == "social"]
    lines = [f"⏰ **{now}（北京时间）** · 利率 **{len(rate_items)}** 条 · 社媒信号 **{len(social_items)}** 条", ""]
    if panic:
        lines.append(f"## 🌡️ 今日市场恐慌指数：{panic['score']} / 100（{panic['level']}）")
        lines.append(f"> `{_panic_bar(panic['score'])}`  {panic['score']}")
        lines.append(f"> 📊 利率快讯 {panic['rate_n']} 条(强相关 {panic['strong_n']}) · "
                     f"社媒信号 {panic['social_n']} 条(早期 {panic['early_n']}) · "
                     f"恐惧词命中 {panic['fear_hits']} 种")
        lines.append("")
    if market or rate_prob:
        lines.append("## 💰 市场数据 & 当日加息概率")
        g = market.get("gold") if market else None
        o = market.get("oil") if market else None
        if g and g.get("ok"):
            arrow = "▲" if g["chg_pct"] >= 0 else "▼"
            lines.append(f"> 🥇 金价：{g['price']:.2f} 美元/盎司  {arrow} {g['chg_pct']:+.2f}%")
        else:
            lines.append("> 🥇 金价：（获取失败，未计入）")
        if o and o.get("ok"):
            arrow = "▲" if o["chg_pct"] >= 0 else "▼"
            lines.append(f"> 🛢️ 国际油价(布伦特)：{o['price']:.2f} 美元/桶  {arrow} {o['chg_pct']:+.2f}%")
        else:
            lines.append("> 🛢️ 国际油价：（获取失败，未计入）")
        # 真实利率期货：市场隐含联邦基金利率（CME FedWatch 底层数据）
        f = fed if (fed and fed.get("ok")) else None
        if f:
            farrow = "▲" if f["chg_bp"] >= 0 else "▼"
            lines.append(f"> 🏦 市场隐含联邦基金利率(ZQ期货)：{f['implied_effr']:.2f}%  "
                         f"{farrow} {f['chg_bp']:+.1f}bp（目标区间 {f['target_low']:.2f}–{f['target_high']:.2f}%）")
        else:
            lines.append("> 🏦 市场隐含联邦基金利率：（获取失败，未计入）")
        if rate_prob:
            lines.append(f"> 🎯 **当日加息概率：{rate_prob['prob']}%**（{rate_prob['label']}）")
            lines.append(f"> 📝 {rate_prob['summary']}")
        lines.append("")
    lines.append("## 📈 利率相关快讯")
    if rate_items:
        lines += _render_items(rate_items, 1, translate)
    else:
        lines.append("🤷 最近时间窗内没有命中「利率相关」的新闻。\n")
    if social_enabled:
        lines.append("## ⚡ 中东局势 · 社媒信号")
        if social_items:
            early_n = sum(1 for i in social_items if i.get("early"))
            if early_n:
                lines.append(f"> 其中 **{early_n}** 条疑似早于主流媒体、尚未被主流覆盖的信号，"
                             f"请重点关注（或影响油价 / 避险 / 利率）\n")
            lines += _render_items(social_items, len(rate_items) + 1, translate)
        else:
            lines.append("🤷 社媒未监测到中东战争相关的新发帖。\n")
    if errors:
        lines.append("⚠️ 部分源抓取异常：")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")
    lines.append("_由 us-stock-news 自动推送_")
    return title, "\n".join(lines)


# ------------------------- 主流程 -------------------------
def run_once(cfg: dict) -> None:
    translate = cfg.get("translate", True)
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
    combined = news_items + social_items

    # 计算每日恐慌指数（基于本次抓取到的全部信号）
    panic = None
    if cfg.get("panic_index", {}).get("enabled", True):
        panic = compute_panic_index(news_items, social_items)
        log.info("今日恐慌指数：%d / 100（%s）", panic["score"], panic["level"])

    # 抓取市场数据（金价 / 国际油价），失败不影响其余推送
    market = None
    if cfg.get("market", {}).get("enabled", True):
        log.info("抓取市场数据（金价 / 国际油价）…")
        market = fetch_market_data(cfg)
        if market:
            for k, v in market.items():
                if v.get("ok"):
                    log.info("%s：%.2f（%+.2f%%）", k, v["price"], v["chg_pct"])
                else:
                    log.warning("%s：获取失败", k)

    # 抓取真实利率期货（30天联邦基金期货 ZQ=F → 市场隐含联邦基金利率；CME FedWatch 底层数据）
    fed = None
    if cfg.get("fed_funds", {}).get("enabled", True):
        log.info("抓取联邦基金期货(ZQ=F) → 市场隐含利率…")
        fed = fetch_fed_funds(cfg)
        if fed and fed.get("ok"):
            log.info("市场隐含联邦基金利率：%.2f%%（%+.1fbp）", fed["implied_effr"], fed["chg_bp"])
        else:
            log.warning("联邦基金期货：获取失败")

    # 当日加息概率（量化 + 定性）：结合新闻多空、金价、油价、真实利率期货、恐慌指数
    rate_prob = None
    if cfg.get("rate_prob", {}).get("enabled", True):
        rate_prob = analyze_rate_prob(news_items, panic, market, fed)
        log.info("当日加息概率：%d%%（%s）", rate_prob["prob"], rate_prob["label"])

    # 全部合并为「一条」推送：早期信号不再单独发，直接带 ⚡ 标记进摘要
    if cfg.get("dedup", {}).get("enabled", True):
        store = SeenStore(cfg["dedup"]["seen_file"], cfg["dedup"].get("max_kept", 2000))
        before = len(combined)
        combined = store.filter_new(combined)
        log.info("去重后待推送 %d 条（去除已推送 %d 条）。", len(combined), before - len(combined))

    title, content = format_message(combined, errors, social_enabled, translate,
                                    panic, market, rate_prob, fed)

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
