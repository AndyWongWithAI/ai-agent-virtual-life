"""ScheduleLock 单测:验证作息锁在各时段的强制动作行为"""
from datetime import datetime

from agent_runtime.schedule import ScheduleLock
from agent_runtime.actions import Action


def test_asleep_at_midnight():
    s = ScheduleLock()
    assert s.is_asleep(datetime(2026, 6, 29, 0, 0)) is True
    assert s.is_asleep(datetime(2026, 6, 29, 23, 30)) is True


def test_forced_sleep_at_2am():
    s = ScheduleLock()
    a = s.forced_action(datetime(2026, 6, 29, 2, 0))
    assert a.name == "sleep"


def test_forced_lunch_at_noon():
    s = ScheduleLock()
    a = s.forced_action(datetime(2026, 6, 29, 12, 0))
    assert a.name == "eat"
    assert a.params["meal"] == "lunch"


def test_no_force_at_3pm():
    s = ScheduleLock()
    a = s.forced_action(datetime(2026, 6, 29, 15, 0))
    assert a is None