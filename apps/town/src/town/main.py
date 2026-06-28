"""town FastAPI server:HTTP 路由 + WebSocket + 后台 tick 循环

- HTTP:
    GET /            -> index.html(前端在 Task 12 接入)
    GET /api/agents  -> 5 个 agent 当前状态(id/name/location)
- WebSocket:
    /ws              -> 推送 AGENT_DECISION / DIALOGUE_MESSAGE 事件
- 后台:
    tick_loop        -> 按 TickScheduler 节奏(白天 60s/夜间 300s)驱动每个 agent 决策
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_behavior_orchestrator import TickScheduler
from event_bus import Topic

from .bootstrap import bootstrap

app = FastAPI(title="AI 智能体虚拟小镇")
ctx: dict | None = None
tick_task: asyncio.Task | None = None
# WebSocket 客户端连接列表(广播用)
ws_clients: list[WebSocket] = []


@app.on_event("startup")
async def startup():
    global ctx, tick_task
    ctx = await bootstrap()
    # 启动 bus 监听循环(把 AGENT_DECISION / DIALOGUE_MESSAGE 广播给所有 ws 客户端)
    asyncio.create_task(ctx["bus"].run_forever())
    # 启动 tick 循环
    tick_task = asyncio.create_task(tick_loop())


@app.on_event("shutdown")
async def shutdown():
    if tick_task:
        tick_task.cancel()
    for ws in list(ws_clients):
        try:
            await ws.close()
        except Exception:
            pass


async def tick_loop():
    """按 TickScheduler 节奏驱动 run_tick,白天 60s/夜间 300s"""
    scheduler = TickScheduler()
    while True:
        interval = scheduler.interval_for()
        await asyncio.sleep(interval)
        try:
            await run_tick()
        except Exception as e:
            print(f"[tick_loop] error: {e}")


async def run_tick():
    """每个 tick:让 5 个 agent 各自 decide 一次,记录到 event_store + 推总线"""
    assert ctx is not None
    for agent_id, agent in ctx["agents"].items():
        snap = ctx["world"].snapshot(agent_id)
        action = await agent.decide(snap)
        # 简化:go_to 改变位置(只接受 5 个合法地点)
        if action.name == "go_to" and action.target in [
            "客厅", "厨房", "公园", "李四家", "王五家"
        ]:
            ctx["world"].place(agent_id, action.target)
        await ctx["event_store"].append(
            agent_id=agent_id,
            kind="decision",
            content=f"{action.name} -> {action.target or '-'}",
        )
        await ctx["bus"].publish(
            Topic.AGENT_DECISION,
            {
                "agent_id": agent_id,
                "action": action.to_dict(),
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

    uvicorn.run("town.main:app", host="0.0.0.0", port=8000, reload=False)
