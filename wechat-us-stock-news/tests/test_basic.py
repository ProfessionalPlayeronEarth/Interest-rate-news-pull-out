"""基础冒烟测试：关键词匹配 + 去重存储。"""
from src.news import _match_keywords
from src.store import SeenStore


def test_word_boundary_no_false_positive():
    # "rate" 不应匹配 "corporate" / "generate"
    assert _match_keywords("corporate earnings beat", ["rate"]) == []
    # "fed" 不应匹配 "FedEx"
    assert _match_keywords("FedEx delivered packages", ["fed"]) == []
    # 真正的利率相关应命中
    assert _match_keywords("Fed holds interest rates steady", ["fed", "interest rate"])
    # 中文子串匹配
    assert _match_keywords("美联储宣布降息", ["美联储", "降息"])


def test_seen_store_dedup(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    items = [{"id": "a", "title": "x"}, {"id": "b", "title": "y"}]
    fresh = store.filter_new(items)
    assert len(fresh) == 2
    # 第二次同一批应被去重
    assert store.filter_new(items) == []
    # 新条目仍能通过
    assert len(store.filter_new([{"id": "c", "title": "z"}])) == 1
