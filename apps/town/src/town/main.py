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
from datetime import datetime, timedelta
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

from .bootstrap import bootstrap, bootstrap_reload
from .director import get_state, set_scene, set_speed
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
# 任务 #131(P0 暂停):模块级 flag,_paused 时 run_tick 整轮跳过(tick_decay + decide + event
# + bus publish 都不跑),状态原地冻结,指令照常排队,resume 后下次 tick 自动恢复。
_paused: bool = False


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
        # 阶段 2 块 2(任务 T11):倍速控制 — sleep 时长按 speed 缩放
        # speed=1.0 → 正常;4x → 间隔 1/4;0.5x → 间隔 2 倍
        speed = get_state().get("speed", 1.0)
        await asyncio.sleep(interval / speed)
        try:
            await run_tick()
        except Exception:
            logger.exception("tick_loop error")


async def run_tick():
    """每个 tick:让 5 个 agent 各自 decide 一次,记录到 event_store + 推总线"""
    # 任务 #131(P0):暂停守卫 — _paused=True 时整轮跳过,world/e/bus/state 全静默。
    # 放在 inc metrics 之前,免得 pause 期也涨 counters(Grafana 看到的「业务量=0」就是真实信号)。
    if _paused:
        return
    assert ctx is not None
    m.town_tick_total.inc()  # Phase 7:tick 计数
    # 任务 #113:tick 开头对所有 agent 应用时间衰减(饿/累/孤独 涨,快乐略降)
    ctx["world"].tick_decay()
    # 任务 T10(阶段 2 块 1):tick 开头读 director.last_scene,塞到所有 agent 的
    # 当前 tick 决策上下文。复用 user_command 通道(decision.py 已支持),避免扩
    # agent.decide 签名。scene 每次 tick 注入直到 clear_scene()(设计清单 §2.1)。
    director_scene = get_state().get("last_scene")
    last_actions: dict[str, str] = {}
    for agent_id, agent in ctx["agents"].items():
        snap = ctx["world"].snapshot(agent_id)
        # V5:从命令队列 pop 一条指令(若有),注入决策上下文
        user_cmd = None
        if commands.get(agent_id):
            user_cmd = commands[agent_id].pop(0)
            logger.info("V5: agent %s 收到指令: %s", agent_id, user_cmd)
        # 任务 T10:若 director 注入场景,把「导演场景:{content}」追加到 user_cmd,
        # 复用 decision.py 的 user_command 通道(不需要改 L2 agent 签名)。
        if director_scene and user_cmd is None:
            user_cmd = f"导演场景:{director_scene['content']}"
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
        # 阶段 3:locations 来源已切到 YAML,运行时校验用 ctx["locations"] 的 name 集合
        _valid_locs = {loc["name"] for loc in ctx.get("locations", []) if isinstance(loc, dict)}
        if action.name == "go_to" and (action.target in DEFAULT_LOCATIONS or action.target in _valid_locs):
            ctx["world"].place(agent_id, action.target)
        # 任务 #113:apply_action 根据动作名调整 4 维状态(eat 饱+ sleep 累- 等)
        # 任务 #127(B1):传 Action 对象(含 target),World 记最新动作给前端近况面板用
        try:
            ctx["world"].apply_action(agent_id, action)
        except Exception:
            logger.exception("world.apply_action failed for %s/%s", agent_id, action.name)
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
    """返回 5 个 agent 当前状态(任务 #127/B1:扩展为含实时状态给「近况」面板)。

    字段:
    - id / name / location(原有)
    - current_action:{name, target} 最新一次 apply_action 的动作+目标
    - status_bar:{饱/累/孤独/快乐: int} 4 维状态(中文 label)
    """
    assert ctx is not None
    out = []
    for p in ctx["personas"]:
        aid = p["id"]
        action_name, target = ctx["world"].latest_action_of(aid)
        status_en = ctx["world"].status_of(aid)
        out.append({
            "id": aid,
            "name": p["name"],
            "location": ctx["world"].location_of(aid),
            "current_action": {"name": action_name, "target": target},
            "status_bar": {LABELS_ZH[k]: v for k, v in status_en.items() if k in LABELS_ZH},
        })
    return out


class CommandRequest(BaseModel):
    agent_id: str
    command: str


# --- 阶段 3 (REQ-7cfc9696):配置 + locations 端点 ---

@app.get("/api/locations")
async def list_locations():
    """返回当前生效的地点列表(从 YAML 加载)。

    阶段 3:T4 前端 canvas.js 用它派生 LOCATIONS,代替硬编码。
    每条形如:{name, x, y, color, adjacency: [name, ...]}
    """
    assert ctx is not None
    return {"locations": ctx.get("locations", [])}


@app.get("/api/config-status")
async def config_status():
    """阶段 3 §4.4:报告当前配置源 + 错误列表,供前端 toast 使用。

    Returns:
        {"source": "custom"|"base", "errors": [...]}
    """
    assert ctx is not None
    return {
        "source": ctx.get("config_source", "base"),
        "errors": ctx.get("config_errors", []),
    }


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


