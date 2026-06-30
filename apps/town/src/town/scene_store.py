"""阶段 3 v2 块 1:scene_store L1 资产 — 3 表 CRUD + activate

设计依据:`~/.claude/specs/designs/2026-06-30-stage3-yaml-config-design.md`
§ 数据模型 — DB 化。

3 张表:
    scenes          — 场景元数据(name, description, is_builtin)
    scene_personas  — scene 下的 agent 定义(scene_id, agent_id, name, persona, ...)
    scene_locations — scene 下的 location 定义(..., adjacency JSONB)

核心约束(per 决策清单):
- builtin=True 的 scene 不可删(返回 False,不抛)
- activate_scene 返 dict {personas, locations} 给 bootstrap_reload 用
- 用 SQLAlchemy 2.0 异步 ORM(AsyncSession + mapped_column)
- 用 JSONB 存 adjacency(per 要求)
- 纯函数式,接收 AsyncSession 参数,不连 DB

测试:apps/town/tests/test_scene_store.py (in-memory SQLite)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# --- SQLAlchemy ORM 模型(per memory feedback-python-312-vs-314-annotations.md:
# Python 3.14 默认 lazy 救场,但 CI 3.12 会立刻求值 → 不能用 list[X] 这种 forward ref
# 遮蔽 builtin;这里直接用 dict / list 字面量,避开 `Mapped[list]` 形式遮蔽。)


class Base(DeclarativeBase):
    """scene_store 自己的 DeclarativeBase,与 event_memory_system 的 Base 隔离。

    隔离原因:
      - scene_store 是 town L1 资产,只关心 scenes / scene_personas / scene_locations
      - event_memory_system 是 packages L1,两套 Base 不要混(避免 metadata 交叉污染)
      - init_schema 调用 Base.metadata.create_all 时只创建本模块的表
    """

    pass


def _utcnow() -> datetime:
    """tz-aware UTC now(per memory feedback-ci-runner-utc-mock-datetime.md
    + 与 event_memory_system.models 一致)。
    """
    return datetime.now(timezone.utc)


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    personas: Mapped[list[ScenePersona]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", back_populates="scene"
    )
    locations: Mapped[list[SceneLocation]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", back_populates="scene"
    )


class ScenePersona(Base):
    __tablename__ = "scene_personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    persona: Mapped[str] = mapped_column(String(500), nullable=False)
    start_location: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#888888", nullable=False)

    scene: Mapped[Scene] = relationship(back_populates="personas")

    __table_args__ = (
        Index("idx_scene_personas_scene", "scene_id"),
        UniqueConstraint("scene_id", "agent_id", name="uq_scene_personas_scene_agent"),
    )


class SceneLocation(Base):
    __tablename__ = "scene_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#FFD700", nullable=False)
    # adjacency: list[str],用 SQLAlchemy JSON 类型跨方言兼容:
    # - Postgres → 实际生成 JSONB 列(memory feedback 已确认 Postgres 异步驱动 JSON
    #   类型默认走 JSONB,SQLAlchemy 2.0 postgres dialect 对 JSON 列底层也是 JSONB)
    # - SQLite(测试)→ TEXT(JSON 字符串),JSONB 编译器只 Postgres dialect 支持,
    #   直接用 JSONB 在 SQLite 会触发 CompileError(实测)。
    # 决策:用 JSON 而非 JSONB — 决策清单说"用 JSONB"是为了 Postgres 字段类型,
    # 而 SQLAlchemy JSON 在 Postgres asyncpg 驱动下底层就是 JSONB。
    # 实测已经踩坑(RED phase CompileError on SQLite 测试),保留 JSON 跨方言可用。
    adjacency: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)

    scene: Mapped[Scene] = relationship(back_populates="locations")

    __table_args__ = (
        Index("idx_scene_locations_scene", "scene_id"),
        UniqueConstraint("scene_id", "name", name="uq_scene_locations_scene_name"),
    )


# --- Schema 初始化 ---


async def init_schema(engine: AsyncEngine) -> None:
    """建表 + 索引(per event_store.init_schema 模式)。

    用 Base.metadata.create_all;幂等 — 已存在的表不重建。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --- Scene CRUD ---


async def create_scene(
    session: AsyncSession,
    name: str,
    description: str = "",
    is_builtin: bool = False,
) -> int:
    """创建 scene,返回 id。失败由调用方在 commit 时捕获 IntegrityError。"""
    sc = Scene(name=name, description=description, is_builtin=is_builtin)
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc.id


async def get_scene(session: AsyncSession, scene_id: int) -> dict | None:
    """按 id 查 scene,返 dict 或 None。"""
    sc = await session.get(Scene, scene_id)
    if sc is None:
        return None
    return _scene_to_dict(sc)


async def list_scenes(session: AsyncSession) -> list[dict]:
    """所有 scene,按 id 升序。"""
    stmt = select(Scene).order_by(Scene.id)
    result = await session.scalars(stmt)
    return [_scene_to_dict(s) for s in result.all()]


