# packages/event-memory-system/src/event_memory_system/store.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from .models import Base, Event, Dialogue, DialogueMessage
from datetime import datetime


class EventStore:
    def __init__(self, db_url: str):
        self.engine = create_async_engine(db_url)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init_schema(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def append(self, *, agent_id: str, kind: str, content: str, payload: dict | None = None) -> int:
        async with self.Session() as s:
            e = Event(agent_id=agent_id, kind=kind, content=content, payload=payload or {})
            s.add(e)
            await s.commit()
            return e.id

    async def list(
        self,
        agent_id: str | None = None,
        since: datetime | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        async with self.Session() as s:
            stmt = select(Event)
            if agent_id:
                stmt = stmt.where(Event.agent_id == agent_id)
            if since:
                stmt = stmt.where(Event.ts >= since)
            if kind:
                stmt = stmt.where(Event.kind == kind)
            stmt = stmt.order_by(Event.ts.desc()).limit(limit)
            r = await s.scalars(stmt)
            return list(r)

    async def create_dialogue(self, location: str) -> int:
        async with self.Session() as s:
            d = Dialogue(location=location)
            s.add(d)
            await s.commit()
            return d.id

    async def add_dialogue_message(self, dialogue_id: int, agent_id: str, content: str):
        async with self.Session() as s:
            m = DialogueMessage(dialogue_id=dialogue_id, agent_id=agent_id, content=content)
            s.add(m)
            await s.commit()

    async def get_dialogue(self, dialogue_id: int) -> list[DialogueMessage]:
        async with self.Session() as s:
            stmt = (
                select(DialogueMessage)
                .where(DialogueMessage.dialogue_id == dialogue_id)
                .order_by(DialogueMessage.ts)
            )
            r = await s.scalars(stmt)
            return list(r)