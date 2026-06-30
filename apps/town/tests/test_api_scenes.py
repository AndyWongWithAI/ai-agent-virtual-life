"""阶段 3 v2 块 2(任务 T18):8 个场景 API 端点测试。

TDD 顺序:
1. 先写测试(本文件)
2. RED — pytest 应失败(端点未实现)
3. GREEN — main.py 加端点
4. REFACTOR

测试策略:
- 复用 T17 (test_scene_store.py) 的 in-memory SQLite fixture + scene_store 函数
- 直接 patch town.main.app 里的 _get_session 拿到 AsyncSession,
  绕过 bootstrap 连 Postgres/Redis(per memory feedback-mock-test-coverage-gap.md)
- TestClient 调 FastAPI 路由(同 test_api_restart.py 模式)

覆盖(per 任务描述):
1. test_list_scenes_empty
2. test_create_scene_returns_id
3. test_create_scene_duplicate_name_400
4. test_get_scene_with_personas_and_locations
5. test_update_scene_name
6. test_delete_scene_returns_204
7. test_delete_builtin_scene_409
8. test_activate_scene_returns_activate_response
9. test_import_yaml_creates_scene
10. test_export_yaml_returns_yaml_string
11. test_get_nonexistent_scene_404
12. test_create_scene_name_too_long_422
"""
from __future__ import annotations

import io

import pytest
import pytest_asyncio
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from town.scene_store import (
    Base,
    add_location,
    add_persona,
    create_scene,
    init_schema,
)


# --- 公共 fixture:用 monkeypatch 注入 in-memory SQLite,绕过 Postgres ---
#
# 设计:不重写 conftest.py(避免影响其它测试)。本文件独立 monkeypatch
# `town.main._get_scene_session` 让它返 in-memory session factory。
# 这样 FastAPI 端点走真 ORM 但 DB 是临时 SQLite,符合
# feedback-mock-test-coverage-gap.md 的「真实调用层」原则。


@pytest_asyncio.fixture
async def in_memory_session_factory(monkeypatch):
    """建 in-memory SQLite + scene_store schema + patch main._get_scene_session。

    返 (session_factory, init_builtin_fn) — 测试用 session_factory 直接
    灌数据(模拟已存在 builtin scene)。
    """
    from town import main as town_main

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def fake_get_scene_session():
        """端点内部用,返新 session(per-request)。"""
        return session_factory()

    monkeypatch.setattr(town_main, "_get_scene_session", fake_get_scene_session)

    # 灌一个 builtin scene (用于 delete_builtin_scene_409 测试)
    async def _seed_builtin(name: str = "base_day") -> int:
        async with session_factory() as s:
            sid = await create_scene(s, name=name, description="默认场景", is_builtin=True)
        return sid

    yield session_factory, _seed_builtin

    await engine.dispose()


@pytest.fixture
def client(monkeypatch):
    """FastAPI TestClient:patch bootstrap 防 lifespan 真连外部服务。

    scene_store 用本文件自己的 in_memory_session_factory(不走 bootstrap)。
    """
    from town import main as town_main

    # reset module state
    town_main._paused = False
    town_main.ws_clients.clear()

    # patch bootstrap 以防 lifespan 调用(虽然 in_memory_session 优先,
    # 但 lifespan 里 ctx 可能为 None,这里防御性 stub)
    async def fake_bootstrap():
        return {
            "personas": [],
            "agents": {},
            "world": type("W", (), {"place": lambda self, *a, **k: None})(),
            "bus": type("B", (), {"subscribe": lambda self, *a, **k: None,
                                   "publish": lambda self, *a, **k: asyncio.sleep(0),
                                   "run_forever": lambda self: asyncio.sleep(0),
                                   "redis": None})(),
            "event_store": type("E", (), {"append": lambda self, **k: asyncio.sleep(0, result=1),
                                          "create_dialogue": lambda self, l: asyncio.sleep(0, result=1),
                                          "add_dialogue_message": lambda self, *a, **k: asyncio.sleep(0),
                                          "list_events": lambda self, **k: asyncio.sleep(0, result=[]),
                                          "aclose": lambda self: asyncio.sleep(0)})(),
            "trigger": type("T", (), {"should_start": lambda self, **k: False})(),
            "dialogue_gen": type("D", (), {"generate": lambda self, **k: asyncio.sleep(0, result=[])})(),
            "llm": None,
            "stm": None,
            "ltm": None,
            "reflector": None,
            "locations": [],
            "config_source": "base",
            "config_errors": [],
        }

    import asyncio
    monkeypatch.setattr(town_main, "bootstrap", fake_bootstrap)
    # patch bootstrap_reload 防 activate 真重启(新版签名:prev_ctx + personas + locations)
    async def fake_reload(prev_ctx=None, **_):
        return prev_ctx or {}
    monkeypatch.setattr(town_main, "bootstrap_reload", fake_reload)

    yield TestClient(town_main.app)


