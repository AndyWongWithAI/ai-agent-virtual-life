"""town FastAPI server:HTTP 路由 + WebSocket + 后台 tick 循环

- HTTP:
    GET /            -> index.html(前端在 Task 12 接入)
    GET /api/agents  -> 5 个 agent 当前状态(id/name/location)
- WebSocket:
    /ws              -> 推送 AGENT_DECISION / DIALOGUE_MESSAGE 事件
- 后台:
    tick_loop        -> 按 TickScheduler 节奏(白天 60s/夜间 300s)驱动每个 agent 决策

启动顺序(不可乱!):
    1. bootstrap():装配 L1/L2/agents(连 Redis/Postgres/LLM)
    2. ctx["bus"].subscribe(...):把 WS 广播 handler 注册到 bus
    3. bus_task = create_task(bus.run_forever()):后台循环监听 Redis pub/sub
    4. tick_task = create_task(tick_loop()):按 schedule 跑每 tick
否则会出现: publish 成功但本地 handler 一个都不触发(I5)或 bus 监听 task
被 GC(C1)。
"""
import asyncio
import json
import logging
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from itertools import combinations
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_behavior_orchestrator import TickScheduler
from event_bus import Topic
from virtual_world_engine import DEFAULT_LOCATIONS

from .bootstrap import bootstrap

logger = logging.getLogger(__name__)

app = FastAPI(title="AI 智能体虚拟小镇")
ctx: dict | None = None
tick_task: asyncio.Task | None = None
bus_task: asyncio.Task | None = None  # C1 fix:保留引用,避免 GC
# WebSocket 客户端连接列表(广播用)
ws_clients: list[WebSocket] = []


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """FastAPI lifespan:替代已 deprecated 的 @app.on_event("startup")。
    启动顺序见模块 docstring。
    """
    global ctx, tick_task, bus_task
    # I7:bootstrap 失败给友好提示,而不是让服务 500
    try:
        ctx = await bootstrap()
    except KeyError as e:
        logger.exception("bootstrap failed: missing env var %s", e)
        print(
            f"[town] 配置缺失:{e}。请检查 apps/town/.env 是否设置了该变量"
            "(如 MINIMAX_API_KEY / REDIS_URL / DATABASE_URL)。",
            file=sys.stderr,
        )
        raise
    except Exception:
        logger.exception("bootstrap failed")
        print(
            "[town] 启动失败:详细 traceback 见上面日志。"
            "请检查 Redis (6380) / Postgres (5433) 是否已 docker-compose up。",
            file=sys.stderr,
        )
        raise

    # 全局 WS 广播 handler:把 bus 上的 AGENT_DECISION / DIALOGUE_MESSAGE 广播给所有 ws 客户端
    async def _ws_broadcast(payload: dict):
        dead: list[WebSocket] = []
        # 并发推,避免一个慢 client 阻塞其他 client
        results = await asyncio.gather(
            *[client.send_json(payload) for client in ws_clients],
            return_exceptions=True,
        )
        for client, res in zip(list(ws_clients), results):
            if isinstance(res, Exception):
                dead.append(client)
        for d in dead:
            if d in ws_clients:
                ws_clients.remove(d)

    ctx["bus"].subscribe(Topic.AGENT_DECISION, _ws_broadcast)
    ctx["bus"].subscribe(Topic.DIALOGUE_MESSAGE, _ws_broadcast)
    ctx["bus"].subscribe(Topic.DIALOGUE_START, _ws_broadcast)

    # C1 fix:保存 task handle,避免被 GC(同时 bus.run_forever 自带 while True 重连)
    bus_task = asyncio.create_task(ctx["bus"].run_forever(), name="bus-run-forever")
    # 启动 tick 循环
    tick_task = asyncio.create_task(tick_loop(), name="tick-loop")

    yield

    # shutdown
    if tick_task:
        tick_task.cancel()
        try:
            await tick_task
        except (asyncio.CancelledError, Exception):
            pass
    if bus_task:
        bus_task.cancel()
        try:
            await bus_task
        except (asyncio.CancelledError, Exception):
            pass
    for ws in list(ws_clients):
        try:
            await ws.close()
        except Exception:
            pass


# I6:用 lifespan 替代 @app.on_event("startup")/shutdown
app.router.lifespan_context = lifespan


