import pytest
from event_memory_system.store import EventStore


@pytest.mark.asyncio
async def test_append_and_list():
    store = EventStore(db_url="sqlite+aiosqlite:///:memory:")
    await store.init_schema()
    eid = await store.append(agent_id="a1", kind="decision", content="回家")
    events = await store.list_events(agent_id="a1")
    assert len(events) == 1
    assert events[0].content == "回家"


@pytest.mark.asyncio
async def test_dialogue_roundtrip():
    store = EventStore(db_url="sqlite+aiosqlite:///:memory:")
    await store.init_schema()
    did = await store.create_dialogue(location="客厅")
    await store.add_dialogue_message(did, "a1", "你好")
    await store.add_dialogue_message(did, "a2", "你好啊")
    msgs = await store.get_dialogue(did)
    assert len(msgs) == 2
    assert msgs[0].content == "你好"


def test_no_list_shadow_in_class():
    """回归:EventStore 不能有同名 list method(遮蔽 builtin list,导致 3.12 下 annotations 求值崩)"""
    import inspect
    members = inspect.getmembers(EventStore, predicate=inspect.isfunction)
    method_names = [name for name, _ in members]
    assert "list" not in method_names, (
        f"EventStore 不能有名为 'list' 的 method(遮蔽 builtin list)"
    )