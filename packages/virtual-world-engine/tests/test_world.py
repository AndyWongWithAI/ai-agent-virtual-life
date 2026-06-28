from virtual_world_engine.world import World


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
