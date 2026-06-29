"""town bootstrap:装配所有 L1/L2 组件,返回运行时上下文

这是 town 服务的唯一启动入口。装配流程:
  1. 加载 personas.yaml + locations.yaml(阶段 3 / REQ-7cfc9696)
     — 自定义 config_dir 优先(TOWN_CONFIG_DIR env),有错则回退到 base 默认
  2. 基础设施:LLMClient(读 env var)、EventBus(Redis)、EventStore(Postgres)
  3. L1 组件:ShortTermMemory、LongTermMemory、Reflector
  4. L2 组件:World、DialogueTrigger、DialogueGenerator
  5. 组装 5 个 Agent 实例,各放到 world 起始位置

返回 dict(称 ctx),由 main.py / FastAPI 路由 / WebSocket 共享。
阶段 3 额外字段:locations / config_source / config_errors,供前端 toast + locations 端点。
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv  # uv add python-dotenv

from llm_client import LLMClient
from memory_reflection import ShortTermMemory, LongTermMemory, Reflector
from event_bus import EventBus
from agent_runtime import Agent
from virtual_world_engine import World
from event_memory_system import EventStore
from dialogue_generator import DialogueTrigger, DialogueGenerator

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
    custom_dir = os.getenv("TOWN_CONFIG_DIR")
    personas, locations, errors = [], [], []
    config_source = "base"
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
            config_source = "base"  # 回退
        else:
            config_source = "custom"
    else:
        personas, locations, errors = load_config(_BASE_CONFIG_DIR)
        config_source = "base"
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
    world = World()
    event_store = EventStore(db_url)
    await event_store.init_schema()
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
    }