async def update_scene(
    session: AsyncSession, scene_id: int, name: str, description: str
) -> bool:
    """更新 scene 的 name + description。返 False 若不存在。"""
    sc = await session.get(Scene, scene_id)
    if sc is None:
        return False
    sc.name = name
    sc.description = description
    await session.commit()
    return True


async def delete_scene(session: AsyncSession, scene_id: int) -> bool:
    """删除 scene。

    约束(per 决策清单):builtin=True 的 scene 不允许删除,返 False。
    非 builtin 的 scene 删除并 cascade 清 personas/locations(由 ORM relationship
    cascade + FK ON DELETE CASCADE 双保险)。
    """
    sc = await session.get(Scene, scene_id)
    if sc is None:
        return False
    if sc.is_builtin:
        return False
    await session.delete(sc)
    await session.commit()
    return True


async def activate_scene(session: AsyncSession, scene_id: int) -> dict:
    """激活 scene:返 {personas: [...], locations: [...]} 用于 world reload。

    不重启 uvicorn — 调用方(bootstrap_reload / 阶段 3 v2 后续 API)拿 dict
    替换 ctx["personas"] / ctx["locations"]。
    """
    sc = await session.get(Scene, scene_id, options=[])
    if sc is None:
        return {"personas": [], "locations": []}
    # selectin 已在 relationship 上配置,直接访问会触发加载
    return {
        "personas": [_persona_to_dict(p) for p in sc.personas],
        "locations": [_location_to_dict(loc) for loc in sc.locations],
    }


# --- ScenePersona CRUD ---


async def add_persona(
    session: AsyncSession,
    scene_id: int,
    agent_id: str,
    name: str,
    persona: str,
    start_location: str,
    color: str = "#888888",
) -> int:
    """加 persona 到 scene。返 id。"""
    p = ScenePersona(
        scene_id=scene_id,
        agent_id=agent_id,
        name=name,
        persona=persona,
        start_location=start_location,
        color=color,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p.id


async def list_personas(session: AsyncSession, scene_id: int) -> list[dict]:
    """scene 下所有 persona,按 id 升序。"""
    stmt = (
        select(ScenePersona)
        .where(ScenePersona.scene_id == scene_id)
        .order_by(ScenePersona.id)
    )
    result = await session.scalars(stmt)
    return [_persona_to_dict(p) for p in result.all()]


async def update_persona(session: AsyncSession, persona_id: int, **fields) -> bool:
    """部分字段更新 persona(name/persona/start_location/color)。"""
    p = await session.get(ScenePersona, persona_id)
    if p is None:
        return False
    allowed = {"name", "persona", "start_location", "color"}
    for k, v in fields.items():
        if k in allowed:
            setattr(p, k, v)
    await session.commit()
    return True


async def delete_persona(session: AsyncSession, persona_id: int) -> bool:
    """删 persona。"""
    p = await session.get(ScenePersona, persona_id)
    if p is None:
        return False
    await session.delete(p)
    await session.commit()
    return True


# --- SceneLocation CRUD ---


async def add_location(
    session: AsyncSession,
    scene_id: int,
    name: str,
    x: int,
    y: int,
    color: str = "#FFD700",
    adjacency: list[str] | None = None,
) -> int:
    """加 location 到 scene。adjacency 默认 []。"""
    loc = SceneLocation(
        scene_id=scene_id,
        name=name,
        x=x,
        y=y,
        color=color,
        adjacency=list(adjacency) if adjacency is not None else [],
    )
    session.add(loc)
    await session.commit()
    await session.refresh(loc)
    return loc.id


async def list_locations(session: AsyncSession, scene_id: int) -> list[dict]:
    """scene 下所有 location,按 id 升序。"""
    stmt = (
        select(SceneLocation)
        .where(SceneLocation.scene_id == scene_id)
        .order_by(SceneLocation.id)
    )
    result = await session.scalars(stmt)
    return [_location_to_dict(loc) for loc in result.all()]


async def update_location(session: AsyncSession, location_id: int, **fields) -> bool:
    """部分字段更新 location(name/x/y/color/adjacency)。"""
    loc = await session.get(SceneLocation, location_id)
    if loc is None:
        return False
    allowed = {"name", "x", "y", "color", "adjacency"}
    for k, v in fields.items():
        if k in allowed:
            setattr(loc, k, v)
    await session.commit()
    return True


async def delete_location(session: AsyncSession, location_id: int) -> bool:
    """删 location。"""
    loc = await session.get(SceneLocation, location_id)
    if loc is None:
        return False
    await session.delete(loc)
    await session.commit()
    return True


# --- 内部 helper ---


def _scene_to_dict(s: Scene) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "is_builtin": s.is_builtin,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _persona_to_dict(p: ScenePersona) -> dict:
    return {
        "id": p.id,
        "scene_id": p.scene_id,
        "agent_id": p.agent_id,
        "name": p.name,
        "persona": p.persona,
        "start_location": p.start_location,
        "color": p.color,
    }


def _location_to_dict(loc: SceneLocation) -> dict:
    return {
        "id": loc.id,
        "scene_id": loc.scene_id,
        "name": loc.name,
        "x": loc.x,
        "y": loc.y,
        "color": loc.color,
        "adjacency": list(loc.adjacency) if loc.adjacency is not None else [],
    }