"""任务 #125(Bug4):/api/events 端点测试 + 任务 #127(B1):/api/agents 实时状态字段测试。

任务 #126(/api/memory-summaries)+ 测试已移除 ——
B 方案决策:6h LTM 反思仅服务 LLM decision prompt,不再直接展示给用户。
"""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory_reflection import Event


@pytest.fixture
def client_with_ctx():
    """最小 ctx:2 agent + fake event_store / world(扩 latest_action_of)。"""
    from town.main import app

    personas = [
        {"id": "lisi", "name": "李四", "persona": "32岁程序员"},
        {"id": "wangwu", "name": "王五", "persona": "30岁产品经理"},
    ]
    # 任务 #125:/api/events 依赖 ctx["event_store"].list_events
    fake_store = MagicMock()
    fake_store.list_events = AsyncMock(return_value=[
        Event(agent_id="wangwu", kind="decision", content="回家", ts=datetime.datetime(2026, 6, 29, 12, 0)),
        Event(agent_id="lisi", kind="dialogue", content="吃午饭", ts=datetime.datetime(2026, 6, 29, 11, 0)),
        Event(agent_id="lisi", kind="decision", content="去公园", ts=datetime.datetime(2026, 6, 29, 10, 0)),
    ])

    fake_world = MagicMock()
    fake_world.location_of = lambda aid: "李四家" if aid == "lisi" else "王五家"
    # 任务 #127(B1):World.latest_action_of(agent_id) -> (action_name, target)
    # 假装 wangwu 最近去了公园,lisi 在吃饭
    fake_world.latest_action_of = lambda aid: (
        ("go_to", "公园") if aid == "wangwu" else ("eat", None)
    )
    # 4 维状态用 INITIAL_STATUS(各 50/30/40/60 范围)fake
    fake_world.status_of = lambda aid: {"hunger": 70, "fatigue": 40, "loneliness": 30, "happiness": 60}

    ctx = {
        "personas": personas,
        "agents": {"lisi": MagicMock(), "wangwu": MagicMock()},
        "event_store": fake_store,
        # ltm / stm 在 /api/agents 与 /api/events 路径都不读,留空 mocks
        "ltm": MagicMock(),
        "stm": MagicMock(),
        "world": fake_world,
    }
    with patch("town.main.ctx", ctx):
        yield TestClient(app)


# --- 任务 #125: /api/events(Bug 4 修活动记录刷新空)---


def test_api_events_returns_ascending(client_with_ctx):
    """任务 #125:/api/events 必须按 ts 升序返回(前端 append 后时序正确)"""
    resp = client_with_ctx.get("/api/events?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    # list_events 返回 desc;端点 reverse 后为 asc
    assert data[0]["content"] == "去公园"   # 最早
    assert data[-1]["content"] == "回家"   # 最晚


def test_api_events_includes_required_fields(client_with_ctx):
    """任务 #125:每个事件必须包含 ts / agent_id / kind / content"""
    resp = client_with_ctx.get("/api/events?limit=10")
    data = resp.json()
    for ev in data:
        assert "ts" in ev
        assert "agent_id" in ev
        assert "kind" in ev
        assert "content" in ev


def test_api_events_rejects_invalid_limit(client_with_ctx):
    """任务 #125:limit 必须在 1-200,越界返 400"""
    resp = client_with_ctx.get("/api/events?limit=0")
    assert resp.status_code == 400
    resp = client_with_ctx.get("/api/events?limit=999")
    assert resp.status_code == 400


# --- 任务 #127(B1):/api/agents 扩展实时状态字段 ---


def test_api_agents_includes_required_base_fields(client_with_ctx):
    """/api/agents 必须含 id/name/location(已有契约保留)"""
    resp = client_with_ctx.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for a in data:
        assert "id" in a
        assert "name" in a
        assert "location" in a


def test_api_agents_includes_current_action(client_with_ctx):
    """任务 #127(B1):current_action 是 {name, target},给前端「近况」面板用"""
    resp = client_with_ctx.get("/api/agents")
    data = resp.json()
    by_id = {a["id"]: a for a in data}
    # wangwu 上次去公园;lisi 在吃饭
    assert by_id["wangwu"]["current_action"]["name"] == "go_to"
    assert by_id["wangwu"]["current_action"]["target"] == "公园"
    assert by_id["lisi"]["current_action"]["name"] == "eat"
    assert by_id["lisi"]["current_action"]["target"] is None


def test_api_agents_includes_status_bar_zh(client_with_ctx):
    """任务 #127(B1):status_bar 是中文 label dict(饱/累/孤独/快乐),前端直接渲染"""
    resp = client_with_ctx.get("/api/agents")
    data = resp.json()
    for a in data:
        bar = a["status_bar"]
        assert "饱" in bar
        assert "累" in bar
        assert "孤独" in bar
        assert "快乐" in bar
        for v in bar.values():
            assert isinstance(v, int)
            assert 0 <= v <= 100
