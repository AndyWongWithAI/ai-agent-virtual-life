"""C5 fix 测试:models 的 ts 列应使用 timezone-aware datetime

- Event / Dialogue / DialogueMessage 的 default 应返回 tz-aware UTC
- 列定义应使用 DateTime(timezone=True)
"""
from datetime import datetime, timezone
import pytest

from event_memory_system.models import Event, Dialogue, DialogueMessage, _utcnow, Base
from sqlalchemy import DateTime


def test_utcnow_is_timezone_aware():
    """_utcnow() 必须返回 timezone-aware UTC datetime"""
    ts = _utcnow()
    assert ts.tzinfo is not None
    assert ts.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_event_default_ts_is_timezone_aware():
    """Event(ts 缺省) 写入 sqlite/Postgres 后,ts 字段应为 tz-aware"""
    from event_memory_system.store import EventStore

    store = EventStore(db_url="sqlite+aiosqlite:///:memory:")
    await store.init_schema()
    eid = await store.append(agent_id="a1", kind="decision", content="回家")
    events = await store.list(agent_id="a1")
    assert len(events) == 1
    assert events[0].ts.tzinfo is not None, (
        f"Event.ts should be tz-aware after insert; got {events[0].ts!r}"
    )


def test_models_columns_use_timezone_true():
    """列定义必须是 DateTime(timezone=True),否则 Postgres 写入会变 naive

    这里用 TypeDecorator(TZDateTime),impl 是 DateTime(timezone=True),
    所以 SQLAlchemy 仍会正确发出 timestamptz (Postgres) / normalize (SQLite)。
    """
    from event_memory_system.models import TZDateTime
    from sqlalchemy import DateTime as SAType

    for cls in (Event, Dialogue, DialogueMessage):
        for col in cls.__table__.columns:
            if col.name in ("ts", "started_at"):
                # 可能是裸 DateTime 或 TypeDecorator 包了 DateTime
                inner = col.type.impl if hasattr(col.type, "impl") else col.type
                assert isinstance(inner, SAType), (
                    f"{cls.__name__}.{col.name} not DateTime-based, got {col.type!r}"
                )
                assert inner.timezone is True, (
                    f"{cls.__name__}.{col.name} should be DateTime(timezone=True)"
                )
