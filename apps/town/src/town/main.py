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

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_behavior_orchestrator import TickScheduler
from event_bus import Topic
from memory_reflection import Event
from virtual_world_engine import DEFAULT_LOCATIONS, LABELS_ZH, STATUS_KEYS

from .bootstrap import bootstrap
from . import metrics as m

# I17 fix(P3 #109):logging.basicConfig 必须在模块顶部(import 时即生效),
# 否则 uvicorn 启动走 `app` 入口时,__main__ 块不会执行,所有 logger.exception
# 只进 stderr 不带 timestamp/level,生产排查时 docker logs 显示为"无头案"。
# 配置为:StreamHandler 到 stderr + 时间戳 + level + module name,符合 12-factor
# 日志规范(docker/k8s 收集 stderr)。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="AI 智能体虚拟小镇")
ctx: dict | None = None
tick_task: asyncio.Task | None = None
bus_task: asyncio.Task | None = None  # C1 fix:保留引用,避免 GC
# WebSocket 客户端连接列表(广播用)
ws_clients: list[WebSocket] = []
# V5:用户指令队列,agent_id -> [cmd1, cmd2, ...]
# 模块级 dict(town 单进程 OK;多进程会不一致,见 brief 风险)
commands: dict[str, list[str]] = defaultdict(list)


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
    ctx["bus"].subscribe(Topic.MEMORY_REFLECT, _ws_broadcast)

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
    m.town_tick_total.inc()  # Phase 7:tick 计数
    last_actions: dict[str, str] = {}
    for agent_id, agent in ctx["agents"].items():
        snap = ctx["world"].snapshot(agent_id)
        # V5:从命令队列 pop 一条指令(若有),注入决策上下文
        user_cmd = None
        if commands.get(agent_id):
            user_cmd = commands[agent_id].pop(0)
            logger.info("V5: agent %s 收到指令: %s", agent_id, user_cmd)
        # I18 fix(P3 #109):LLM 失败(空响应/截断无法修复) → fallback 到 idle
        # 而不是 raise,否则该 agent tick 整轮失败,UI 上"卡死"。
        # logger.warning 留下证据,既可观测又不阻塞其他 agent。
        try:
            action = await agent.decide(snap, user_command=user_cmd)
        except Exception as e:
            logger.warning(
                "P3 fallback: agent %s decide failed (%s), using idle",
                agent_id, e,
            )
            m.town_decide_fail_total.labels(agent_id=agent_id).inc()  # Phase 7
            from agent_runtime.actions import Action
            action = Action(name="idle", target=None, params={})
        last_actions[agent_id] = action.name
        m.town_decisions_total.labels(action=action.name).inc()  # Phase 7
        # I2 fix:用 DEFAULT_LOCATIONS 校验,消除硬编码
        if action.name == "go_to" and action.target in DEFAULT_LOCATIONS:
            ctx["world"].place(agent_id, action.target)
        await ctx["event_store"].append(
            agent_id=agent_id,
            kind="decision",
            content=f"{action.name} -> {action.target or '-'}",
        )
        # V6:decision 同时 append 到 STM(Reflector 需要从这里读事件)
        try:
            await ctx["stm"].add(
                Event(
                    agent_id=agent_id,
                    kind="decision",
                    content=f"{action.name} -> {action.target or '-'}",
                )
            )
        except Exception:
            logger.exception("stm.add decision failed for %s", agent_id)
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

    # V6:tick 末尾给每个 agent 跑一次反思检查,触发条件由 Reflector 内部判断(>6h)
    # 失败必须 try/except,不能 crash tick_loop(其他 agent 跟着停摆)
    for agent_id in ctx["agents"]:
        try:
            result = await ctx["reflector"].maybe_reflect(agent_id, bus=ctx["bus"])
            if result is not None:
                m.town_reflects_total.labels(agent_id=agent_id).inc()  # Phase 7
        except Exception:
            m.town_reflect_fails_total.inc()  # Phase 7
            logger.exception("reflector.maybe_reflect failed for %s", agent_id)


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
        m.town_dialogue_fails_total.inc()  # Phase 7
        logger.exception("dialogue LLM generate failed")
        return
    m.town_dialogues_total.inc()  # Phase 7
    for who, content in msgs:
        speaker_id = a_id if who == a.name else b_id
        await ctx["event_store"].add_dialogue_message(did, speaker_id, content)
        # V6:dialogue message 同时 append 到 STM(Reflector 需要 dialogue 事件)
        try:
            await ctx["stm"].add(
                Event(
                    agent_id=speaker_id,
                    kind="dialogue",
                    content=content,
                )
            )
        except Exception:
            logger.exception("stm.add dialogue failed for %s", speaker_id)
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


