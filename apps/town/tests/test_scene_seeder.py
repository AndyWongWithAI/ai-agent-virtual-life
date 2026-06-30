"""阶段 3 v2 块 5:scene_seeder 测试

TDD 顺序:
1. 本文件
2. RED → GREEN(实现 scene_seeder.py)
3. 测试 3 条覆盖:空表 seed / idempotent / YAML 读取

数据库:sqlite+aiosqlite:///:memory: per memory feedback-mock-test-coverage-gap.md

YAML 路径处理:
- 真实 YAML 路径(_BASE_CONFIG_DIR)由 seeder 内部使用绝对路径,跟随源码
- 测试第三条用 monkeypatch 替换 _read_default_yaml 或写临时 YAML 让 seeder 读
"""
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from town import scene_seeder, scene_store


# 真实 base YAML 内容(用于 test_seed_reads_v1_yaml 校验)
_REAL_PERSONAS = {
    "agents": [
        {"id": "lisi", "name": "李四", "persona": "32 岁程序员",
         "start_location": "李四家", "color": "#FF6B6B"},
        {"id": "wangwu", "name": "王五", "persona": "30 岁产品经理",
         "start_location": "王五家", "color": "#4ECDC4"},
        {"id": "zhangwei", "name": "张伟", "persona": "28 岁设计师",
         "start_location": "公园", "color": "#45B7D1"},
        {"id": "liuna", "name": "刘娜", "persona": "35 岁教师",
         "start_location": "客厅", "color": "#FFA07A"},
        {"id": "chenlei", "name": "陈雷", "persona": "29 岁销售",
         "start_location": "王五家", "color": "#C39BD3"},
    ]
}
_REAL_LOCATIONS = {
    "locations": [
        {"name": "李四家", "x": 100, "y": 100, "color": "#FFD700",
         "adjacency": ["客厅"]},
        {"name": "王五家", "x": 300, "y": 100, "color": "#87CEEB",
         "adjacency": ["客厅", "公园"]},
        {"name": "客厅", "x": 200, "y": 250, "color": "#98FB98",
         "adjacency": ["李四家", "王五家", "厨房"]},
        {"name": "厨房", "x": 350, "y": 300, "color": "#FFA07A",
         "adjacency": ["客厅"]},
        {"name": "公园", "x": 600, "y": 350, "color": "#90EE90",
         "adjacency": ["王五家"]},
    ]
}


@pytest_asyncio.fixture
async def engine():
    """每个测试用独立 in-memory SQLite engine,scene_seeder 依赖此 engine。"""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await scene_store.init_schema(eng)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_seed_creates_default_scene_when_empty(engine):
    """空 DB 跑 seed → 1 scene (builtin=True) + 5 personas + 5 locations。"""
    scene_id = await scene_seeder.seed_default_scene_if_empty(engine)

    from sqlalchemy.ext.asyncio import async_sessionmaker
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as s:
        scenes = await scene_store.list_scenes(s)
        assert len(scenes) == 1
        assert scenes[0]["id"] == scene_id
        assert scenes[0]["name"] == "小镇默认"
        assert scenes[0]["is_builtin"] is True

        personas = await scene_store.list_personas(s, scene_id)
        locations = await scene_store.list_locations(s, scene_id)

    assert len(personas) == 5
    assert len(locations) == 5
    # 抽样校验真实 base yaml 数据落到 DB
    agent_ids = {p["agent_id"] for p in personas}
    assert agent_ids == {"lisi", "wangwu", "zhangwei", "liuna", "chenlei"}
    loc_names = {loc["name"] for loc in locations}
    assert loc_names == {"李四家", "王五家", "客厅", "厨房", "公园"}


@pytest.mark.asyncio
async def test_seed_idempotent(engine):
    """跑 seed 2 次,scenes 表仍 1 条,返回同一 id。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    Session = async_sessionmaker(engine, expire_on_commit=False)

    first_id = await scene_seeder.seed_default_scene_if_empty(engine)
    second_id = await scene_seeder.seed_default_scene_if_empty(engine)

    assert first_id == second_id
    async with Session() as s:
        scenes = await scene_store.list_scenes(s)
        assert len(scenes) == 1
        personas = await scene_store.list_personas(s, first_id)
        locations = await scene_store.list_locations(s, first_id)
    assert len(personas) == 5
    assert len(locations) == 5


@pytest.mark.asyncio
async def test_seed_reads_v1_yaml(monkeypatch, tmp_path, engine):
    """验证 seeder 从 YAML 读 — monkeypatch _read_default_yaml 走 tmp_path。"""
    # 写测试用 YAML 到 tmp_path
    personas_file = tmp_path / "personas.yaml"
    locations_file = tmp_path / "locations.yaml"
    personas_file.write_text(
        yaml.safe_dump(_REAL_PERSONAS, allow_unicode=True),
        encoding="utf-8",
    )
    locations_file.write_text(
        yaml.safe_dump(_REAL_LOCATIONS, allow_unicode=True),
        encoding="utf-8",
    )

    # monkeypatch seeder 内部的 _read_default_yaml 让它读 tmp_path
    def fake_read():
        from town.config_loader import load_config
        return load_config(tmp_path)

    monkeypatch.setattr(scene_seeder, "_read_default_yaml", fake_read)

    scene_id = await scene_seeder.seed_default_scene_if_empty(engine)

    from sqlalchemy.ext.asyncio import async_sessionmaker
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        personas = await scene_store.list_personas(s, scene_id)
        locations = await scene_store.list_locations(s, scene_id)

    # 数据一致:YAML 5+5 → DB 5+5
    assert len(personas) == 5
    assert len(locations) == 5
    lisi = next(p for p in personas if p["agent_id"] == "lisi")
    assert lisi["name"] == "李四"
    assert lisi["start_location"] == "李四家"
    assert lisi["color"] == "#FF6B6B"

    wangwu_home = next(loc for loc in locations if loc["name"] == "王五家")
    assert wangwu_home["x"] == 300
    assert wangwu_home["y"] == 100
    assert set(wangwu_home["adjacency"]) == {"客厅", "公园"}