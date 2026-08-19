"""配置加载：读取 config.yaml，并与内置默认值合并。"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict = {
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
    "rate_keywords": ["rate", "rates", "interest rate", "federal reserve", "fomc",
                      "powell", "treasury", "yield", "yields", "bond", "bonds",
                      "inflation", "ecb", "rate cut", "rate hike", "降息", "加息",
                      "利率", "美联储", "国债", "通胀", "收益率", "鲍威尔"],
    "strong_keywords": ["rate cut", "rate hike", "interest rate", "fomc",
                        "federal reserve", "powell", "treasury yield", "降息",
                        "加息", "利率", "美联储", "鲍威尔"],
    "fetch": {"max_items_per_source": 30, "lookback_hours": 24, "request_timeout": 15},
    "schedule": {"timezone": "Asia/Shanghai", "times": ["07:30", "12:30", "21:30"],
                 "run_immediately": False},
    "dedup": {"enabled": True, "seen_file": "data/seen.json", "max_kept": 2000},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 覆盖 base。"""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    user_cfg = {}
    if path.exists():
        user_cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = _deep_merge(DEFAULT_CONFIG, user_cfg)

    # seen_file 相对项目根目录解析
    seen_raw = cfg["dedup"].get("seen_file", "data/seen.json")
    cfg["dedup"]["seen_file"] = str(PROJECT_ROOT / seen_raw)
    return cfg


if __name__ == "__main__":
    import json
    print(json.dumps(load_config(), ensure_ascii=False, indent=2))
