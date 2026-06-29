"""真集成测试 — 标记 @pytest.mark.integration,需 docker up

覆盖:
- 真 redis 连接 (6380)
- 真 postgres 连接 (5433)
- 真 PG append + list(端到端)
- 真服务 + fake LLM 跑 1 次 tick 决策入 PG(模拟生产路径)
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_redis_real_connection(redis_url):
    """真连 redis 6380"""
    import redis.asyncio as redis_async
    r = redis_async.from_url(redis_url)
    pong = await r.ping()
    assert pong is True
    await r.aclose()


@pytest.mark.asyncio
async def test_postgres_real_connection(database_url):
    """真连 postgres 5433"""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    engine = create_async_engine(database_url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_event_store_real_append_and_list(event_store):
    """真 PG append + list 端到端"""
    eid = await event_store.append(
        agent_id="int_test_agent",
        kind="decision",
        content="test integration event",
        payload={"source": "integration_test"},
    )
    assert eid > 0
    events = await event_store.list(agent_id="int_test_agent", kind="decision")
    assert any(e.id == eid and e.content == "test integration event" for e in events)


@pytest.mark.asyncio
async def test_tick_loop_one_iteration_fake_llm(redis_url, database_url):
    """真服务(redis+pg)+ fake LLM,跑 1 次 tick,验证决策入 PG"""
    from unittest.mock import AsyncMock, MagicMock
    from event_memory_system import EventStore
    from virtual_world_engine import World
    from agent_runtime import Agent
    from memory_reflection import ShortTermMemory, LongTermMemory, Reflector
    import redis.asyncio as redis_async

    # fake LLM(避免烧钱)
    fake_llm = MagicMock()
    fake_llm.call = AsyncMock(return_value={
        "reasoning": "fake decision",
        "action": {"name": "go_to", "target": "厨房", "params": {}},
    })
    fake_llm.aclose = AsyncMock()

    r = redis_async.from_url(redis_url)
    stm = ShortTermMemory(r)
    ltm = LongTermMemory(r)
    reflector = Reflector(fake_llm, stm, ltm)

    world = World()
    store = EventStore(database_url)
    await store.init_schema()

    agent = Agent(
        agent_id="int_tick_a", name="测试A", persona={"desc": "test"},
        llm=fake_llm, stm=stm, ltm=ltm, reflector=reflector,
    )
    world.place("int_tick_a", "李四家")
    snap = world.snapshot("int_tick_a")
    action = await agent.decide(snap)
    assert action.name == "go_to"
    assert action.target == "厨房"

    # 入 PG
    eid = await store.append(
        agent_id="int_tick_a", kind="decision",
        content=str(action.to_dict()),
    )
    events = await store.list(agent_id="int_tick_a", kind="decision")
    assert any(e.id == eid for e in events)

    # 验证 LLM 真被调(且是 fake LLM,无网络)
    assert fake_llm.call.await_count >= 1
    await r.aclose()