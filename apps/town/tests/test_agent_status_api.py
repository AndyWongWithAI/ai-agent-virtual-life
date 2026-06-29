"""GET /api/agents/{id}/status API 测试。"""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory_reflection import Event, Summary


@pytest.fixture
def client_with_ctx():
    """最小 ctx + FastAPI TestClient."""
    from town.main import app

    personas = [
        {"id": "lisi", "name": "李四", "persona": "32岁程序员"},
        {"id": "wangwu", "name": "王五", "persona": "30岁产品经理"},
    ]
    fake_stm = MagicMock()
    fake_stm.recent = AsyncMock(return_value=[
        Event(agent_id="lisi", kind="dialogue", content="和王五吃午饭", ts=datetime.datetime(2026, 6, 29, 12, 0)),
        Event(agent_id="lisi", kind="decision", content="wake_up -> bed", ts=datetime.datetime(2026, 6, 29, 7, 0)),
    ])
    fake_ltm = MagicMock()
    fake_ltm.recent_summaries = AsyncMock(return_value=[
        Summary(
            agent_id="lisi",
            period_start=datetime.datetime(2026, 6, 28, 22, 0),
            period_end=datetime.datetime(2026, 6, 29, 4, 0),
            text="李四昨天工作到很晚",
        ),
    ])
    fake_world = MagicMock()
    fake_world.location_of = lambda aid: "李四家" if aid == "lisi" else "王五家"
    fake_world.snapshot = lambda aid: {
        "status_bar": {"饱": 70, "累": 40, "孤独": 30, "快乐": 60},
        "location": "李四家" if aid == "lisi" else "王五家",
    }
    ctx = {
        "personas": personas,
        "stm": fake_stm,
        "ltm": fake_ltm,
        "world": fake_world,
    }
    with patch("town.main.ctx", ctx):
        yield TestClient(app)


def test_agent_status_returns_structured_status_bar(client_with_ctx):
    """/api/agents/lisi/status 必须返回 status_bar dict,不是字符串"""
    resp = client_with_ctx.get("/api/agents/lisi/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "lisi"
    assert data["name"] == "李四"
    assert isinstance(data["status_bar"], dict)
    assert "饱" in data["status_bar"]
    assert "累" in data["status_bar"]
    assert "孤独" in data["status_bar"]
    assert "快乐" in data["status_bar"]


def test_agent_status_includes_recent_summaries(client_with_ctx):
    """/api/agents/lisi/status 必须返回 LTM 反思摘要"""
    resp = client_with_ctx.get("/api/agents/lisi/status")
    data = resp.json()
    assert len(data["recent_summaries"]) == 1
    assert "工作" in data["recent_summaries"][0]["text"]


def test_agent_status_includes_recent_events(client_with_ctx):
    """/api/agents/lisi/status 必须返回 STM 近期事件"""
    resp = client_with_ctx.get("/api/agents/lisi/status")
    data = resp.json()
    assert len(data["recent_events"]) == 2
    assert data["recent_events"][0]["kind"] == "dialogue"


def test_agent_status_404_for_unknown_agent(client_with_ctx):
    """不存在的 agent_id 应返回 404 或 error"""
    resp = client_with_ctx.get("/api/agents/unknown/status")
    # 接受 404 或 200 with error
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert "error" in resp.json()
