"""阶段 2 块 1(任务 T10):导演场景注入端点测试。

覆盖:
- test_inject_scene_returns_state:POST /api/director/scene → 200 + state.last_scene 非空
- test_empty_content_rejected:content="" → 400
- test_state_endpoint:GET /api/director/state → paused/speed/last_scene
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_ctx():
    """最小 ctx + reset _paused + reset director state。"""
    from town import main as town_main
    from town.director import _director_state

    # reset 状态
    town_main._paused = False
    _director_state["paused"] = False
    _director_state["speed"] = 1.0
    _director_state["last_scene"] = None

    personas = [{"id": "lisi", "name": "李四", "persona": "32岁程序员"}]
    # bus.publish 必须是 AsyncMock,端点会 await 它
    ctx = {
        "personas": personas,
        "agents": {"lisi": MagicMock()},
        "event_store": MagicMock(),
        "ltm": MagicMock(),
        "stm": MagicMock(),
        "world": MagicMock(),
        "bus": MagicMock(publish=AsyncMock()),
    }
    with patch("town.main.ctx", ctx):
        yield TestClient(town_main.app)

    town_main._paused = False
    _director_state["last_scene"] = None


def test_inject_scene_returns_state(client_with_ctx):
    """POST /api/director/scene → 200 + state.last_scene 非空 + bus.publish 被调"""
    from town import main as town_main

    resp = client_with_ctx.post(
        "/api/director/scene",
        json={"kind": "birthday", "content": "今天李四过生日"},
    )
    assert resp.status_code == 200, f"expected 200, got {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["ok"] is True
    assert data["state"]["last_scene"] is not None
    assert data["state"]["last_scene"]["kind"] == "birthday"
    assert data["state"]["last_scene"]["content"] == "今天李四过生日"
    # bus.publish 调过 DIRECTOR_SCENE
    publish_calls = town_main.ctx["bus"].publish.call_args_list
    assert len(publish_calls) >= 1, "bus.publish 应至少被调一次"


def test_empty_content_rejected(client_with_ctx):
    """POST /api/director/scene {content: ""} → 400 + last_scene 不变"""
    from town.director import _director_state

    _director_state["last_scene"] = None
    resp = client_with_ctx.post(
        "/api/director/scene",
        json={"kind": "x", "content": ""},
    )
    assert resp.status_code == 400
    assert _director_state["last_scene"] is None


def test_state_endpoint(client_with_ctx):
    """GET /api/director/state → 返 paused/speed/last_scene"""
    from town.director import set_scene, set_paused, set_speed

    set_paused(True)
    set_speed(2.0)
    set_scene("festival", "春节")

    resp = client_with_ctx.get("/api/director/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paused"] is True
    assert data["speed"] == 2.0
    assert data["last_scene"] is not None
    assert data["last_scene"]["kind"] == "festival"
    assert data["last_scene"]["content"] == "春节"
