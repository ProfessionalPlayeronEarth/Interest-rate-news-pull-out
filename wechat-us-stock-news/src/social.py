"""社媒舆情监测：抓取 Reddit / Mastodon / Telegram(RSSHub) 上
关于中东战争等「利率敏感地缘政治」的早期个人发帖，并与主流媒体做交叉比对，
标记出「可能早于主流媒体报道」的信号。

说明：X(Twitter)、TikTok 已无可用免费 API，本模块不含这两者。
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import feedparser
import requests

from src.news import _match_keywords, _strip_html, fetch_source, relative_time

log = logging.getLogger("us-stock-news")

_UA = "us-stock-news/1.0 (geopolitical OSINT monitor; +https://github.com)"


def _parse_reddit_time(epoch: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def fetch_reddit(subreddit: str, limit: int, timeout: int) -> tuple[list[dict], str | None]:
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
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
        body = f"{title}\n{text}"
        out.append({
            "title": title,
            "text": body,
            "link": "https://www.reddit.com" + d.get("permalink", ""),
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "author": d.get("author", ""),
            "published": _parse_reddit_time(d.get("created_utc")),
            "source": f"Reddit r/{subreddit}",
        })
    return out, None


def fetch_mastodon(instance: str, tag: str, limit: int, timeout: int) -> tuple[list[dict], str | None]:
    url = f"https://{instance}/api/v1/timelines/tag/{tag}?limit={limit}"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
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


def fetch_rss_social(url: str, limit: int, timeout: int) -> tuple[list[dict], str | None]:
    """用于 Telegram 频道经 RSSHub 桥接的 RSS（或任意 RSS）。"""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
        feed = feedparser.parse(r.content)
    except Exception as exc:  # noqa: BLE001
        return [], f"RSS {url} 抓取失败: {exc}"

    out = []
    for e in feed.entries[:limit]:
        title = e.get("title", "")
        summary = _strip_html(e.get("summary", "") or e.get("description", ""))[:400]
        body = f"{title}\n{summary}"
        published = None
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                published = datetime.fromtimestamp(time.mktime(e[key]), tz=timezone.utc)
                break
        out.append({
            "title": title,
            "text": body,
            "link": e.get("link", ""),
            "url": e.get("link", ""),
            "author": e.get("author", ""),
            "published": published,
            "source": feed.feed.get("title", url) if hasattr(feed, "feed") else url,
        })
    return out, None


def _fresh_minutes(published: datetime | None) -> float | None:
    if not published:
        return None
    now = datetime.now(timezone.utc)
    return (now - published).total_seconds() / 60.0


def collect_social(cfg: dict, mainstream_texts: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """返回 (社媒条目, 异常列表)。条目含 kind='social' 与 early 标记。"""
    s_cfg = cfg.get("social", {})
    if not s_cfg.get("enabled", False):
        return [], []

    war_kw = s_cfg.get("war_keywords", [])
    lookback = s_cfg.get("lookback_hours", 12)
    early_window = s_cfg.get("early_window_hours", 3)
    max_per = s_cfg.get("max_items_per_source", 20)
    timeout = cfg.get("fetch", {}).get("request_timeout", 15)

    # 主流已覆盖的战争关键词集合（用于"早于主流"判断）
    mainstream_kw_set: set[str] = set()
    if mainstream_texts:
        blob = " ".join(mainstream_texts).lower()
        for k in war_kw:
            kl = k.lower()
            if kl.isascii() and kl.replace(" ", "").isalpha():
                if re.search(rf"(?<![a-z]){re.escape(kl)}(?![a-z])", blob):
                    mainstream_kw_set.add(kl)
            elif kl in blob:
                mainstream_kw_set.add(kl)

    items: list[dict] = []
    errors: list[str] = []
    for src in s_cfg.get("sources", []):
        stype = src.get("type", "rss")
        if stype == "reddit":
            chunk, err = fetch_reddit(src.get("subreddit", ""), max_per, timeout)
        elif stype == "mastodon":
            chunk, err = fetch_mastodon(src.get("instance", ""), src.get("tag", ""), max_per, timeout)
        else:  # rss
            chunk, err = fetch_rss_social(src.get("url", ""), max_per, timeout)
        if err:
            errors.append(err)
            continue

        for it in chunk:
            # 时间窗过滤
            mins = _fresh_minutes(it["published"])
            if lookback > 0 and mins is not None and mins > lookback * 60:
                continue
            # 战争关键词过滤
            matched = _match_keywords(it["text"], war_kw)
            if not matched:
                continue
            it["matched"] = matched
            it["kind"] = "social"
            it["id"] = it.get("url") or it.get("link") or it.get("title")
            # 早于主流判断：新鲜 + 含有主流尚未覆盖的战争维度
            fresh = (mins is not None and mins <= early_window * 60)
            unique_kw = set(m.lower() for m in matched) - mainstream_kw_set
            it["early"] = bool(fresh and unique_kw)
            it["fresh_minutes"] = mins
            items.append(it)

    # 排序：早期信号优先，其次按时间倒序
    items.sort(key=lambda x: (not x.get("early"), -(x["published"].timestamp() if x.get("published") else 0)))
    return items, errors
