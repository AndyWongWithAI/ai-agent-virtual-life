"""阶段 3 v2 块 1:scene_store L1 资产测试

TDD 严格顺序:
1. 先写测试(本文件)
2. RED — pytest 应失败(scene_store.py 不存在)
3. GREEN — 实现 scene_store.py
4. REFACTOR

测试目标(per 设计决策清单 § 数据模型 — DB 化):
- 3 张表:scenes / scene_personas / scene_locations
- builtin 场景不可删
- 同名 scene 报 IntegrityError
- activate_scene 返 dict 含 personas + locations
- 删 scene 级联清 personas/locations

数据库:sqlite+aiosqlite:///:memory: per memory feedback-mock-test-coverage-gap.md
"""
import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from town.scene_store import (
    Base,
    Scene,
    ScenePersona,
    SceneLocation,
    init_schema,
    create_scene,
    get_scene,
    list_scenes,
    update_scene,
    delete_scene,
    add_persona,
    list_personas,
    update_persona,
    delete_persona,
    add_location,
    list_locations,
    update_location,
    delete_location,
    activate_scene,
)


@pytest_asyncio.fixture
async def engine():
    """每个测试用独立 in-memory SQLite engine,避免状态串。"""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_schema(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    """共享 session factory,测试拿的是 Session 类,不是预开 session(per scene_store
    接口风格:每个函数接收 AsyncSession 参数,调用方决定事务边界)。"""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_init_schema_creates_tables(engine):
    """RED → GREEN:init_schema 后能 query 3 张表"""
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "scenes" in tables
    assert "scene_personas" in tables
    assert "scene_locations" in tables


@pytest.mark.asyncio
async def test_create_and_get_scene(session):
    """create + get 返相同 name"""
    async with session() as s:
        sid = await create_scene(s, name="咖啡馆", description="悠闲一角")
    async with session() as s:
        sc = await get_scene(s, sid)
    assert sc is not None
    assert sc["name"] == "咖啡馆"
    assert sc["description"] == "悠闲一角"
    assert sc["is_builtin"] is False


@pytest.mark.asyncio
async def test_builtin_scene_cannot_be_deleted(session):
    """builtin 场景 delete_scene 返 False + 记录还在"""
    async with session() as s:
        sid = await create_scene(s, name="base_day", description="", is_builtin=True)
    async with session() as s:
        ok = await delete_scene(s, sid)
        assert ok is False
    async with session() as s:
        sc = await get_scene(s, sid)
    assert sc is not None
    assert sc["name"] == "base_day"


@pytest.mark.asyncio
async def test_non_builtin_scene_can_be_deleted(session):
    """非 builtin 场景可正常删除,返 True"""
    async with session() as s:
        sid = await create_scene(s, name="可删场景", description="")
    async with session() as s:
        ok = await delete_scene(s, sid)
        assert ok is True
    async with session() as s:
        sc = await get_scene(s, sid)
    assert sc is None


@pytest.mark.asyncio
async def test_unique_scene_name(session):
    """同名 scene 报 IntegrityError"""
    async with session() as s:
        await create_scene(s, name="重复", description="")
    async with session() as s:
        with pytest.raises(IntegrityError):
            await create_scene(s, name="重复", description="")
            await s.commit()


@pytest.mark.asyncio
async def test_add_persona_to_scene(session):
    """add_persona 后 list_personas 返 1 条"""
    async with session() as s:
        sid = await create_scene(s, name="测试", description="")
    async with session() as s:
        pid = await add_persona(
            s, scene_id=sid, agent_id="alice",
            name="Alice", persona="爱喝咖啡",
            start_location="客厅", color="#FF0000",
        )
    async with session() as s:
        personas = await list_personas(s, sid)
    assert len(personas) == 1
    assert personas[0]["agent_id"] == "alice"
    assert personas[0]["name"] == "Alice"
    assert personas[0]["start_location"] == "客厅"


@pytest.mark.asyncio
async def test_add_location_to_scene(session):
    """add_location + list_locations 含 adjacency"""
    async with session() as s:
        sid = await create_scene(s, name="测试", description="")
    async with session() as s:
        lid = await add_location(
            s, scene_id=sid, name="客厅", x=100, y=200,
            color="#FFD700", adjacency=["厨房", "卧室"],
        )
    async with session() as s:
        locs = await list_locations(s, sid)
    assert len(locs) == 1
    assert locs[0]["name"] == "客厅"
    assert locs[0]["x"] == 100
    assert locs[0]["y"] == 200
    assert locs[0]["adjacency"] == ["厨房", "卧室"]


@pytest.mark.asyncio
async def test_activate_scene_returns_personas_and_locations(session):
    """activate_scene 返 dict 含 personas + locations 两个 key"""
    async with session() as s:
        sid = await create_scene(s, name="激活测试", description="")
    async with session() as s:
        await add_persona(
            s, scene_id=sid, agent_id="bob",
            name="Bob", persona="活泼",
            start_location="公园", color="#00FF00",
        )
    async with session() as s:
        await add_location(
            s, scene_id=sid, name="公园", x=50, y=50,
            color="#FFD700", adjacency=[],
        )
    async with session() as s:
        activated = await activate_scene(s, sid)
    assert "personas" in activated
    assert "locations" in activated
    assert len(activated["personas"]) == 1
    assert len(activated["locations"]) == 1


@pytest.mark.asyncio
async def test_activate_empty_scene_returns_empty_lists(session):
    """空 scene(无 persona/location)activate 返 []"""
    async with session() as s:
        sid = await create_scene(s, name="空", description="")
    async with session() as s:
        activated = await activate_scene(s, sid)
    assert activated == {"personas": [], "locations": []}


@pytest.mark.asyncio
async def test_cascade_delete_personas_and_locations(session):
    """删 scene 时自动级联删 personas/locations"""
    async with session() as s:
        sid = await create_scene(s, name="级联测试", description="")
    async with session() as s:
        await add_persona(
            s, scene_id=sid, agent_id="x",
            name="X", persona="p",
            start_location="家", color="#888888",
        )
    async with session() as s:
        await add_location(
            s, scene_id=sid, name="家", x=0, y=0,
            color="#FFD700", adjacency=[],
        )
    async with session() as s:
        await delete_scene(s, sid)
    async with session() as s:
        assert await list_personas(s, sid) == []
        assert await list_locations(s, sid) == []


@pytest.mark.asyncio
async def test_update_scene(session):
    """update_scene 修改 name + description"""
    async with session() as s:
        sid = await create_scene(s, name="旧名", description="旧描述")
    async with session() as s:
        ok = await update_scene(s, sid, name="新名", description="新描述")
        assert ok is True
    async with session() as s:
        sc = await get_scene(s, sid)
    assert sc["name"] == "新名"
    assert sc["description"] == "新描述"


@pytest.mark.asyncio
async def test_list_scenes_orders_by_id(session):
    """list_scenes 返所有 scene(按 id 升序)"""
    async with session() as s:
        await create_scene(s, name="第一个", description="")
    async with session() as s:
        await create_scene(s, name="第二个", description="")
    async with session() as s:
        scenes = await list_scenes(s)
    assert len(scenes) == 2
    names = [s_["name"] for s_ in scenes]
    assert names == ["第一个", "第二个"]


@pytest.mark.asyncio
async def test_update_persona_and_location(session):
    """update_persona / update_location 部分字段更新"""
    async with session() as s:
        sid = await create_scene(s, name="upd", description="")
    async with session() as s:
        pid = await add_persona(
            s, scene_id=sid, agent_id="c",
            name="C", persona="p",
            start_location="起点", color="#888888",
        )
    async with session() as s:
        ok = await update_persona(s, pid, name="C2", color="#ABCDEF")
        assert ok is True
    async with session() as s:
        ps = await list_personas(s, sid)
    assert ps[0]["name"] == "C2"
    assert ps[0]["color"] == "#ABCDEF"
    # persona 字段未改
    assert ps[0]["persona"] == "p"

    async with session() as s:
        lid = await add_location(
            s, scene_id=sid, name="loc", x=0, y=0,
            color="#FFD700", adjacency=["a"],
        )
    async with session() as s:
        ok = await update_location(s, lid, x=99, adjacency=["a", "b"])
        assert ok is True
    async with session() as s:
        ls = await list_locations(s, sid)
    assert ls[0]["x"] == 99
    assert ls[0]["adjacency"] == ["a", "b"]


@pytest.mark.asyncio
async def test_delete_persona_and_location(session):
    """delete_persona / delete_location 返 True 并移除记录"""
    async with session() as s:
        sid = await create_scene(s, name="del", description="")
    async with session() as s:
        pid = await add_persona(
            s, scene_id=sid, agent_id="d",
            name="D", persona="p",
            start_location="起点", color="#888888",
        )
    async with session() as s:
        ok = await delete_persona(s, pid)
        assert ok is True
    async with session() as s:
        assert await list_personas(s, sid) == []

    async with session() as s:
        lid = await add_location(
            s, scene_id=sid, name="del_loc", x=0, y=0,
            color="#FFD700", adjacency=[],
        )
    async with session() as s:
        ok = await delete_location(s, lid)
        assert ok is True
    async with session() as s:
        assert await list_locations(s, sid) == []