# --- 12 个测试 ---


def test_list_scenes_empty(client, in_memory_session_factory):
    """空 DB → GET /api/scenes 返 []"""
    resp = client.get("/api/scenes")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_scene_returns_id(client, in_memory_session_factory):
    """POST /api/scenes {name} → 201 + id"""
    resp = client.post("/api/scenes", json={"name": "咖啡馆", "description": "悠闲一角"})
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert isinstance(data["id"], int)
    assert data["id"] > 0


def test_create_scene_duplicate_name_400(client, in_memory_session_factory):
    """同名 scene → 400(IntegrityError 转 HTTP 400)"""
    client.post("/api/scenes", json={"name": "重名"})
    resp = client.post("/api/scenes", json={"name": "重名"})
    assert resp.status_code == 400
    assert "重名" in resp.json()["detail"] or "duplicate" in resp.json()["detail"].lower()


def test_get_scene_with_personas_and_locations(client, in_memory_session_factory):
    """GET /api/scenes/{id} 含 personas + locations 列表"""
    factory, _ = in_memory_session_factory
    import asyncio

    async def _seed():
        async with factory() as s:
            sid = await create_scene(s, name="详情测试", description="desc")
            await add_persona(s, scene_id=sid, agent_id="a", name="A", persona="p",
                              start_location="loc1", color="#FF0000")
            await add_location(s, scene_id=sid, name="loc1", x=10, y=20,
                               color="#FFD700", adjacency=["loc2"])
            await add_location(s, scene_id=sid, name="loc2", x=30, y=40,
                               color="#00FF00", adjacency=["loc1"])
        return sid

    sid = asyncio.run(_seed())

    resp = client.get(f"/api/scenes/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sid
    assert data["name"] == "详情测试"
    assert len(data["personas"]) == 1
    assert data["personas"][0]["agent_id"] == "a"
    assert len(data["locations"]) == 2
    loc_names = {loc["name"] for loc in data["locations"]}
    assert loc_names == {"loc1", "loc2"}


def test_update_scene_name(client, in_memory_session_factory):
    """PATCH /api/scenes/{id} {name} → 200"""
    factory, _ = in_memory_session_factory
    import asyncio

    async def _seed():
        async with factory() as s:
            return await create_scene(s, name="旧名", description="")

    sid = asyncio.run(_seed())
    resp = client.patch(f"/api/scenes/{sid}", json={"name": "新名", "description": "新描述"})
    assert resp.status_code == 200
    # 验证 DB 真的改了
    resp2 = client.get(f"/api/scenes/{sid}")
    assert resp2.json()["name"] == "新名"
    assert resp2.json()["description"] == "新描述"


def test_delete_scene_returns_204(client, in_memory_session_factory):
    """DELETE /api/scenes/{id} → 204"""
    factory, _ = in_memory_session_factory
    import asyncio

    async def _seed():
        async with factory() as s:
            return await create_scene(s, name="待删", description="")

    sid = asyncio.run(_seed())
    resp = client.delete(f"/api/scenes/{sid}")
    assert resp.status_code == 204
    # 再 GET 应该是 404
    resp2 = client.get(f"/api/scenes/{sid}")
    assert resp2.status_code == 404


def test_delete_builtin_scene_409(client, in_memory_session_factory):
    """内置场景 DELETE → 409 + 不能删"""
    _, seed_builtin = in_memory_session_factory
    import asyncio
    sid = asyncio.run(seed_builtin())

    resp = client.delete(f"/api/scenes/{sid}")
    assert resp.status_code == 409
    assert "内置" in resp.json()["detail"]


def test_activate_scene_returns_activate_response(client, in_memory_session_factory, monkeypatch):
    """POST /api/scenes/{id}/activate → 200 + 结构正确

    注:bootstrap_reload 已被 client fixture monkey-patch 为 fake(返 prev_ctx),
    不会真重启。端点 contract:status="activated" + scene_id/name/counts。
    """
    factory, _ = in_memory_session_factory
    import asyncio

    async def _seed():
        async with factory() as s:
            sid = await create_scene(s, name="激活测试", description="激活 desc")
            await add_persona(s, scene_id=sid, agent_id="x", name="X", persona="p",
                              start_location="h", color="#888888")
            await add_location(s, scene_id=sid, name="h", x=0, y=0,
                               color="#FFD700", adjacency=[])
        return sid

    sid = asyncio.run(_seed())
    resp = client.post(f"/api/scenes/{sid}/activate")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code} {resp.text}"
    data = resp.json()
    # contract:{status: "activated", scene_id, scene_name, personas_count, locations_count}
    assert data["status"] == "activated"
    assert data["scene_id"] == sid
    assert data["scene_name"] == "激活测试"
    assert data["personas_count"] == 1
    assert data["locations_count"] == 1


