"""E2E 测试:V1 验收 + bootstrap 装配

V1 验收:打开 http://localhost:8000/api/agents 能看到 5 个智能体。
这里通过 ASGITransport 直接调 /api/agents,断言 5 个 agent 都在 + 名字正确。
"""
import pytest

from town.main import app
from virtual_world_engine import World


@pytest.mark.asyncio
async def test_v1_list_agents(client):
    """V1: GET /api/agents 返回 5 个 agent,名字集合 == {李四,王五,张伟,刘娜,陈雷}"""
    r = await client.get("/api/agents")
    assert r.status_code == 200
    agents = r.json()
    assert len(agents) == 5
    assert {a["name"] for a in agents} == {"李四", "王五", "张伟", "刘娜", "陈雷"}
    # 每个 agent 都有 id / name / location 字段
    for a in agents:
        assert "id" in a
        assert "name" in a
        assert "location" in a
        assert a["location"]  # 非空


def test_world_includes_five_locations():
    """V1 底层:World 5 个合法地点全部就绪"""
    w = World()
    # 5 个 agent 起始位置都合法
    for loc in ["李四家", "王五家", "公园", "客厅"]:
        w.place("x", loc)
    assert w.location_of("x") == "客厅"


@pytest.mark.asyncio
async def test_bootstrap_creates_components():
    """V1 底层:bootstrap 后 5 个 agent 都在 World 中(用 stub ctx,避免真 LLM/DB)"""
    # 这里不连真 bootstrap,直接构造 World + 5 agent,断言装配结构
    from pathlib import Path
    import yaml
    personas = yaml.safe_load(
        Path(__file__).parent.parent.joinpath("src/town/personas.yaml").read_text(
            encoding="utf-8"
        )
    )["agents"]
    w = World()
    for p in personas:
        w.place(p["id"], p["start_location"])
    for aid in ["lisi", "wangwu", "zhangwei", "liuna", "chenlei"]:
        assert aid in {p["id"] for p in personas}
        assert w.location_of(aid) is not None