@app.get("/api/events")
async def list_events(limit: int = 30):
    """任务 #125(Bug4):最近 N 条全局事件,按时间正序输出(前端 init 拉一次)。

    默认 limit=30,前端 events 面板初始化时调用,避免刷新后从 0 开始。
    时序升序返回,前端 append 后形成"上=最早 下=最近",新 WS 事件 prepend 即对齐。
    """
    assert ctx is not None
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit 必须在 1-200")
    # 拿到 desc 排序后反转,得到 asc
    events = await ctx["event_store"].list_events(limit=limit)
    events_asc = list(reversed(events))
    return [
        {
            "ts": e.ts.isoformat(),
            "agent_id": e.agent_id,
            "kind": e.kind,
            "content": e.content,
        }
        for e in events_asc
    ]


# 注:任务 #127(B)之后,/api/memory-summaries 端点移除 ——
# 6h LTM 反思仅作为 LLM decision prompt 的 recent_summary 注入,
# 不再直接展示给用户。用户面板走实时状态(/api/agents + 4 维 + 当前动作)。


# --- 任务 #131(P0):暂停/启动端点 + 控制广播 ---

async def _broadcast_control(kind: str) -> None:
    """任务 #131:把暂停/恢复事件直接 broadcast 给所有 WS 客户端。

    绕过 event_bus(避免增加 topic 类型)— town 控制信号是 town 自治事件,
    不属于 L1/L2 业务通路。失败客户端自动从 ws_clients 摘除(同 _ws_broadcast)。

    阶段 2 块 2(任务 T11):扩 payload 含 speed,前端可在倍速切换时立即刷新按钮高亮。
    """
    state = get_state()
    payload = {
        "topic": "town.control",
        "kind": kind,
        "paused": state.get("paused", False),
        "speed": state.get("speed", 1.0),
    }
    dead: list[WebSocket] = []
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


@app.post("/api/pause")
async def pause_town():
    """任务 #131(P0 暂停):切到暂停态。已暂停返 200 不重复触发 broadcast。"""
    global _paused
    if not _paused:
        _paused = True
        logger.info("town paused")
        await _broadcast_control("paused")
    return {"paused": True}


# --- 阶段 3 (REQ-7cfc9696) 收尾:重启生效端点(任务 T9) ---


@app.post("/api/restart")
async def restart_town():
    """阶段 3 收尾:重新加载 YAML 配置 + 重建 world/agents。

    复用 LLM / EventBus / Postgres / Reflector(避免重连花销,不踢 WS 客户端,
    6h 反思状态连续)。返回新的 personas / locations 计数让前端 toast 显示。

    流程:
      1. 调 bootstrap_reload(prev_ctx=ctx) 复用基础设施
      2. 替换全局 ctx
      3. 广播 control(kind=restarted)让前端刷新页面
      4. 返 {status, source, personas_count, locations_count}

    失败(配置损坏):抛 500,前端显示 toast。
    """
    global ctx
    # 暂停状态下不允许重启(避免状态不一致)— 先恢复
    # 注:这里不强制,允许用户「暂停+重启」组合
    try:
        prev = ctx
        new_ctx = await bootstrap_reload(prev_ctx=prev)
    except Exception:
        logger.exception("restart_town: bootstrap_reload failed")
        raise HTTPException(
            status_code=500,
            detail="配置损坏,重启失败。请检查 YAML 后再试。",
        )

    ctx = new_ctx  # 替换全局 ctx(tick_loop 下次循环自然读新 ctx)

    # 广播 restart 事件给所有 WS(前端收到后刷新页面)
    payload = {
        "topic": "town.control",
        "kind": "restarted",
        "source": new_ctx.get("config_source", "base"),
        "personas_count": len(new_ctx.get("personas", [])),
        "locations_count": len(new_ctx.get("locations", [])),
    }
    dead: list[WebSocket] = []
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

    logger.info(
        "town restarted: source=%s, personas=%d, locations=%d",
        payload["source"], payload["personas_count"], payload["locations_count"],
    )
    return {
        "status": "restarted",
        "source": payload["source"],
        "personas_count": payload["personas_count"],
        "locations_count": payload["locations_count"],
    }


@app.post("/api/resume")
async def resume_town():
    """任务 #131(P0 暂停):从暂停恢复。tick_loop 下一轮自然醒,run_tick 自动跑。"""
    global _paused
    if _paused:
        _paused = False
        logger.info("town resumed")
        await _broadcast_control("resumed")
    return {"paused": False}


@app.get("/api/status")
async def town_status():
    """任务 #131(P0 暂停):前端 init / WS 重连时拉当前运行态。"""
    return {
        "paused": _paused,
        "agents": len(ctx["agents"]) if ctx else 0,
        "ts": datetime.now().isoformat(),
    }


# --- 阶段 2 块 1(任务 T10):导演场景注入 ---


