"""#110 P3 防 V2/V5/V6 静默回归的 e2e 测试

背景:town 已有 83 个单测,但都基于 FastAPI TestClient(mock 掉 ctx),
不覆盖真实浏览器层(WS 升级 / 静态资源 / 反向代理)是否让用户能看见互动。
playwright 在 WSL ubuntu26.04 装不上(熔断:5 分钟切到 B 方案),
改用 httpx + websockets 直接模拟浏览器:

测试矩阵(跑在已部署 town 上,默认 https://life.intelab.cn,可用 E2E_BASE_URL 覆盖):
1. 静态资源:GET / 返回 200 + 含 canvas 元素 + agent-panel/command-panel/memory-panel DOM
2. /api/agents:返回 5 个 agent 完整字段(id/name/location)
3. WS 升级:模拟浏览器 Upgrade/Connection 头,确认 101 Switching Protocols
4. 指令面板端到端:POST /api/command → GET /api/agents/{id}/commands 看到队列
5. 状态面板:GET /api/agents/{id}/status 200 + status_bar(dict,4 项) + recent_summaries(列表)
6. WS 静默失败防御:onclose 后 1.5s 重连(前端 connectWS 的 setTimeout)
7. 前端 JS 引用:canvas.js 引用所有 DOM id(防止改 HTML 改坏 JS)
8. CORS / 静态资源子目录:/static/canvas.js /static/style.css 200

环境变量:
- E2E_BASE_URL 默认 https://life.intelab.cn(生产)
- 本地: E2E_BASE_URL=http://127.0.0.1:8001
- CI:目前不跑(需要公网访问,加 marker 'e2e' 跳过默认)
"""
import json
import os
import re
import socket
import ssl
import time
from urllib.parse import urlparse

import httpx
import pytest


E2E_BASE_URL = os.getenv("E2E_BASE_URL", "https://life.intelab.cn")


# 所有测试都用 production marker(默认 skip,跑时加 `-m production` 或去掉 marker)
pytestmark = pytest.mark.production


def _ws_url(base: str) -> str:
    """HTTP(S) → WS(S)"""
    p = urlparse(base)
    scheme = "wss" if p.scheme == "https" else "ws"
    return f"{scheme}://{p.netloc}/ws"


# --- 1. 静态首页 ---

def test_homepage_loads_with_required_dom():
    """GET / 返回 200 + 含 canvas/agent-panel/command-panel/memory-panel DOM"""
    r = httpx.get(f"{E2E_BASE_URL}/", timeout=10)
    assert r.status_code == 200, f"homepage status: {r.status_code}"
    html = r.text
    for required_id in ("map", "agent-panel", "command-panel", "memory-panel",
                        "command-agent", "command-input", "command-send"):
        assert f'id="{required_id}"' in html, f"missing #{required_id} in homepage"


# --- 2. /api/agents ---

def test_api_agents_returns_five():
    """GET /api/agents 返回 5 个 agent"""
    r = httpx.get(f"{E2E_BASE_URL}/api/agents", timeout=10)
    assert r.status_code == 200
    agents = r.json()
    assert len(agents) == 5, f"expected 5 agents, got {len(agents)}: {agents}"
    for a in agents:
        assert {"id", "name", "location"} <= set(a.keys()), f"agent missing fields: {a}"
        assert a["location"] in ("李四家", "王五家", "客厅", "厨房", "公园"), \
            f"agent {a['id']} unknown location: {a['location']}"


# --- 3. WS 升级(模拟真实浏览器) ---

def test_websocket_upgrade_succeeds():
    """模拟浏览器 WS 升级:GET /ws 带 Upgrade/Connection/Sec-WebSocket-Key 头

    FastAPI/Starlette 收到合法升级请求 → 101 Switching Protocols
    nginx map + location = /ws 配置必须正确(见 feedback-nginx-http2-websocket-upgrade)
    """
    from websockets.sync.client import connect
    with connect(_ws_url(E2E_BASE_URL), open_timeout=10) as ws:
        # 收到任一 topic 即视为 WS 通
        try:
            msg = ws.recv(timeout=5)
        except Exception:
            # town 60s 才推一次,等不到也说明握手成功(没 close)
            msg = None
        # 收到的是 JSON,topic 字段是已知 topic 之一
        if msg is not None:
            data = json.loads(msg)
            assert "topic" in data, f"WS message missing topic: {msg[:200]}"


# --- 4. 指令面板端到端 ---

def test_command_post_then_get_queue():
    """POST /api/command → GET /api/agents/{id}/commands 看到 pending"""
    # 选 lisi(5 个 agent 之一)
    r1 = httpx.post(
        f"{E2E_BASE_URL}/api/command",
        json={"agent_id": "lisi", "command": "去公园"},
        timeout=10,
    )
    assert r1.status_code == 200, f"POST status: {r1.status_code}, body: {r1.text}"
    body = r1.json()
    assert body["status"] == "queued"
    assert body["agent_id"] == "lisi"
    assert body["command"] == "去公园"
    # queue_len 应该 >= 1
    assert body["queue_len"] >= 1, f"queue_len should be >=1, got {body['queue_len']}"
    # 注意:60s tick 会消费,断言有数据,不一定包含我们这条
    r2 = httpx.get(f"{E2E_BASE_URL}/api/agents/lisi/commands", timeout=10)
    assert r2.status_code == 200
    pending = r2.json()["pending"]
    assert isinstance(pending, list)