class CommandRequest(BaseModel):
    agent_id: str
    command: str


@app.post("/api/command")
async def post_command(req: CommandRequest):
    """用户给单个 agent 下指令。指令排队,下次 tick 由 prompt 注入。

    Returns: {"status": "queued", "agent_id": "...", "command": "...", "queue_len": N}
    """
    assert ctx is not None
    # 校验 agent 存在
    if req.agent_id not in ctx["agents"]:
        raise HTTPException(status_code=404, detail=f"unknown agent: {req.agent_id}")
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="command 不能为空")
    commands[req.agent_id].append(req.command.strip())
    # Phase 7:上报 command 队列长度
    m.town_command_queue_size.labels(agent_id=req.agent_id).set(
        len(commands[req.agent_id])
    )
    return {
        "status": "queued",
        "agent_id": req.agent_id,
        "command": req.command.strip(),
        "queue_len": len(commands[req.agent_id]),
    }


@app.get("/api/agents/{agent_id}/commands")
async def get_commands(agent_id: str):
    """查看某 agent 当前 pending 指令队列(不消费,只读)"""
    if agent_id not in (ctx["agents"] if ctx else {}):
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    return {
        "agent_id": agent_id,
        "pending": list(commands.get(agent_id, [])),
    }


@app.get("/api/agents/{agent_id}/status")
async def agent_status(agent_id: str):
    """单个 agent 详细状态

    用于 V2 前端点击智能体后弹出的状态面板:
    - status_bar(结构化 dict,中文 label 为 key)+ 当前位置
    - 近期 LTM 反思摘要
    - 近期 STM 事件

    任务 #114:status_bar 内部 key 是英文(STATUS_KEYS),输出时按 LABELS_ZH
    映射成中文 label,前端不变(继续用 Object.entries 取 label)。
    status_keys 字段额外返回英文 enum 列表,供 i18n 扩展。
    """
    assert ctx is not None
    persona = next((p for p in ctx["personas"] if p["id"] == agent_id), None)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    snap = ctx["world"].snapshot(agent_id)
    summaries = await ctx["ltm"].recent_summaries(agent_id, n=3)
    recent_events = await ctx["stm"].recent(agent_id, n=10)
    # 内部英文 → 中文 label
    status_bar_zh = {LABELS_ZH[k]: v for k, v in snap["status_bar"].items() if k in LABELS_ZH}
    return {
        "id": agent_id,
        "name": persona["name"],
        "persona": persona["persona"],
        "location": ctx["world"].location_of(agent_id),
        "status_bar": status_bar_zh,
        "status_keys": list(STATUS_KEYS),  # 供 i18n 扩展的英文 enum
        "recent_summaries": [
            {"ts": s.period_end.isoformat(), "text": s.text} for s in summaries
        ],
        "recent_events": [
            {"ts": e.ts.isoformat(), "kind": e.kind, "content": e.content}
            for e in recent_events
        ],
    }


@app.websocket("/ws")
async def ws(ws: WebSocket):
    """WebSocket:订阅 AGENT_DECISION / DIALOGUE_MESSAGE,推送给客户端"""
    await ws.accept()
    ws_clients.append(ws)
    m.town_ws_clients.set(len(ws_clients))  # Phase 7
    try:
        # 保持连接,客户端不发消息也行
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)
        m.town_ws_clients.set(len(ws_clients))  # Phase 7


# 静态文件(static/)— Task 12 会填内容
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/health")
async def health():
    """健康检查(deploy workflow 复用)

    不依赖 Redis/Postgres/LLM,只读 ctx(可能为 None 当 bootstrap 失败时)。
    永远返回 200,只要进程在跑。
    """
    return {
        "status": "ok",
        "agents": len(ctx["agents"]) if ctx else 0,
        "ts": datetime.now().isoformat(),
    }


# Phase 7 运维第 1 步:Prometheus 抓取端点
# 暴露 Counter/Gauge 业务指标,供 Prometheus server 定时 scrape
# 不带鉴权(只暴露指标不含敏感数据,nginx /metrics 限制内网或 VPN 访问)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus 抓取端点(P7-1)

    格式:prometheus text format
    Counter/Gauge 来自 town.metrics 模块
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