@app.post("/api/director/scene")
async def inject_scene(body: dict):
    """导演场景注入:下个 tick 把 {kind, content} 写到所有 agent 的 LLM prompt。

    body: {"kind": str, "content": str}
    - 返回 {ok, state};失败 400(content 不能空)。
    - 同时 publish Topic.DIRECTOR_SCENE 让前端 WS 能看到注入事件。
    """
    import time
    assert ctx is not None
    kind = body.get("kind", "custom")
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content 不能空")
    set_scene(kind, content)
    # publish 到 bus(bus 可能为 None 测试桩)
    bus = ctx.get("bus")
    if bus is not None:
        await bus.publish(
            Topic.DIRECTOR_SCENE,
            {
                "topic": Topic.DIRECTOR_SCENE.value,
                "kind": kind,
                "content": content,
                "ts": time.time(),
            },
        )
    return {"ok": True, "state": get_state()}


@app.get("/api/director/state")
async def director_state():
    """导演面板状态查询:paused / speed / last_scene。"""
    return get_state()


# --- 阶段 2 块 3(任务 T12):时间倒带端点 ---


@app.get("/api/director/replay")
async def director_replay(ts: str | None = None, limit: int = 50):
    """导演时间倒带:按 ts 拉 events + 每个 agent 的 LTM 摘要(只读回放,不改 SSOT)。

    Query params:
    - ts: ISO 格式时间字符串,默认 now-1h。解析失败 → 400。
    - limit: 最多返 N 条 events,默认 50,上限 200(超出 clamp)。

    Returns:
        {
            "ts": 入参 ts(ISO),
            "events": [{"ts", "agent_id", "kind", "content"} ...] 按 asc 排序,
            "summaries": {agent_id: [{"ts", "text"} ...] ...}
        }

    设计要点(决策清单 §2.3):
    - 只读:不调 event_store.append / ltm.add_summary / world.place,SSOT 不动。
    - ts 早于最早事件 → 200 + events=[](list_events 自然返空)。
    - 每个 agent 各取最近 3 条 LTM(LTM 接口只支持按 N,不支持 since 过滤)。
    """
    assert ctx is not None
    # 1. ts 解析
    if ts is None:
        cut_ts = datetime.now() - timedelta(hours=1)
    else:
        try:
            cut_ts = datetime.fromisoformat(ts)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"ts 解析失败:{ts!r} 不是 ISO 格式",
            )
    # 2. limit clamp(1-200)
    limit = max(1, min(int(limit), 200))

    # 3. 拉 events(asc):EventStore.list_events 返 desc,路由反转 + 过滤 ts
    raw_events = await ctx["event_store"].list_events(limit=limit, since=cut_ts)
    events = [
        {
            "ts": e.ts.isoformat(),
            "agent_id": e.agent_id,
            "kind": e.kind,
            "content": e.content,
        }
        for e in reversed(raw_events)
        if e.ts >= cut_ts  # 二次过滤:list_events since 是 >=,但若 mock 不一致时兜底
    ]

    # 4. 每个 agent 拉 LTM 摘要(n=3)
    summaries: dict[str, list[dict]] = {}
    for aid in ctx["agents"]:
        items = await ctx["ltm"].recent_summaries(aid, n=3)
        summaries[aid] = [
            {"ts": s.period_end.isoformat(), "text": s.text} for s in items
        ]

    return {"ts": cut_ts.isoformat(), "events": events, "summaries": summaries}


# --- 阶段 2 块 2(任务 T11):倍速控制端点 ---


class DirectorControlRequest(BaseModel):
    """POST /api/director/control body schema。

    字段:
    - action: "speed" | "pause" | "resume"(pause/resume 仍兼容 /api/pause 与 /api/resume)
    - factor: float — speed action 必填,值必须在 (0.5, 1.0, 2.0, 4.0)
    """

    action: str
    factor: float | None = None


@app.post("/api/director/control")
async def director_control(body: DirectorControlRequest):
    """导演面板控制端点(阶段 2 块 2):倍速 / 暂停 / 恢复 统一入口。

    body:
    - {action: "speed", factor: 0.5|1.0|2.0|4.0} — 切倍速
    - {action: "pause"} — 等价于 POST /api/pause(保留旧端点向后兼容)
    - {action: "resume"} — 等价于 POST /api/resume

    返回:{ok, state} 让前端立即拿到最新 director state。
    失败:factor 越界 → 422(Pydantic 验证);action 非法 → 400。
    """
    action = body.action
    if action == "speed":
        if body.factor is None:
            raise HTTPException(status_code=400, detail="factor 必填")
        try:
            set_speed(body.factor)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        await _broadcast_control("speed")
        return {"ok": True, "state": get_state()}
    if action == "pause":
        # 复用 /api/pause 逻辑(避免双源真相)
        return await pause_town()
    if action == "resume":
        return await resume_town()
    raise HTTPException(status_code=400, detail=f"未知 action: {action}")


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
