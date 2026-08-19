"""去重存储：用 JSON 记录已推送条目的唯一标识，避免重复推送。"""
from __future__ import annotations

import json
import time
from pathlib import Path


class SeenStore:
    def __init__(self, path: str, max_kept: int = 2000):
        self.path = Path(path)
        self.max_kept = max_kept
        self.seen: dict[str, float] = self._load()

    def _load(self) -> dict[str, float]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.seen, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )

    def contains(self, key: str) -> bool:
        return key in self.seen

    def add(self, key: str) -> None:
        self.seen[key] = time.time()
        if len(self.seen) > self.max_kept:
            # 清理最旧的记录
            oldest = sorted(self.seen.items(), key=lambda kv: kv[1])[: len(self.seen) - self.max_kept]
            for k, _ in oldest:
                self.seen.pop(k, None)
        self._save()

    def filter_new(self, items: list[dict]) -> list[dict]:
        """返回 items 中尚未推送过的条目，并把它们的 id 标记为已见。"""
        fresh = []
        for it in items:
            key = it.get("id") or it.get("link") or it.get("title")
            if not self.contains(key):
                fresh.append(it)
                self.add(key)
        return fresh
