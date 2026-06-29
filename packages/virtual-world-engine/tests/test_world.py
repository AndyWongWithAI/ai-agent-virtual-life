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


def test_snapshot_status_bar_is_dict():
    """snapshot 的 status_bar 必须是 dict,前端可视化需要结构化(V2 task #84)
    任务 #114:内部 key 用英文(hunger/fatigue/loneliness/happiness),
    中文 label 由 town API 层做映射(参见 apps/town/src/town/main.py)
    """
    w = World()
    w.place("lisi", "李四家")
    snap = w.snapshot("lisi")
    assert isinstance(snap["status_bar"], dict)
    # 内部 key 英文(stable)
    for k in ("hunger", "fatigue", "loneliness", "happiness"):
        assert k in snap["status_bar"], f"missing internal key: {k}"
    # 4 个值都是 0-100 整数
    for v in snap["status_bar"].values():
        assert 0 <= v <= 100
