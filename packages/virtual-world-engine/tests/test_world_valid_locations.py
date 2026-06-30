"""任务 T6:World 支持 valid_locations 参数(阶段 3 自定义地点)
测试契约:
  1. 不传 valid_locations → 回退 DEFAULT_LOCATIONS(向后兼容 6 个旧测试)
  2. 传 valid_locations=[自定义地点] → place 不再 assert 失败
  3. 传 valid_locations 后 → snapshot.legal_targets 返自定义列表
  4. assert 仍生效:不在 valid_locations 的地点仍抛 AssertionError
"""
from virtual_world_engine import World
from virtual_world_engine.space import DEFAULT_LOCATIONS


def test_default_locations_when_no_arg():
    """不传参 → 走 DEFAULT_LOCATIONS,行为完全不变(向后兼容)"""
    w = World()
    w.place("a1", "李四家")  # DEFAULT_LOCATIONS 里有,不应抛
    snap = w.snapshot("a1")
    assert sorted(snap["legal_targets"]) == sorted(DEFAULT_LOCATIONS)


def test_custom_valid_locations_accept():
    """传 valid_locations=[自定义地点] → place 不再 assert 失败"""
    w = World(valid_locations=["魔法学院", "精灵森林"])
    w.place("a1", "魔法学院")  # 不在 DEFAULT_LOCATIONS,但在 valid_locations,应通过
    assert w.location_of("a1") == "魔法学院"


def test_custom_valid_locations_snapshot():
    """传 valid_locations 后 → legal_targets 返自定义列表"""
    custom = ["魔法学院", "精灵森林", "龙穴"]
    w = World(valid_locations=custom)
    w.place("a1", "魔法学院")
    snap = w.snapshot("a1")
    assert sorted(snap["legal_targets"]) == sorted(custom)


def test_custom_valid_locations_still_reject_unknown():
    """assert 仍生效:不在 valid_locations 的地点仍抛 AssertionError"""
    w = World(valid_locations=["魔法学院", "精灵森林"])
    w.place("a1", "魔法学院")
    try:
        w.place("a1", "李四家")  # 不在 valid_locations,应抛
        raised = False
    except AssertionError:
        raised = True
    assert raised, "应当对 valid_locations 外的地点抛 AssertionError"