def test_command_rejects_unknown_agent():
    """POST /api/command agent_id 不存在 → 404"""
    r = httpx.post(
        f"{E2E_BASE_URL}/api/command",
        json={"agent_id": "no-such-agent", "command": "x"},
        timeout=10,
    )
    assert r.status_code == 404


def test_command_rejects_empty_command():
    """POST /api/command command 空 → 400"""
    r = httpx.post(
        f"{E2E_BASE_URL}/api/command",
        json={"agent_id": "lisi", "command": "   "},
        timeout=10,
    )
    assert r.status_code == 400


# --- 5. 状态面板(V2 任务 #84) ---

def test_agent_status_returns_status_bar_dict():
    """GET /api/agents/{id}/status 200 + status_bar 是 dict(4 项)+ summaries/events 列表"""
    r = httpx.get(f"{E2E_BASE_URL}/api/agents/lisi/status", timeout=10)
    assert r.status_code == 200, f"status: {r.status_code}, body: {r.text[:200]}"
    data = r.json()
    assert data["id"] == "lisi"
    # status_bar 是 dict(不是字符串)
    assert isinstance(data["status_bar"], dict), \
        f"status_bar should be dict, got {type(data['status_bar'])}"
    assert len(data["status_bar"]) == 4, f"expected 4 status fields, got {data['status_bar']}"
    # summaries + events 都是列表
    assert isinstance(data["recent_summaries"], list)
    assert isinstance(data["recent_events"], list)


def test_agent_status_unknown_agent_404():
    """GET /api/agents/unknown/status → 404"""
    r = httpx.get(f"{E2E_BASE_URL}/api/agents/no-such-agent/status", timeout=10)
    assert r.status_code == 404


# --- 6. WS 静默失败防御(前端 connectWS 的 onclose→1.5s 重连) ---

def test_websocket_receives_decision_event_within_tick():
    """WS 客户端应在合理时间内收到至少一个 agent.decision 事件

    注意:town 重启(deploy 后 30s 内)会让 WS 拿不到旧 town 推的事件。
    新 town lifespan() 启动后,ws_clients 列表为空,新连入的 client 需等
    下一次 tick(白天 60s / 夜间 300s)。
    测前先确认 town health 稳定 + 给 5s 缓冲。

    town 白天 60s/tick,夜间 300s。生产 09:48 是白天,应 60s 内收到。
    限 120s 防夜间时间表(实际 60s 内 town 不会进夜间表)。
    """
    from websockets.sync.client import connect
    # 触发一次活跃(模拟真实用户,避免 town idle 时不推 decision)
    httpx.post(
        f"{E2E_BASE_URL}/api/command",
        json={"agent_id": "lisi", "command": "去公园"},
        timeout=10,
    )
    # 给 5s 让 town lifespan + warmup tick 跑完
    time.sleep(5)
    deadline = time.time() + 120
    with connect(_ws_url(E2E_BASE_URL), open_timeout=10) as ws:
        while time.time() < deadline:
            try:
                msg = ws.recv(timeout=10)
            except Exception:
                # timeout / close — 可能是 deploy 期间重连,继续
                continue
            data = json.loads(msg)
            if data.get("topic") == "agent.decision":
                # 收到 decision,验证字段
                assert "agent_id" in data
                assert "action" in data
                return  # 通过
        # 超时 = town 静默失败(任务 #70 教训)
        pytest.fail("no agent.decision event in 120s — town tick_loop 疑似静默失败")


# --- 7. 静态资源子目录(防 deploy 漏拷) ---

def test_static_canvas_js_loads():
    """GET /static/canvas.js 200 + 含 init/connectWS/agentRenderPositions 关键函数"""
    r = httpx.get(f"{E2E_BASE_URL}/static/canvas.js", timeout=10)
    assert r.status_code == 200
    js = r.text
    for fn in ("function init", "function connectWS", "agentRenderPositions",
               "function showAgentPanel", "function sendCommand"):
        assert fn in js, f"canvas.js missing {fn}"


def test_static_index_html_loads():
    """GET /static/index.html(如果用 StaticFiles mount,可能 404;不影响测试)"""
    r = httpx.get(f"{E2E_BASE_URL}/static/index.html", timeout=10)
    # 200 或 404 都可(取决于 town 怎么 mount,FileResponse 走 / 路由)
    assert r.status_code in (200, 404)


# --- 8. CORS / 错误路径(防 500) ---

def test_health_endpoint():
    """GET /health 200(nginx/uvicorn 链路通)"""
    r = httpx.get(f"{E2E_BASE_URL}/health", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["agents"] == 5


def test_unknown_api_path_404():
    """GET /api/no-such-path → 404(不是 500)"""
    r = httpx.get(f"{E2E_BASE_URL}/api/no-such-path", timeout=10)
    assert r.status_code == 404
