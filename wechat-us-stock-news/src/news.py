"""新闻抓取与过滤：RSS 拉取 -> 利率关键词命中 -> 时间窗过滤 -> 排序。"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import feedparser
import requests

logger = logging.getLogger("us-stock-news")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _parse_date(entry: dict):
    """尽量从 RSS 条目里解析出 UTC 时间。"""
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
            parsed = feedparser._parse_date(val)  # type: ignore[attr-defined]
            if parsed:
                return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    return None


def fetch_source(name: str, url: str, timeout: int, max_items: int) -> tuple[list[dict], str | None]:
    """抓取单个 RSS 源，返回条目列表与（可选）错误信息。"""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        return [], f"抓取失败: {exc}"

    items: list[dict] = []
    for e in feed.entries[:max_items]:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        summary = (e.get("summary") or e.get("description") or "").strip()
        # 去掉 HTML 标签，只留纯文本
        summary = _strip_html(summary)
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


def _strip_html(text: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    """中文关键词用子串匹配；英文关键词用单词边界匹配，避免误命中
    （如 'rate' 不应匹配 'corporate'/'generate'，'fed' 不应匹配 'FedEx'）。
    英文词尾允许可选 's'，以兼容复数（rates/bonds/airstrikes 等）。"""
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


def collect_news(cfg: dict) -> list[dict]:
    """汇总所有源，过滤出利率相关且落在时间窗内的新闻，按相关度+时间排序。"""
    rate_kw = cfg.get("rate_keywords", [])
    strong_kw = cfg.get("strong_keywords", [])
    fetch_cfg = cfg.get("fetch", {})
    lookback = fetch_cfg.get("lookback_hours", 24)
    now = datetime.now(timezone.utc)

    all_items: list[dict] = []
    errors: list[str] = []
    for src in cfg.get("sources", []):
        items, err = fetch_source(
            src["name"], src["url"],
            fetch_cfg.get("request_timeout", 15),
            fetch_cfg.get("max_items_per_source", 30),
        )
        if err:
            errors.append(f"{src['name']}: {err}")
            continue
        all_items.extend(items)

    results: list[dict] = []
    for it in all_items:
        hay = f"{it['title']} {it['summary']}"
        hits = _match_keywords(hay, rate_kw)
        if not hits:
            continue
        # 时间窗过滤
        pub = it.get("published")
        if lookback and pub:
            age_h = (now - pub).total_seconds() / 3600.0
            if age_h > lookback:
                continue
        it["matched"] = hits
        it["strong"] = bool(_match_keywords(hay, strong_kw))
        # 相关度评分：强相关 +2，命中关键词数量 +1/个
        it["score"] = (2 if it["strong"] else 0) + len(hits)
        # 没有时间的放最前（更可能重要），否则按时间倒序
        it["_sortkey"] = pub or now
        results.append(it)

    results.sort(key=lambda x: (x["score"], x["_sortkey"]), reverse=True)
    for it in results:
        it.pop("_sortkey", None)
    return results, errors


def fetch_recent_texts(cfg: dict) -> list[str]:
    """抓取主流 RSS 源近期标题+摘要文本，供社媒模块做「早于主流」交叉比对。"""
    texts: list[str] = []
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


def relative_time(dt: datetime | None) -> str:
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
