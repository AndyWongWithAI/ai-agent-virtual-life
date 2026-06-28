from datetime import datetime
from agent_behavior_orchestrator.scheduler import TickScheduler


def test_day_interval():
    s = TickScheduler()
    assert s.interval_for(datetime(2026, 6, 29, 14, 0)) == 60


def test_night_interval():
    s = TickScheduler()
    assert s.interval_for(datetime(2026, 6, 29, 2, 0)) == 300


def test_boundary_at_23():
    s = TickScheduler()
    assert s.interval_for(datetime(2026, 6, 29, 23, 0)) == 300
    assert s.interval_for(datetime(2026, 6, 29, 22, 59)) == 60