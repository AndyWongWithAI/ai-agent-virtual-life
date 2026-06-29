"""POST /api/command + GET /api/agents/{id}/commands API 测试(V5 指令面板)。"""
import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


@pytest.fixture
def client_with_ctx():
    """最小 ctx + FastAPI TestClient + 隔离 commands dict 避免测试间污染。"""
    from town.main import app, commands as global_commands

    agents = {
        "lisi": MagicMock(),
        "wangwu": MagicMock(),
    }
    personas = [
        {"id": "lisi", "name": "李四", "persona": "32岁程序员"},
        {"id": "wangwu", "name": "王五", "persona": "30岁产品经理"},
    ]
    ctx = {
        "personas": personas,
        "agents": agents,
        "world": MagicMock(location_of=lambda aid: "李四家" if aid == "lisi" else "王五家"),
    }
    # 清空全局 commands(防止前面测试残留)
    global_commands.clear()
    with patch("town.main.ctx", ctx), patch("town.main.commands", global_commands):
        try:
            yield TestClient(app)
        finally:
            global_commands.clear()


def test_post_command_queues_to_agent(client_with_ctx):
    """POST /api/command 把指令排队到对应 agent"""
    resp = client_with_ctx.post(
        "/api/command",
        json={"agent_id": "lisi", "command": "去买菜"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["agent_id"] == "lisi"
    assert data["command"] == "去买菜"
    assert data["queue_len"] == 1


def test_post_command_404_for_unknown_agent(client_with_ctx):
    """不存在的 agent 返回 404"""
    resp = client_with_ctx.post(
        "/api/command",
        json={"agent_id": "unknown", "command": "去买菜"},
    )
    assert resp.status_code == 404


def test_post_command_400_for_empty(client_with_ctx):
    """空指令返回 400"""
    resp = client_with_ctx.post(
        "/api/command",
        json={"agent_id": "lisi", "command": "  "},
    )
    assert resp.status_code == 400


def test_get_commands_returns_pending(client_with_ctx):
    """GET /api/agents/{id}/commands 返回当前 pending"""
    client_with_ctx.post("/api/command", json={"agent_id": "lisi", "command": "a"})
    client_with_ctx.post("/api/command", json={"agent_id": "lisi", "command": "b"})
    resp = client_with_ctx.get("/api/agents/lisi/commands")
    assert resp.status_code == 200
    assert resp.json()["pending"] == ["a", "b"]


def test_post_command_independent_per_agent(client_with_ctx):
    """不同 agent 的指令队列独立"""
    client_with_ctx.post("/api/command", json={"agent_id": "lisi", "command": "lisi_cmd"})
    client_with_ctx.post("/api/command", json={"agent_id": "wangwu", "command": "wangwu_cmd"})
    lisi = client_with_ctx.get("/api/agents/lisi/commands").json()["pending"]
    wangwu = client_with_ctx.get("/api/agents/wangwu/commands").json()["pending"]
    assert lisi == ["lisi_cmd"]
    assert wangwu == ["wangwu_cmd"]
