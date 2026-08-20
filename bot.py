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
            # Reddit 统一走官方 .rss（比 JSON 接口更不易被 GitHub Actions IP 限流/封禁，
            # 彻底消除以往「每次推送末尾报 reddit 抓取错误」的困扰）
            {"type": "rss", "name": "Reddit r/worldnews",
             "url": "https://www.reddit.com/r/worldnews/new/.rss"},
            {"type": "rss", "name": "Reddit r/CombatFootage",
             "url": "https://www.reddit.com/r/CombatFootage/new/.rss"},
            {"type": "rss", "name": "Reddit r/geopolitics",
             "url": "https://www.reddit.com/r/geopolitics/new/.rss"},
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
        # —— 转折点舆情关键词（精简推送的硬筛选）——
        # war_keywords 只决定「是否与中东战争相关」；本列表进一步判定「是否对战局有转折意义」。
        # 命中本列表才进入社媒摘要，其余泛相关帖子不推送，从源头精简内容。
        "turning_point_keywords": [
            # 停火 / 和平转折
            "ceasefire", "truce", "peace deal", "peace agreement", "peace talks",
            "negotiations", "diplomatic", "treaty", "summit", "deal reached",
            # 战事升级 / 转折
            "invasion", "escalation", "ground offensive", "airstrike", "airstrikes",
            "missile attack", "drone strike", "bombing", "shelling", "offensive",
            "retaliation", "counterattack",
            # 重大转折事件
            "surrender", "retreat", "breakthrough", "captured", "fallen", "massacre",
            "genocide", "nuclear", "decapitation", "assassination", "collapse",
            # 制裁 / 封锁 / 能源转折
            "sanctions", "embargo", "blockade", "oil embargo", "strait of hormuz",
            "hormuz", "pipeline", "oil price spike",
            # 中文
            "停火", "停战", "和谈", "和平协议", "撤军", "投降", "入侵", "空袭",
            "导弹", "封锁", "制裁", "斩首", "核", "升级", "外交", "条约", "峰会",
            "和谈", "破城", "沦陷", "突围",
        ],
        "turning_only": True,   # True=只推送转折点舆情（精简）；False=推送全部战争相关
        "max_turning": 10,      # 转折点舆情最多保留条数（按 early 优先 + 时间新排序）
    },
    "hormuz": {
        "enabled": True,
        "api_base": "https://hormuz.data-tracking.net/api",
        "drop_threshold": 0.25,   # 通航量较基线下降超 25% 即判定为异常
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
def _needs_translation(text: str) -> bool:
    """仅当文本含英文字母、且几乎不含中文时，才需要翻译成中文。"""
    if not text:
        return False
    has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in text)
    has_latin = any(ch.isascii() and ch.isalpha() for ch in text)
    return has_latin and not has_cjk


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


