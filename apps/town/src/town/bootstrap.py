"""town bootstrap:装配所有 L1/L2 组件,返回运行时上下文

这是 town 服务的唯一启动入口。装配流程:
  1. 加载 personas.yaml + locations.yaml(阶段 3 / REQ-7cfc9696)
     — 自定义 config_dir 优先(TOWN_CONFIG_DIR env),有错则回退到 base 默认
  2. 基础设施:LLMClient(读 env var)、EventBus(Redis)、EventStore(Postgres)
     + scene_store engine + init_schema(阶段 3 v2 块 5)
  3. 阶段 3 v2:seed 默认 5+5 场景到 DB(scene_seeder)
  4. L1 组件:ShortTermMemory、LongTermMemory、Reflector
  5. L2 组件:World、DialogueTrigger、DialogueGenerator
  6. 组装 5 个 Agent 实例,各放到 world 起始位置

返回 dict(称 ctx),由 main.py / FastAPI 路由 / WebSocket 共享。
阶段 3 额外字段:locations / config_source / config_errors,供前端 toast + locations 端点。
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv  # uv add python-dotenv
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from llm_client import LLMClient
from memory_reflection import ShortTermMemory, LongTermMemory, Reflector
from event_bus import EventBus
from agent_runtime import Agent
from virtual_world_engine import World
from event_memory_system import EventStore
from dialogue_generator import DialogueTrigger, DialogueGenerator

from . import scene_seeder, scene_store
from .config_loader import load_config

# 加载 .env(放在 town/ 目录,即 apps/town/.env)
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# 默认 base 配置目录(随仓库提交,保证 demo 一定跑得起来)
_BASE_CONFIG_DIR = Path(__file__).parent / "config" / "base"


async def bootstrap() -> dict:
    """装配所有组件,返回运行时上下文。

    配置来源(全部支持 env var 覆盖):
        REDIS_URL                 — Redis 连接串
        DATABASE_URL              — Postgres 异步连接串
        MINIMAX_API_KEY           — MiniMax M3 API key(必填)
        MINIMAX_BASE_URL          — MiniMax OpenAI 兼容端点
        MINIMAX_MODEL             — 模型名(默认 MiniMax-M3)
        LLM_DAILY_BUDGET_CNY      — 日预算,单位 CNY
        TOWN_CONFIG_DIR           — 自定义 YAML 配置目录(默认走内置 base)
    """
    # 1. 加载 personas + locations(阶段 3)
    #    优先级:TOWN_CONFIG_DIR(自定义)→ base(默认)
    #    errors 非空 → logger.warning + 回退 base;ctx["config_source"]="base"
    personas, locations, errors, config_source = _load_config_with_fallback()
    if not personas or not locations:
        # 极端兜底:连 base 都坏,直接抛,不让服务带着空配置启动
        raise RuntimeError(
            f"base 配置损坏,无法启动。errors={errors}"
        )

    # 2. 基础设施
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://town:town_dev_pwd@localhost:5432/town",
    )
    llm = LLMClient(
        api_key=os.environ["MINIMAX_API_KEY"],
        base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1"),
        redis_url=redis_url,
        daily_budget_cny=float(os.getenv("LLM_DAILY_BUDGET_CNY", "20")),
        model=os.getenv("MINIMAX_MODEL", "MiniMax-M3"),
    )

    # 3. L1 组件
    bus = EventBus(redis_url)
    stm = ShortTermMemory(bus.redis)
    ltm = LongTermMemory(bus.redis)
    reflector = Reflector(llm, stm, ltm)

    # 4. L2 组件
    # 阶段 3 (T6):把 YAML locations 的 name 列表注入 World,替换硬编码
    # DEFAULT_LOCATIONS,让自定义地点名也能通过 place() 的 assert。
    world = World(valid_locations=[loc["name"] for loc in locations])
    event_store = EventStore(db_url)
    await event_store.init_schema()
    # 阶段 3 v2:scene_store 自己的 engine(用同一 DATABASE_URL,与 EventStore 同库
    # 不同 Base — scene_store.Base 隔离 metadata,避免交叉污染)
    scene_engine: AsyncEngine = create_async_engine(db_url)
    await scene_store.init_schema(scene_engine)
    # seed 默认 5+5 场景(idempotent — scenes 表非空时跳过)
    await scene_seeder.seed_default_scene_if_empty(scene_engine)
    trigger = DialogueTrigger()
    dialogue_gen = DialogueGenerator(llm)

    # 5. 组装 5 个 Agent
    agents: dict[str, Agent] = {}
    for p in personas:
        world.place(p["id"], p["start_location"])
        a = Agent(
            agent_id=p["id"],
            name=p["name"],
            persona=p["persona"],
            llm=llm,
            stm=stm,
            ltm=ltm,
            reflector=reflector,
        )
        agents[p["id"]] = a

    return {
        "llm": llm,
        "bus": bus,
        "stm": stm,
        "ltm": ltm,
        "reflector": reflector,
        "world": world,
        "event_store": event_store,
        "trigger": trigger,
        "dialogue_gen": dialogue_gen,
        "agents": agents,
        "personas": personas,
        # 阶段 3:配置相关字段供 /api/locations + /api/config-status 使用
        "locations": locations,
        "config_source": config_source,
        "config_errors": errors,
        # 阶段 3 v2(块 5):scene_store DB 引擎 + session factory
        # — /api/scenes/* 端点用它读 scenes/personas/locations
        # — activate 时调 scene_store.activate_scene(session, scene_id)
        # — session_maker 不重连(共享 engine,expire_on_commit=False 避免 lazy load 失效)
        "scene_engine": scene_engine,
        "scene_session_maker": async_sessionmaker(
            scene_engine, expire_on_commit=False,
        ),
    }


def _load_config_with_fallback() -> tuple[list[dict], list[dict], list[dict], str]:
    """bootstrap() 与 bootstrap_reload() 共用的「读 YAML 配置 + 回退 base」逻辑。

    优先 TOWN_CONFIG_DIR(自定义),有错/未设 → 回退到内置 base。

    Returns:
        (personas, locations, errors, config_source)
        config_source ∈ {"custom", "base"}
        极端 base 都坏时 → (空, 空, errors, "base"),由调用方决定 raise。
    """
    custom_dir = os.getenv("TOWN_CONFIG_DIR")
    if custom_dir:
        custom_path = Path(custom_dir)
        personas, locations, errors = load_config(custom_path)
        if errors:
            logger.warning(
                "[town] 自定义配置 %s 有 %d 条错误,回退到 base 默认:",
                custom_path, len(errors),
            )
            for e in errors:
                logger.warning("  - %s", e["message"])
            personas, locations, errors = load_config(_BASE_CONFIG_DIR)
            return personas, locations, errors, "base"
        return personas, locations, errors, "custom"
    personas, locations, errors = load_config(_BASE_CONFIG_DIR)
    return personas, locations, errors, "base"


async def bootstrap_reload(
    prev_ctx: dict | None = None,
    personas: list[dict] | None = None,
    locations: list[dict] | None = None,
) -> dict:
    """阶段 3 (REQ-7cfc9696) 重启生效端点用:复用基础设施,只重新装配 world + agents。

    关键约束(架构警告 — 任务 T9):
      - 复用 prev_ctx 里的 llm / bus / stm / ltm / reflector / event_store / trigger
        / dialogue_gen / scene_engine / scene_session_maker(避免重连花销,也不踢 WS)
      - event_store 不动(保留历史事件,Postgres 持久)
      - reflector 不动(6h 反思连续,不重置)
      - 只重新装配 world + agents(personas/locations 来源可能来自 YAML 也可能来自 DB)

    阶段 3 v2(块 4 / T20):personas/locations 参数允许直接传 DB 数据(来自
    scene_store.activate_scene)而不走 YAML 路径;两参数都传则覆盖 YAML 加载,
    任一为 None 则回退 YAML(向后兼容 v1 /api/restart 端点)。

    Args:
        prev_ctx:上一轮 bootstrap() 装配的 ctx,用于复用基础设施。
                 None 时退化到完整 bootstrap()(向后兼容)。
        personas:可选,直接传入 personas 列表(从 DB 读出的格式)。
                 None → 回退 YAML。
        locations:可选,直接传入 locations 列表(从 DB 读出的格式)。
                   None → 回退 YAML。

    Returns:
        新 ctx dict(人员/地点配置已重新加载,基础设施沿用)
    """
    if prev_ctx is None:
        # 兼容模式:没传旧 ctx → 走完整 bootstrap()(含基础设施装配)
        return await bootstrap()

    # 1. 决定 personas/locations 来源
    #    - 两参数都给 → 用传入的(DB 数据,v2 路径)
    #    - 任一为 None → 回退 YAML(v1 路径,/api/restart 走这里)
    if personas is not None and locations is not None:
        # v2 DB 路径:activate 端点传入,无 errors / config_source 概念(都是 DB)
        errors: list[dict] = []
        config_source = "db"
        logger.info(
            "bootstrap_reload: using %d personas + %d locations from DB (scene activation)",
            len(personas), len(locations),
        )
    else:
        # v1 YAML 路径:/api/restart 重启生效
        personas, locations, errors, config_source = _load_config_with_fallback()
        if not personas or not locations:
            raise RuntimeError(
                f"重启失败:配置损坏。errors={errors}"
            )

    # 2. 复用基础设施 — 关键不要 close 任何连接(会断 WS / 重置反思状态)
    #    prev_ctx 是真 bootstrap() 装配结果,字段都齐全
    llm = prev_ctx["llm"]
    bus = prev_ctx["bus"]
    stm = prev_ctx["stm"]
    ltm = prev_ctx["ltm"]
    reflector = prev_ctx["reflector"]
    event_store = prev_ctx["event_store"]
    trigger = prev_ctx["trigger"]
    dialogue_gen = prev_ctx["dialogue_gen"]
    # 阶段 3 v2:scene_engine / scene_session_maker 沿用(共享 DB 连接池)
    scene_engine = prev_ctx.get("scene_engine")
    scene_session_maker = prev_ctx.get("scene_session_maker")

    # 3. 重新装配 world(新 valid_locations 列表)+ agents(新 persona)
    world = World(valid_locations=[loc["name"] for loc in locations])
    agents: dict[str, Agent] = {}
    for p in personas:
        world.place(p["id"], p["start_location"])
        a = Agent(
            agent_id=p["id"],
            name=p["name"],
            persona=p["persona"],
            llm=llm,
            stm=stm,
            ltm=ltm,
            reflector=reflector,
        )
        agents[p["id"]] = a

    return {
        # 沿用(避免重连 + 不踢 WS)
        "llm": llm,
        "bus": bus,
        "stm": stm,
        "ltm": ltm,
        "reflector": reflector,
        "event_store": event_store,
        "trigger": trigger,
        "dialogue_gen": dialogue_gen,
        "scene_engine": scene_engine,
        "scene_session_maker": scene_session_maker,
        # 重新装配
        "world": world,
        "agents": agents,
        "personas": personas,
        "locations": locations,
        "config_source": config_source,
        "config_errors": errors,
    }
