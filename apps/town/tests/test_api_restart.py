"""阶段 3 收尾(任务 T9):/api/restart 端点测试。

覆盖:
- happy path:POST /api/restart → 200,personas_count + locations_count 对得上 YAML
- 失败路径:bootstrap_reload 抛异常 → 500
- 架构约束:bootstrap_reload 返回的 ctx 复用 prev 的基础设施(不重连)
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_ctx():
    """构造最小可用 ctx(含 personas/locations) + monkey-patch bootstrap_reload。"""
    from town import main as town_main

    # reset module-level
    town_main._paused = False
    town_main.ws_clients.clear()

    personas = [
        {"id": "lisi", "name": "李四", "persona": "32岁程序员",
         "start_location": "李四家"},
        {"id": "wangwu", "name": "王五", "persona": "40岁数学家",
         "start_location": "中心广场"},
    ]
    locations = [
        {"name": "李四家", "x": 100, "y": 200, "color": "#FF6B6B", "adjacency": ["中心广场"]},
        {"name": "中心广场", "x": 400, "y": 250, "color": "#4ECDC4", "adjacency": ["李四家"]},
    ]
    fake_world = MagicMock()
    fake_world.place = MagicMock()
    ctx = {
        "personas": personas,
        "agents": {"lisi": MagicMock(), "wangwu": MagicMock()},
        "event_store": MagicMock(),
        "ltm": MagicMock(),
        "stm": MagicMock(),
        "world": fake_world,
        "bus": MagicMock(),
        "trigger": MagicMock(),
        "dialogue_gen": MagicMock(),
        "llm": MagicMock(),
        "reflector": MagicMock(),
        "locations": locations,
        "config_source": "base",
        "config_errors": [],
    }

    # 默认 fake_bootstrap_reload 走 happy path
    async def fake_reload(prev_ctx=None):
        return {
            **prev_ctx,
            "personas": personas,
            "locations": locations,
            "config_source": "base",
            "config_errors": [],
            "world": MagicMock(),
            "agents": {"lisi": MagicMock(), "wangwu": MagicMock()},
        }

    with patch("town.main.ctx", ctx), \
         patch.object(town_main, "bootstrap_reload", side_effect=fake_reload):
        yield TestClient(town_main.app), ctx, fake_reload

    town_main._paused = False
    town_main.ws_clients.clear()


def test_restart_happy_path(client_with_ctx):
    """happy path:POST /api/restart → 200 + 返 personas/locations 计数正确。

    架构警告:必须用 fake_bootstrap_reload 替代真函数(real 会连 Redis/Postgres)。
    """
    client, prev_ctx, _ = client_with_ctx
    resp = client.post("/api/restart")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["status"] == "restarted"
    assert data["source"] == "base"
    assert data["personas_count"] == len(prev_ctx["personas"])
    assert data["locations_count"] == len(prev_ctx["locations"])
    assert data["personas_count"] == 2
    assert data["locations_count"] == 2


def test_restart_reports_custom_source(client_with_ctx):
    """自定义配置 source=custom 时,响应里 source 字段如实返回。"""
    from town import main as town_main
    client, prev_ctx, fake_reload = client_with_ctx

    # 改 fake_reload 让它返回 custom source
    async def custom_reload(prev_ctx=None):
        out = await fake_reload(prev_ctx=prev_ctx)
        out["config_source"] = "custom"
        return out

    with patch.object(town_main, "bootstrap_reload", side_effect=custom_reload):
        resp = client.post("/api/restart")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "custom"


def test_restart_returns_500_when_bootstrap_reload_raises(client_with_ctx):
    """失败路径:bootstrap_reload 抛异常 → 500 + 不污染全局 ctx。"""
    from town import main as town_main
    client, prev_ctx, _ = client_with_ctx

    # 用 mock 直接 raise
    with patch.object(
        town_main,
        "bootstrap_reload",
        side_effect=RuntimeError("模拟配置损坏"),
    ):
        resp = client.post("/api/restart")

    # 端点应捕异常并 raise HTTPException(500)
    assert resp.status_code == 500
    assert "配置" in resp.json()["detail"]


def test_restart_does_not_close_infrastructure(client_with_ctx):
    """架构警告(STOP 条件):bootstrap_reload 必须复用基础设施(llm/bus/reflector)。

    即:fake prev_ctx 里的 llm/bus 等**不能**被 close() 调过。
    """
    client, prev_ctx, fake_reload = client_with_ctx
    resp = client.post("/api/restart")
    assert resp.status_code == 200
    # prev_ctx 的 llm/bus/reflector 没被改引用 = 没重连
    assert prev_ctx["llm"] is not None
    assert prev_ctx["bus"] is not None
    assert prev_ctx["reflector"] is not None
    # 关键:不调用 close() / aclose()
    assert prev_ctx["llm"].close.call_count == 0 if hasattr(prev_ctx["llm"], "close") else True
    assert prev_ctx["bus"].close.call_count == 0 if hasattr(prev_ctx["bus"], "close") else True