def _translate_libre(text: str) -> str:
    """第三备用翻译通道（LibreTranslate 公共实例，无需密钥）。"""
    url = "https://translate.terraprint.co/translate"
    try:
        r = requests.post(url, json={"q": text[:500], "source": "en", "target": "zh", "format": "text"},
                          timeout=10, headers={"User-Agent": UA})
        r.raise_for_status()
        return (r.json().get("translatedText") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def translate_to_zh(text: str) -> str:
    """把英文翻译成中文。依次尝试 Google → MyMemory → LibreTranslate 三个免费接口；
    任一返回有效译文即采用；全部失败则原样回退，绝不中断推送。
    若某个接口把原文「原样返回」（等同于没翻译），则继续尝试下一个通道，避免漏翻。"""
    if not text or not text.strip():
        return text
    if not _needs_translation(text):
        return text  # 已含中文，无需翻译
    stripped = text.strip()
    for fn in (_translate_gtx, _translate_mymemory, _translate_libre):
        try:
            out = fn(text)
            if out and out != stripped:
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


def fetch_rss_social(url: str, limit: int, timeout: int, name: str = ""):
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
        src_name = name or (feed.feed.get("title", url) if hasattr(feed, "feed") else url)
        out.append({
            "title": title,
            "text": f"{title}\n{summary}",
            "link": e.get("link", ""),
            "url": e.get("link", ""),
            "author": e.get("author", ""),
            "published": published,
            "source": src_name,
        })
    return out, None


def collect_social(cfg: dict, mainstream_texts=None):
    s_cfg = cfg.get("social", {})
    if not s_cfg.get("enabled", False):
        return [], []

    war_kw = s_cfg.get("war_keywords", [])
    tp_kw = s_cfg.get("turning_point_keywords", [])
    turning_only = s_cfg.get("turning_only", True)
    max_turning = s_cfg.get("max_turning", 10)
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
        if stype == "mastodon":
            chunk, err = fetch_mastodon(src.get("instance", ""), src.get("tag", ""), max_per, timeout)
        else:
            chunk, err = fetch_rss_social(src.get("url", ""), max_per, timeout, src.get("name", ""))
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
            # 「转折点」判定：仅命中转折级关键词，才视为对战局有转折意义的舆情
            tp_hits = _match_keywords(f"{it.get('title', '')} {it.get('text', '')}", tp_kw)
            it["turning"] = bool(tp_hits)
            it["kind"] = "social"
            it["id"] = it.get("url") or it.get("link") or it.get("title")
            fresh = (mins is not None and mins <= early_window * 60)
            unique_kw = set(m.lower() for m in matched) - mainstream_kw_set
            it["early"] = bool(fresh and unique_kw)
            it["fresh_minutes"] = mins
            items.append(it)

    # 精简：仅保留「转折点舆情」，从源头减少推送噪声
    if turning_only:
        items = [it for it in items if it.get("turning")]
        items.sort(key=lambda x: (not x.get("early"),
                                  -(x["published"].timestamp() if x.get("published") else 0)))
        if len(items) > max_turning:
            items = items[:max_turning]
    else:
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


def fetch_hormuz_traffic(cfg: dict) -> dict | None:
    """抓取霍尔木兹海峡通航数据（免费 JSON API，每30分钟轮询 AIS）。

    两个端点：
      - /api/crossings/daily ：每日通行统计（in_strait/inbound/outbound 计数）
      - /api/ships           ：当前区域内每艘船的实时快照（含船型/区域/速度）
    用「最新完整日通航量」对比近 7–14 日基线，骤降即视为中东局势升级的早期硬指标
    （船减少 → 油价/避险/利率早于主流媒体反应）。失败时返回 {"ok": False}，不影响其余推送。
    """
    hcfg = cfg.get("hormuz", {})
    if not hcfg.get("enabled", True):
        return None
    base = hcfg.get("api_base", "https://hormuz.data-tracking.net/api")
    thr = hcfg.get("drop_threshold", 0.25)
    out = {"ok": False, "source": base}
    try:
        r = requests.get(f"{base}/crossings/daily", timeout=15, headers={"User-Agent": UA})
        r.raise_for_status()
        rows = r.json()
        series = sorted((x["day"], x["count"]) for x in rows
                        if x.get("direction") == "in_strait" and x.get("count") is not None)
        if len(series) >= 2:
            latest_day, latest = series[-1]                       # 可能当天未统计完
            prev_day, prev = series[-2]                           # 最新完整日
            hist = [c for _, c in series[:-1]][-14:]              # 基线排除当天
            baseline = sum(hist) / len(hist) if hist else float(prev)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            out.update({
                "latest_day": latest_day, "latest_count": latest,
                "prev_day": prev_day, "prev_count": prev,
                "baseline": round(baseline, 1),
                "latest_incomplete": latest_day >= today,
            })
            change = (prev - baseline) / baseline if baseline else 0.0
            out["change_pct"] = round(change * 100, 1)
            out["abnormal"] = change <= -thr
            out["ok"] = True
    except Exception as e:  # noqa: BLE001
        log.warning("霍尔木兹通行统计获取失败: %s", e)
    # 实时快照（当前海峡内船数 + 油轮数），不受「日聚合未完成」影响
    try:
        r2 = requests.get(f"{base}/ships", timeout=15, headers={"User-Agent": UA})
        r2.raise_for_status()
        ships = r2.json()
        if isinstance(ships, list):
            strait = [s for s in ships if s.get("zone") == "strait"]
            tanker_cats = ("Crude Oil Tanker", "LNG Tanker", "LPG/LNG Tanker",
                           "Oil Products Tanker", "Chemical/Oil Products Tanker")
            tankers = [s for s in strait if s.get("ship_category") in tanker_cats]
            out["snapshot_total"] = len(ships)
            out["snapshot_strait"] = len(strait)
            out["snapshot_tankers"] = len(tankers)
            out["snapshot_ts"] = strait[0].get("timestamp") if strait else None
    except Exception as e:  # noqa: BLE001
        log.warning("霍尔木兹实时船只获取失败: %s", e)
    return out


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


def market_rate_prob(fed: dict | None) -> dict | None:
    """市场隐含加息概率：直接由 ZQ=F（30天联邦基金期货）定价推导（CME FedWatch 同源）。

    这一路就是『市场真实定价』本身，故不再压权重，作为每日推送的【基准概率】。
    与 analyze_rate_prob（综合研判）并排展示，二者之差即另类信号带来的偏移。
    """
    if not fed or not fed.get("ok"):
        return None
    chg = fed.get("chg_bp", 0.0)
    fed_s = max(-1.0, min(1.0, chg / 5.0))   # 每 ±5bp 记满格
    bias = 0.15 * fed_s                        # 与 analyze_rate_prob 中 fed 一路保持一致（±5bp→±11%）
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
    direction = "上行" if chg >= 0 else "下行"
    return {"prob": prob, "bias": round(bias, 3), "label": label,
            "summary": f"市场隐含利率{direction}{abs(chg):.1f}bp（目标区间 "
                       f"{fed['target_low']:.2f}–{fed['target_high']:.2f}%），CME FedWatch 同源定价。"}


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


def format_social_abstract(social_items: list[dict], translate: bool = True) -> list[str]:
    """社媒「转折点舆情」摘要：只给对战争走势有转折意义的帖子。

    每条 = 翻译后的要点 + 可点击的 🔗 图标（不展示冗长网址）：
        1. 中文摘要 [🔗](原始链接)
        2. 中文摘要 [🔗](原始链接)
    """
    lines = []
    n = len(social_items)
    early_n = sum(1 for i in social_items if i.get("early"))
    lines.append(f"> 📋 **转折点舆情摘要（精选 {n} 条，其中早期 ⚡ {early_n} 条）**：")
    if not n:
        lines.append("> 🤷 今日未监测到「对战争走势有转折意义」的社媒舆情，相关监测仍在运行。")
        return lines
    for idx, it in enumerate(social_items, 1):
        raw = (it.get("title") or it.get("text") or "")[:120]
        disp = translate_to_zh(raw) if (translate and raw) else raw
        early = "⚡ " if it.get("early") else ""
        link = it.get("url") or it.get("link") or ""
        icon = f" [🔗]({link})" if link else ""   # 点击图标即可进入原始帖子
        src = it.get("source", "")
        lines.append(f"> {early}{idx}. {disp}{icon}")
        lines.append(f"> · 来源：{src} · {relative_time(it.get('published'))}")
    if early_n:
        lines.append("> ⚠️ 其中早期信号可能早于主流媒体，请重点关注（或影响油价 / 避险 / 利率）。")
    return lines


def format_message(items: list[dict], errors: list[str], social_enabled: bool = False,
                   translate: bool = True, panic: dict | None = None,
                   market: dict | None = None, rate_prob: dict | None = None,
                   market_prob: dict | None = None, fed: dict | None = None,
                   hormuz: dict | None = None, anomalies: list | None = None):
    now = _beijing_now().strftime("%Y-%m-%d %H:%M")
    title = f"📈 美股利率快讯 · 中东局势（{now}）"
    rate_items = [i for i in items if i.get("kind") != "social"]
    social_items = [i for i in items if i.get("kind") == "social"]
    lines = [f"⏰ **{now}（北京时间）** · 利率 **{len(rate_items)}** 条 · 社媒信号 **{len(social_items)}** 条", ""]

    # —— 置顶模块：市场情绪指数 + 两个加息概率（市场隐含 + 综合研判）——
    lines.append("## 🔝 今日速览（置顶）")
    if panic:
        lines.append(f"> 🌡️ **今日市场情绪指数（原恐慌指数）：{panic['score']} / 100（{panic['level']}）**")
        lines.append(f"> `{_panic_bar(panic['score'])}`  {panic['score']}")
        lines.append(f"> 📊 利率快讯 {panic['rate_n']} 条(强相关 {panic['strong_n']}) · "
                     f"社媒信号 {panic['social_n']} 条(早期 {panic['early_n']}) · "
                     f"恐惧词命中 {panic['fear_hits']} 种")
    f = fed if (fed and fed.get("ok")) else None
    if f:
        farrow = "▲" if f["chg_bp"] >= 0 else "▼"
        lines.append(f"> 🏦 市场隐含联邦基金利率(ZQ期货)：{f['implied_effr']:.2f}%  "
                     f"{farrow} {f['chg_bp']:+.1f}bp（目标区间 {f['target_low']:.2f}–{f['target_high']:.2f}%）")
    else:
        lines.append("> 🏦 市场隐含联邦基金利率：（获取失败，未计入）")
    if market_prob:
        lines.append(f"> 📊 **市场隐含加息概率(ZQ=F)：{market_prob['prob']}%**（{market_prob['label']}）← 真实市场定价(基准)")
    if rate_prob:
        lines.append(f"> 🎯 **综合研判加息概率(多维信号)：{rate_prob['prob']}%**（{rate_prob['label']}）")
        if market_prob:
            diff = rate_prob["prob"] - market_prob["prob"]
            if abs(diff) >= 3:
                tag = "偏高" if diff > 0 else "偏低"
                lines.append(f"> 🔍 综合研判较市场隐含{tag} {abs(diff)} 个百分点"
                             f"（差异来自新闻/油价/金价/恐慌等另类信号）")
    lines.append("")

    # —— 今日异动提醒 ——
    if anomalies:
        lines.append("## ⚠️ 今日异动提醒")
        for a in anomalies:
            lines.append(f"> {a}")
        lines.append("")

    # —— 市场数据（金价 / 国际油价）——
    if market:
        lines.append("## 💰 市场数据（金价 / 国际油价）")
        g = market.get("gold")
        o = market.get("oil")
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
        lines.append("")

    # —— 霍尔木兹海峡通航监测（移到市场数据下方）——
    if hormuz and hormuz.get("ok"):
        lines.append("## ⚓ 霍尔木兹海峡通航监测")
        chg = hormuz.get("change_pct", 0.0)
        arrow = "↓" if chg < 0 else "↑"
        lines.append(f"> 🚢 最新完整日通航：{hormuz['prev_count']} 艘"
                     f"（基线 {hormuz['baseline']} 艘，{arrow} {abs(chg):.0f}%）")
        if hormuz.get("snapshot_strait") is not None:
            lines.append(f"> 🛢️ 实时海峡内在航：{hormuz['snapshot_strait']} 艘"
                         f"（油轮/LNG {hormuz['snapshot_tankers']} 艘）")
        if hormuz.get("abnormal"):
            lines.append("> ⚠️ **通航量骤降**：中东局势可能升级，油价/避险/利率或提前反应（早于主流媒体）。")
        if hormuz.get("latest_incomplete"):
            lines.append("> ℹ️ 当日数据仍在统计中，以上为最近完整日口径。")
        lines.append("")

    # —— 利率相关快讯 ——
    lines.append("## 📈 利率相关快讯")
    if rate_items:
        lines += _render_items(rate_items, 1, translate)
    else:
        lines.append("🤷 最近时间窗内没有命中「利率相关」的新闻。\n")

    # —— 社媒信号（仅摘要：列点 + 可点击 🔗 图标）——
    if social_enabled:
        lines.append("## 🔥 中东局势 · 转折点舆情（精选）")
        lines += format_social_abstract(social_items, translate)
        lines.append("")

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
        log.info("抓取社媒舆情（Reddit RSS / Mastodon / Telegram），仅保留转折点舆情…")
        mainstream_texts = fetch_recent_texts(cfg)
        social_items, social_errors = collect_social(cfg, mainstream_texts)
        log.info("转折点舆情 %d 条（其中早期信号 %d 条）；源异常 %d 个。",
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

    # 市场隐含加息概率（ZQ=F 真实定价，作为基准，与综合研判并排展示）
    market_prob = market_rate_prob(fed)
    if market_prob:
        log.info("市场隐含加息概率(ZQ=F)：%d%%（%s）", market_prob["prob"], market_prob["label"])

    # 霍尔木兹海峡通航监测（早期硬指标：船真不走，比新闻/油价更早）
    hormuz = None
    if cfg.get("hormuz", {}).get("enabled", True):
        log.info("抓取霍尔木兹海峡通航数据…")
        hormuz = fetch_hormuz_traffic(cfg)
        if hormuz and hormuz.get("ok"):
            log.info("霍尔木兹最新完整日通航 %d 艘（基线 %.1f，变化 %+.1f%%）",
                     hormuz["prev_count"], hormuz["baseline"], hormuz["change_pct"])
        else:
            log.warning("霍尔木兹通航数据获取失败")

    # 异动提醒：关注的数据出现明显波动时，主动在推送里提示
    anomalies = []
    if panic and panic["score"] >= 60:
        anomalies.append(f"🌡️ 市场情绪指数升至 {panic['score']}（{panic['level']}），需警惕")
    if market:
        g = market.get("gold")
        if g and g.get("ok") and abs(g["chg_pct"]) >= 2:
            anomalies.append(f"🥇 金价异动 {g['chg_pct']:+.2f}%（{'大涨' if g['chg_pct'] > 0 else '大跌'}）")
        o = market.get("oil")
        if o and o.get("ok") and abs(o["chg_pct"]) >= 3:
            anomalies.append(f"🛢️ 国际油价异动 {o['chg_pct']:+.2f}%（{'大涨' if o['chg_pct'] > 0 else '大跌'}）")
    if fed and fed.get("ok") and abs(fed["chg_bp"]) >= 3:
        anomalies.append(f"🏦 市场隐含联邦基金利率变动 {fed['chg_bp']:+.1f}bp"
                         f"（目标区间 {fed['target_low']:.2f}–{fed['target_high']:.2f}%）")
    if market_prob and rate_prob:
        diff = rate_prob["prob"] - market_prob["prob"]
        if abs(diff) >= 15:
            tag = "高于" if diff > 0 else "低于"
            anomalies.append(f"🎯 综合研判加息概率{tag}市场隐含 {abs(diff)} 个百分点"
                             f"（{rate_prob['prob']}% vs {market_prob['prob']}%）")
    if hormuz and hormuz.get("ok") and hormuz.get("abnormal"):
        anomalies.append("⚓ 霍尔木兹海峡通航量骤降，中东局势或升级")
    if anomalies:
        log.info("今日异动 %d 项：%s", len(anomalies), "；".join(anomalies))

    # 全部合并为「一条」推送：早期信号不再单独发，直接带 ⚡ 标记进摘要
    if cfg.get("dedup", {}).get("enabled", True):
        store = SeenStore(cfg["dedup"]["seen_file"], cfg["dedup"].get("max_kept", 2000))
        before = len(combined)
        combined = store.filter_new(combined)
        log.info("去重后待推送 %d 条（去除已推送 %d 条）。", len(combined), before - len(combined))

    title, content = format_message(combined, errors, social_enabled, translate,
                                    panic, market, rate_prob, market_prob, fed, hormuz, anomalies)

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
