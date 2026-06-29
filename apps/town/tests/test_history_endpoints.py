"""任务 #125/#126(Bug4/5):/api/events + /api/memory-summaries 端点测试。"""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory_reflection import Event, Summary


@pytest.fixture
def client_with_ctx():
    """最小 ctx:5 agent + fake event_store / ltm。"""
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
    # 任务 #126:/api/memory-summaries 依赖 ctx["ltm"].recent_summaries
    fake_ltm = MagicMock()

    async def _fake_recent(aid, n=3):
        return [
            Summary(
                agent_id=aid,
                period_start=datetime.datetime(2026, 6, 29, 12, 0),
                period_end=datetime.datetime(2026, 6, 29, 18, 0),
                text=f"{aid} 反思摘要",
            ),
        ]

    fake_ltm.recent_summaries = AsyncMock(side_effect=_fake_recent)

    fake_world = MagicMock()
    fake_world.location_of = lambda aid: "李四家" if aid == "lisi" else "王五家"

    ctx = {
        "personas": personas,
        "agents": {"lisi": MagicMock(), "wangwu": MagicMock()},
        "event_store": fake_store,
        "ltm": fake_ltm,
        "stm": MagicMock(),
        "world": fake_world,
    }
    with patch("town.main.ctx", ctx):
        yield TestClient(app)


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


def test_api_memory_summaries_returns_merged_desc(client_with_ctx):
    """任务 #126:/api/memory-summaries 应跨所有 agent merge,按 period_end desc,取 limit 条"""
    resp = client_with_ctx.get("/api/memory-summaries?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    # fake_ltm 给每个 agent 返一条,总共 2 条,limit=5 → 2 条
    assert len(data) == 2
    # 每条必有 ts / agent_id / text
    for m in data:
        assert "ts" in m
        assert "agent_id" in m
        assert "text" in m


def test_api_memory_summaries_respects_limit(client_with_ctx):
    """任务 #126:limit 应被遵守,只取前 N 条跨 agent 合并后的 top N"""
    # 现有 fixture 每 agent 1 条,共 2 条;limit=1 → 应只剩 1 条
    resp = client_with_ctx.get("/api/memory-summaries?limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    # limit=10 → 全部 2 条都返回(不会被反向裁掉)
    resp = client_with_ctx.get("/api/memory-summaries?limit=10")
    data = resp.json()
    assert len(data) == 2


def test_api_memory_summaries_rejects_invalid_limit(client_with_ctx):
    """任务 #126:limit 必须在 1-50"""
    resp = client_with_ctx.get("/api/memory-summaries?limit=0")
    assert resp.status_code == 400
    resp = client_with_ctx.get("/api/memory-summaries?limit=999")
    assert resp.status_code == 400
