"""阶段 3 v2 块 5:内置 5+5 默认场景 seeder

设计依据:`~/.claude/specs/designs/2026-06-30-stage3-yaml-config-design.md`
§ 数据模型 — DB 化。

启动时检查:若 scenes 表为空,从 config/base/personas.yaml + locations.yaml
灌入一个 builtin=True 的"小镇默认"场景(idempotent — 已存在场景不重建)。

测试:apps/town/tests/test_scene_seeder.py (in-memory SQLite)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from . import scene_store

logger = logging.getLogger(__name__)

# 默认 base 配置目录(沿用 bootstrap._BASE_CONFIG_DIR 的位置)
_BASE_CONFIG_DIR = Path(__file__).parent / "config" / "base"

# 默认 builtin scene 的元数据(per 设计清单 § 数据模型 — DB 化)
_DEFAULT_SCENE_NAME = "小镇默认"
_DEFAULT_SCENE_DESCRIPTION = "默认 5+5 配置(从 config/base/*.yaml 灌入)"


async def seed_default_scene_if_empty(engine: AsyncEngine) -> int:
    """启动时检查:若 scenes 表为空,创建内置 5+5 默认场景。

    Args:
        engine:scene_store 的 AsyncEngine(已 init_schema)

    Returns:
        已存在/新建场景的 id。scenes 表非空时直接返首个 scene id(不重建)。
    """
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        existing = await scene_store.list_scenes(session)
        if existing:
            logger.info(
                "[scene_seeder] scenes 表已有 %d 条,跳过 seed(返 id=%d)",
                len(existing), existing[0]["id"],
            )
            return existing[0]["id"]

    # 读 YAML(支持 env 覆盖 — 与 bootstrap._load_config_with_fallback 一致)
    personas, locations, errors = _read_default_yaml()
    if errors:
        for e in errors:
            logger.warning("[scene_seeder] YAML 解析告警: %s", e["message"])
    if not personas or not locations:
        raise RuntimeError(
            f"[scene_seeder] base YAML 配置损坏,无法 seed 默认场景。"
            f"errors={errors}"
        )

    async with Session() as session:
        scene_id = await scene_store.create_scene(
            session,
            name=_DEFAULT_SCENE_NAME,
            description=_DEFAULT_SCENE_DESCRIPTION,
            is_builtin=True,
        )
        for p in personas:
            await scene_store.add_persona(
                session,
                scene_id=scene_id,
                agent_id=p["id"],
                name=p["name"],
                persona=p["persona"],
                start_location=p.get("start_location", ""),
                color=p.get("color", "#888888"),
            )
        for loc in locations:
            await scene_store.add_location(
                session,
                scene_id=scene_id,
                name=loc["name"],
                x=int(loc["x"]),
                y=int(loc["y"]),
                color=loc.get("color", "#FFD700"),
                adjacency=list(loc.get("adjacency") or []),
            )
        logger.info(
            "[scene_seeder] 已创建默认场景 id=%d,personas=%d,locations=%d",
            scene_id, len(personas), len(locations),
        )
        return scene_id


def _read_default_yaml() -> tuple[list[dict], list[dict], list[dict]]:
    """读 config/base/*.yaml(支持 TOWN_CONFIG_DIR 覆盖 — 与 bootstrap 一致)。

    复用 config_loader.load_config(已有校验 + 错误聚合逻辑,避免重新发明)。
    """
    from .config_loader import load_config

    custom_dir = os.getenv("TOWN_CONFIG_DIR")
    if custom_dir:
        custom_path = Path(custom_dir)
        if custom_path.exists():
            return load_config(custom_path)
    return load_config(_BASE_CONFIG_DIR)