def test_import_yaml_creates_scene(client, in_memory_session_factory):
    """POST /api/scenes/import-yaml (multipart) → 201 + 新 id

    YAML 格式与 config/base/personas.yaml + locations.yaml 一致。
    """
    yaml_text = yaml.safe_dump({
        "agents": [
            {"id": "p1", "name": "P1", "persona": "性格一",
             "start_location": "地点甲", "color": "#FF0000"},
        ],
        "locations": [
            {"name": "地点甲", "x": 100, "y": 200,
             "color": "#FFD700", "adjacency": []},
        ],
    }, allow_unicode=True)
    files = {"file": ("test_scene.yaml", io.BytesIO(yaml_text.encode("utf-8")), "application/x-yaml")}
    resp = client.post("/api/scenes/import-yaml", files=files)
    assert resp.status_code == 201, f"expected 201, got {resp.status_code} {resp.text}"
    data = resp.json()
    assert "id" in data
    assert data["name"] == "test_scene"  # 文件名去扩展

    # 验证 personas/locations 真的进了 DB
    sid = data["id"]
    detail = client.get(f"/api/scenes/{sid}").json()
    assert len(detail["personas"]) == 1
    assert detail["personas"][0]["agent_id"] == "p1"
    assert len(detail["locations"]) == 1
    assert detail["locations"][0]["name"] == "地点甲"


def test_export_yaml_returns_yaml_string(client, in_memory_session_factory):
    """GET /api/scenes/{id}/export-yaml → 200 + content-type yaml + 含 'agents:'"""
    factory, _ = in_memory_session_factory
    import asyncio

    async def _seed():
        async with factory() as s:
            sid = await create_scene(s, name="导出测试", description="")
            await add_persona(s, scene_id=sid, agent_id="e", name="E", persona="p",
                              start_location="loc", color="#888888")
            await add_location(s, scene_id=sid, name="loc", x=10, y=20,
                               color="#FFD700", adjacency=[])
        return sid

    sid = asyncio.run(_seed())
    resp = client.get(f"/api/scenes/{sid}/export-yaml")
    assert resp.status_code == 200
    assert "yaml" in resp.headers["content-type"].lower()
    body = resp.text
    assert "agents:" in body
    assert "locations:" in body
    # 反向 parse 验证可读
    parsed = yaml.safe_load(body)
    assert len(parsed["agents"]) == 1
    assert parsed["agents"][0]["agent_id"] == "e"
    assert len(parsed["locations"]) == 1
    assert parsed["locations"][0]["name"] == "loc"
    # Content-Disposition 含 attachment + filename
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".yaml" in cd


def test_get_nonexistent_scene_404(client, in_memory_session_factory):
    """GET /api/scenes/999 → 404"""
    resp = client.get("/api/scenes/999")
    assert resp.status_code == 404


def test_create_scene_name_too_long_422(client, in_memory_session_factory):
    """name 65 字符 → 422(Pydantic 自动校验)"""
    resp = client.post("/api/scenes", json={"name": "x" * 65})
    assert resp.status_code == 422