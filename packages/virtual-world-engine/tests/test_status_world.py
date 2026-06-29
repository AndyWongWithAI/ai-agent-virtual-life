"""任务 #113:World 4 维状态真实计算(去掉硬编码 70/40/30/60)"""
import pytest

from virtual_world_engine import (
    World,
    INITIAL_STATUS,
    TICK_DECAY,
    ACTION_EFFECTS,
    STATUS_KEYS,
    LABELS_ZH,
)


def test_world_status_initialized_to_neutral_on_first_place():
    """World.place 后,agent 状态被初始化为中性值(不是 70/40/30/60)"""
    w = World()
    w.place("lisi", "李四家")
    s = w.status_of("lisi")
    assert s == INITIAL_STATUS
    # 关键:不再使用旧的 70/40/30/60
    assert s["hunger"] == 50
    assert s["fatigue"] == 30
    assert s["loneliness"] == 40
    assert s["happiness"] == 60


def test_world_status_of_unknown_agent_returns_initial():
    """未注册 agent 调用 status_of,返回 INITIAL_STATUS 副本(不污染)"""
    w = World()
    s1 = w.status_of("ghost")
    s2 = w.status_of("ghost")
    # 两次返回应是独立副本
    s1["hunger"] = 0
    assert s2["hunger"] == INITIAL_STATUS["hunger"]


def test_tick_decay_increases_hunger_fatigue_loneliness_decreases_happiness():
    """每个 tick 衰减:hunger/fatigue/loneliness +decay, happiness -1"""
    w = World()
    w.place("lisi", "李四家")
    s_before = w.status_of("lisi")
    w.tick_decay()
    s_after = w.status_of("lisi")
    assert s_after["hunger"] == _clamp(s_before["hunger"] + TICK_DECAY["hunger"])
    assert s_after["fatigue"] == _clamp(s_before["fatigue"] + TICK_DECAY["fatigue"])
    assert s_after["loneliness"] == _clamp(s_before["loneliness"] + TICK_DECAY["loneliness"])
    assert s_after["happiness"] == _clamp(s_before["happiness"] + TICK_DECAY["happiness"])


def test_apply_action_eat_reduces_hunger():
    """apply_action('eat') → hunger -40"""
    w = World()
    w.place("lisi", "李四家")
    w.apply_action("lisi", "eat")
    s = w.status_of("lisi")
    assert s["hunger"] == max(0, INITIAL_STATUS["hunger"] + ACTION_EFFECTS["eat"]["hunger"])
    # happiness 略涨
    assert s["happiness"] == min(100, INITIAL_STATUS["happiness"] + ACTION_EFFECTS["eat"]["happiness"])


def test_apply_action_sleep_reduces_fatigue():
    """apply_action('sleep') → fatigue -60"""
    w = World()
    w.place("lisi", "李四家")
    w.apply_action("lisi", "sleep")
    s = w.status_of("lisi")
    assert s["fatigue"] == max(0, INITIAL_STATUS["fatigue"] - 60)


def test_apply_action_talk_reduces_loneliness_increases_happiness():
    """apply_action('talk_to') → loneliness -25, happiness +10"""
    w = World()
    w.place("lisi", "李四家")
    w.apply_action("lisi", "talk_to")
    s = w.status_of("lisi")
    assert s["loneliness"] == max(0, INITIAL_STATUS["loneliness"] - 25)
    assert s["happiness"] == min(100, INITIAL_STATUS["happiness"] + 10)


def test_apply_action_work_increases_fatigue_decreases_happiness():
    """apply_action('work') → fatigue +20, happiness -5"""
    w = World()
    w.place("lisi", "李四家")
    w.apply_action("lisi", "work")
    s = w.status_of("lisi")
    assert s["fatigue"] == min(100, INITIAL_STATUS["fatigue"] + 20)
    assert s["happiness"] == max(0, INITIAL_STATUS["happiness"] - 5)


def test_apply_action_unknown_action_no_op():
    """apply_action 未知 action 名 → 无变化(不抛)"""
    w = World()
    w.place("lisi", "李四家")
    w.apply_action("lisi", "no-such-action")
    assert w.status_of("lisi") == INITIAL_STATUS


def test_apply_action_clamps_to_0_100():
    """apply_action 重复触发,值被 clamp 到 0-100,不能越界"""
    w = World()
    w.place("lisi", "李四家")
    # 吃 10 次,饱度应稳定在 0(不变成 -350)
    for _ in range(10):
        w.apply_action("lisi", "eat")
    s = w.status_of("lisi")
    assert 0 <= s["hunger"] <= 100
    # happiness 每次 +5, 10 次后应到 100
    assert s["happiness"] == 100


def test_snapshot_status_bar_reflects_calculated_state():
    """snapshot 的 status_bar 应反映 World 内部计算的状态(非硬编码)"""
    w = World()
    w.place("lisi", "李四家")
    snap = w.snapshot("lisi")
    # 直接等于 INITIAL_STATUS(没 tick_decay / apply_action 时)
    assert snap["status_bar"] == INITIAL_STATUS
    # 跑一次 tick_decay,snapshot 反映变化
    w.tick_decay()
    snap2 = w.snapshot("lisi")
    assert snap2["status_bar"]["hunger"] == INITIAL_STATUS["hunger"] + TICK_DECAY["hunger"]


def _clamp(v: int) -> int:
    return max(0, min(100, v))
