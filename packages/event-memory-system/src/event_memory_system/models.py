# packages/event-memory-system/src/event_memory_system/models.py
from datetime import datetime, timezone
from sqlalchemy import TypeDecorator, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, JSON, ForeignKey


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    """C5 fix:返回 timezone-aware UTC datetime,避免 utcnow() 在 3.12+ 弃用
    + naive datetime 写入 Postgres 被 session timezone 解释。
    """
    return datetime.now(timezone.utc)


class TZDateTime(TypeDecorator):
    """DateTime 列,强制存读都用 timezone-aware。

    - 写入:naive datetime 视为 UTC;已带 tzinfo 保留。
    - 读取:driver 返回的 naive datetime 重新附加 UTC(SQLite 没有原生 tz 列,
      但 Postgres 的 timestamptz 会自带 tzinfo;两边统一行为)。

    C5 fix:关键 — 保证应用层拿到的 Event.ts 永远是 tz-aware,避免
    `Event.ts >= since_tz_aware` 时 TypeError。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(
        TZDateTime(), default=_utcnow, index=True
    )


class Dialogue(Base):
    __tablename__ = "dialogues"
    id: Mapped[int] = mapped_column(primary_key=True)
    location: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(
        TZDateTime(), default=_utcnow
    )


class DialogueMessage(Base):
    __tablename__ = "dialogue_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    dialogue_id: Mapped[int] = mapped_column(ForeignKey("dialogues.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(
        TZDateTime(), default=_utcnow
    )
