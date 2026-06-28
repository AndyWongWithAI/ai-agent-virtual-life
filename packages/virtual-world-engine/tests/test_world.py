from virtual_world_engine.world import World
from virtual_world_engine.space import DEFAULT_LOCATIONS


def test_place_and_neighbors():
    w = World()
    w.place("李四", "客厅")
    w.place("王五", "客厅")
    w.place("张伟", "公园")
    n = w.neighbors_of("李四")
    assert "王五" in n  # 同位置
    assert "厨房" in n  # 邻接


def test_snapshot_has_required_keys():
    w = World()
    w.place("李四", "客厅")
    s = w.snapshot("李四")
    for k in ["location", "adjacency", "now_str", "weekday", "weather", "status_bar"]:
        assert k in s


def test_snapshot_exposes_legal_targets():
    """I2/AD1 fix:World.snapshot 应暴露 legal_targets(SSOT),消除 main.py 硬编码"""
    w = World()
    w.place("李四", "客厅")
    s = w.snapshot("李四")
    assert "legal_targets" in s
    # 必须是 DEFAULT_LOCATIONS 的完整副本
    assert set(s["legal_targets"]) == set(DEFAULT_LOCATIONS)
    assert len(s["legal_targets"]) == len(DEFAULT_LOCATIONS)
