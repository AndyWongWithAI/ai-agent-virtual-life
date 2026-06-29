"""任务 #131(P0 暂停):端点 + tick 守卫测试。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_ctx():
    """最小 ctx + reset _paused。"""
    from town import main as town_main

    # reset module-level flag before each test
    town_main._paused = False

    personas = [{"id": "lisi", "name": "李四", "persona": "32岁程序员"}]
    ctx = {
        "personas": personas,
        "agents": {"lisi": MagicMock()},
        "event_store": MagicMock(),
        "ltm": MagicMock(),
        "stm": MagicMock(),
        "world": MagicMock(),
        "bus": MagicMock(),
    }
    with patch("town.main.ctx", ctx):
        yield TestClient(town_main.app)


def test_status_default_unpaused(client_with_ctx):
    """任务 #131:GET /api/status 默认 paused=False"""
    resp = client_with_ctx.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paused"] is False
    assert data["agents"] == 1


def test_pause_toggles_flag(client_with_ctx):
    """任务 #131:POST /api/pause 后 paused=True;再 POST /api/resume 回 False"""
    r1 = client_with_ctx.post("/api/pause")
    assert r1.status_code == 200
    assert r1.json() == {"paused": True}

    s1 = client_with_ctx.get("/api/status").json()
    assert s1["paused"] is True

    r2 = client_with_ctx.post("/api/resume")
    assert r2.status_code == 200
    assert r2.json() == {"paused": False}

    s2 = client_with_ctx.get("/api/status").json()
    assert s2["paused"] is False


@pytest.mark.asyncio
async def test_run_tick_skipped_when_paused():
    """任务 #131:_paused=True 时 run_tick 整轮早返回,world/tick_decay/bus 全不动。"""
    from town import main as town_main

    # arrange
    town_main._paused = True
    fake_world = MagicMock()
    fake_bus = MagicMock(publish=AsyncMock())
    town_main.ctx = {
        "personas": [],
        "agents": {},
        "event_store": MagicMock(append=AsyncMock()),
        "ltm": MagicMock(),
        "stm": MagicMock(add=AsyncMock()),
        "world": fake_world,
        "bus": fake_bus,
        "reflector": MagicMock(maybe_reflect=AsyncMock()),
    }

    # act
    await town_main.run_tick()

    # assert: world.tick_decay 没有被调,bus.publish 没有被调
    fake_world.tick_decay.assert_not_called()
    fake_bus.publish.assert_not_called()
    fake_world.location_of.assert_not_called()

    # cleanup
    town_main._paused = False


@pytest.mark.asyncio
async def test_run_tick_runs_when_unpaused():
    """任务 #131:sanity — _paused=False 时 run_tick 正常走(NOT regression)"""
    from town import main as town_main

    # arrange: _paused False is default, but be explicit
    town_main._paused = False

    persona_decision_calls = []

    class _FakeAgent:
        def __init__(self, aid):
            self.id = aid
        async def decide(self, snap, user_command=None):
            from agent_runtime.actions import Action
            persona_decision_calls.append(self.id)
            return Action(name="idle", target=None, params={})

    fake_world = MagicMock(
        tick_decay=MagicMock(),
        snapshot=lambda aid: {
            "status_bar": {"hunger": 50, "fatigue": 30, "loneliness": 40, "happiness": 60},
            "location": "李四家",
            "adjacency": [],
            "now_str": "x", "weekday": "x", "weather": "晴",
            "legal_targets": [],
        },
        apply_action=MagicMock(),
        place=MagicMock(),
    )
    fake_event_store = MagicMock(append=AsyncMock())
    fake_stm = MagicMock(add=AsyncMock())
    fake_bus = MagicMock(publish=AsyncMock())
    trigger = MagicMock(should_start=MagicMock(return_value=False))
    reflector = MagicMock(maybe_reflect=AsyncMock())
    town_main.ctx = {
        "personas": [{"id": "lisi", "name": "李四"}, {"id": "wangwu", "name": "王五"}],
        "agents": {"lisi": _FakeAgent("lisi"), "wangwu": _FakeAgent("wangwu")},
        "event_store": fake_event_store,
        "ltm": MagicMock(),
        "stm": fake_stm,
        "world": fake_world,
        "bus": fake_bus,
        "trigger": trigger,
        "reflector": reflector,
    }

    # act
    await town_main.run_tick()

    # assert:决策被调了 2 次(tick_decay + apply_action + bus.publish 都跑了)
    assert len(persona_decision_calls) == 2
    fake_world.tick_decay.assert_called_once()
    assert fake_event_store.append.await_count == 2  # 2 agents × 1 decision event
    assert fake_bus.publish.await_count == 2  # 2 AGENT_DECISION publishes