async def tick_loop():
    """按 TickScheduler 节奏驱动 run_tick,白天 60s/夜间 300s。
    I12:启动时先 3s warmup(给 Redis/Postgres 握手 + WS handler 注册完成),
    然后**立即**跑一次 first tick,UX 上让用户启动后马上看到动作,而不是
    空等一个 interval。
    """
    # I12:warmup,放最前
    await asyncio.sleep(3)
    # 立即跑 first tick(I12)
    try:
        await run_tick()
    except Exception as e:
        logger.exception("first tick error: %s", e)

    scheduler = TickScheduler()
    while True:
        interval = scheduler.interval_for()
        await asyncio.sleep(interval)
        try:
            await run_tick()
        except Exception:
            logger.exception("tick_loop error")


async def run_tick():
    """每个 tick:让 5 个 agent 各自 decide 一次,记录到 event_store + 推总线"""
    assert ctx is not None
    last_actions: dict[str, str] = {}
    for agent_id, agent in ctx["agents"].items():
        snap = ctx["world"].snapshot(agent_id)
        action = await agent.decide(snap)
        last_actions[agent_id] = action.name
        # I2 fix:用 DEFAULT_LOCATIONS 校验,消除硬编码
        if action.name == "go_to" and action.target in DEFAULT_LOCATIONS:
            ctx["world"].place(agent_id, action.target)
        await ctx["event_store"].append(
            agent_id=agent_id,
            kind="decision",
            content=f"{action.name} -> {action.target or '-'}",
        )
        await ctx["bus"].publish(
            Topic.AGENT_DECISION,
            {
                "topic": Topic.AGENT_DECISION.value,
                "agent_id": agent_id,
                "action": action.to_dict(),
                "ts": datetime.now().isoformat(),
            },
        )

    # C3 fix:对话触发 — pair 所有 C(n,2) 组合,3+ 人同位置全员有机会配对
    by_loc: dict[str, list[str]] = defaultdict(list)
    for aid in ctx["agents"]:
        by_loc[ctx["world"].location_of(aid)].append(aid)
    for loc, occupants in by_loc.items():
        if len(occupants) < 2:
            continue
        for a_id, b_id in combinations(occupants, 2):
            if ctx["trigger"].should_start(
                action_a_name=last_actions.get(a_id, "idle"),
                action_b_name=last_actions.get(b_id, "idle"),
                location=loc,
            ):
                await run_dialogue(a_id, b_id, loc)


async def run_dialogue(a_id: str, b_id: str, location: str):
    """生成一段两人对话,持久化到 event_store + 推总线 DIALOGUE_START / DIALOGUE_MESSAGE。"""
    assert ctx is not None
    a, b = ctx["agents"][a_id], ctx["agents"][b_id]
    did = await ctx["event_store"].create_dialogue(location)
    await ctx["bus"].publish(
        Topic.DIALOGUE_START,
        {
            "topic": Topic.DIALOGUE_START.value,
            "dialogue_id": did,
            "location": location,
            "participants": [a_id, b_id],
            "ts": datetime.now().isoformat(),
        },
    )
    try:
        msgs = await ctx["dialogue_gen"].generate(
            a_name=a.name,
            b_name=b.name,
            a_persona=a.persona,
            b_persona=b.persona,
            location=location,
        )
    except Exception:
        logger.exception("dialogue LLM generate failed")
        return
    for who, content in msgs:
        speaker_id = a_id if who == a.name else b_id
        await ctx["event_store"].add_dialogue_message(did, speaker_id, content)
        await ctx["bus"].publish(
            Topic.DIALOGUE_MESSAGE,
            {
                "topic": Topic.DIALOGUE_MESSAGE.value,
                "dialogue_id": did,
                "agent_id": speaker_id,
                "content": content,
                "ts": datetime.now().isoformat(),
            },
        )


@app.get("/api/agents")
async def list_agents():
    """返回 5 个 agent 当前状态"""
    assert ctx is not None
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "location": ctx["world"].location_of(p["id"]),
        }
        for p in ctx["personas"]
    ]


@app.websocket("/ws")
async def ws(ws: WebSocket):
    """WebSocket:订阅 AGENT_DECISION / DIALOGUE_MESSAGE,推送给客户端"""
    await ws.accept()
    ws_clients.append(ws)
    try:
        # 保持连接,客户端不发消息也行
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)


# 静态文件(static/)— Task 12 会填内容
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """返回 index.html(Task 12 会创建)"""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    # 占位文本(开发期间)
    return {"status": "town running", "hint": "Task 12 will provide index.html"}


if __name__ == "__main__":
    import uvicorn

    # 配 logging,让 print 之外的 logger.exception 也能落盘
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run("town.main:app", host="0.0.0.0", port=8000, reload=False)
