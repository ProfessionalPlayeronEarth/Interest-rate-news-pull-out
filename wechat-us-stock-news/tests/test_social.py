"""社媒模块单测：用 mock 数据验证过滤与「早于主流」判断（沙箱网络拉不到真实社媒）。"""
from datetime import datetime, timedelta, timezone
from unittest import mock

from src import social


def _item(title: str, age_min: int, source: str = "Reddit r/worldnews") -> dict:
    return {
        "title": title, "text": title,
        "link": "http://x/" + title, "url": "http://x/" + title,
        "author": "u_test", "source": source,
        "published": datetime.now(timezone.utc) - timedelta(minutes=age_min),
    }


def _cfg(war_kw, lookback=12, early=3):
    return {
        "social": {
            "enabled": True, "lookback_hours": lookback, "early_window_hours": early,
            "max_items_per_source": 20,
            "sources": [{"type": "reddit", "subreddit": "worldnews"}],
            "war_keywords": war_kw,
        },
        "fetch": {"request_timeout": 15},
    }


def test_filter_and_early_detection():
    reddit = [
        _item("Israel launches airstrikes on Gaza", 10),   # 新鲜 + 战争维度，主流未覆盖
        _item("Cute cat picture", 10),                     # 无战争词 -> 过滤
        _item("Oil price spikes on Middle East tension", 60 * 20),  # 20 小时前 -> 超 lookback 过滤
    ]
    cfg = _cfg(["israel", "gaza", "airstrike", "oil price", "middle east"])
    # 主流只覆盖了 oil price / middle east，未覆盖 israel/gaza/airstrike
    mainstream = ["oil price jumps on middle east tension"]

    with mock.patch("src.social.fetch_reddit", return_value=(reddit, None)), \
         mock.patch("src.social.fetch_mastodon", return_value=([], None)), \
         mock.patch("src.social.fetch_rss_social", return_value=([], None)):
        items, errs = social.collect_social(cfg, mainstream)

    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "social"
    assert it["early"] is True
    assert set(it["matched"]) >= {"israel", "gaza", "airstrike"}


def test_not_early_when_mainstream_covered():
    reddit = [_item("Oil price rises after middle east comments", 10)]
    cfg = _cfg(["oil price", "middle east"])
    mainstream = ["oil price rises on middle east tension"]  # 主流已覆盖

    with mock.patch("src.social.fetch_reddit", return_value=(reddit, None)), \
         mock.patch("src.social.fetch_mastodon", return_value=([], None)), \
         mock.patch("src.social.fetch_rss_social", return_value=([], None)):
        items, _ = social.collect_social(cfg, mainstream)

    assert len(items) == 1
    assert items[0]["early"] is False


def test_old_post_not_early():
    reddit = [_item("Israel IDF airstrike reported", 60 * 20)]  # 20 小时前，超 lookback
    cfg = _cfg(["israel", "idf", "airstrike"], lookback=12, early=3)
    with mock.patch("src.social.fetch_reddit", return_value=(reddit, None)), \
         mock.patch("src.social.fetch_mastodon", return_value=([], None)), \
         mock.patch("src.social.fetch_rss_social", return_value=([], None)):
        items, _ = social.collect_social(cfg, [])
    assert items == []  # 超时间窗被过滤
