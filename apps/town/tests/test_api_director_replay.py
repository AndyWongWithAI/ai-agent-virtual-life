"""阶段 2 块 3(任务 T12):时间倒带端点测试。

GET /api/director/replay?ts=...&limit=...
- 返 ts 之后 N 条全局 events(asc)+ 每个 agent 的 LTM 摘要(只读)

覆盖:
- test_replay_returns_events_after_ts:events 数组只含 ts >= 入参的事件
- test_replay_includes_all_agents_summaries:summaries dict 含 5 个 agent_id
- test_invalid_ts_returns_400:ts=garbage → 400
- test_default_ts_is_one_hour_ago:不传 ts → 服务端默认 now-1h
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# 5 个 agent_id 与 personas.yaml 一致
AGENT_IDS = ["lisi", "wangwu", "zhangwei", "liuna", "chenlei"]


def _make_summary(agent_id: str, period_end: datetime, text: str):
    """构造 LTM Summary 的 stub(Summary 是 Pydantic,这里返回 SimpleNamespace 即可)。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        agent_id=agent_id,
        period_start=period_end - timedelta(hours=6),
        period_end=period_end,
        text=text,
    )


@pytest.fixture
def client_with_ctx():
    """最小 ctx + event_store.list_events / ltm.recent_summaries 都 mock 掉。

    - event_store.list_events:返回 desc 排序(与 EventStore 真实实现一致),路由应 reverse 成 asc。
    - ltm.recent_summaries:每个 agent 返回 3 条 stub Summary。
    """
    from town import main as town_main

    personas = [{"id": aid, "name": aid, "persona": "p"} for aid in AGENT_IDS]
    now = datetime.now()

    # 事件按 desc 排序(模拟 EventStore.list_events 行为)
    ev_desc = [
        MagicMock(
            ts=now - timedelta(minutes=10),
            agent_id="lisi",
            kind="decision",
            content="go_to 咖啡店",
        ),
        MagicMock(
            ts=now - timedelta(minutes=20),
            agent_id="wangwu",
            kind="decision",
            content="go_to 公园",
        ),
        MagicMock(
            ts=now - timedelta(minutes=30),
            agent_id="lisi",
            kind="dialogue",
            content="你好",
        ),
    ]

    event_store = MagicMock()
    event_store.list_events = AsyncMock(return_value=list(ev_desc))

    ltm = MagicMock()
    async def _recent(agent_id, n=3):
        return [
            _make_summary(agent_id, now - timedelta(hours=1), f"摘要1-{agent_id}"),
            _make_summary(agent_id, now - timedelta(hours=7), f"摘要2-{agent_id}"),
            _make_summary(agent_id, now - timedelta(hours=13), f"摘要3-{agent_id}"),
        ]
    ltm.recent_summaries = AsyncMock(side_effect=_recent)

    ctx = {
        "personas": personas,
        "agents": {aid: MagicMock() for aid in AGENT_IDS},
        "event_store": event_store,
        "ltm": ltm,
        "stm": MagicMock(),
        "world": MagicMock(),
        "bus": MagicMock(),
    }
    with patch("town.main.ctx", ctx):
        yield TestClient(town_main.app), ctx, now


def test_replay_returns_events_after_ts(client_with_ctx):
    """GET /api/director/replay?ts=... → events 数组只含 ts >= 入参,且按 asc 排。

    入参 ts 取 now - 25min,理论应只剩 2 条(10min/20min 那两条 desc),asc 排序后
    第一条是 20min 那条。30min 那条 ts < 25min,被滤掉。
    """
    client, ctx, now = client_with_ctx
    cut_ts = now - timedelta(minutes=25)
    resp = client.get(f"/api/director/replay?ts={cut_ts.isoformat()}")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code} {resp.text}"
    data = resp.json()

    # 顶层结构
    assert "events" in data
    assert "summaries" in data
    assert "ts" in data

    # 30min 那条 ts < cut_ts,应被过滤(路由要做 ts 过滤,不能全交给 EventStore)
    assert len(data["events"]) == 2, f"expected 2 events, got {len(data['events'])}"
    # asc 排序:第一条应是 -20min(更早),第二条 -10min
    assert data["events"][0]["content"] == "go_to 公园"
    assert data["events"][1]["content"] == "go_to 咖啡店"
    # 每条结构
    for ev in data["events"]:
        assert "ts" in ev
        assert "agent_id" in ev
        assert "kind" in ev
        assert "content" in ev

    # event_store.list_events 应被调,since 参数传入
    call_args = ctx["event_store"].list_events.call_args
    assert call_args is not None
    # since 参数位置/kwargs 两种都接受
    kwargs = call_args.kwargs
    args = call_args.args
    if kwargs:
        assert "since" in kwargs
        assert kwargs["since"] == cut_ts
    else:
        # 第二位置 since
        assert args[1] == cut_ts


def test_replay_includes_all_agents_summaries(client_with_ctx):
    """GET /api/director/replay → summaries dict 含 5 个 agent_id,每人 3 条。"""
    client, ctx, _now = client_with_ctx
    resp = client.get("/api/director/replay")
    assert resp.status_code == 200
    data = resp.json()
    summaries = data["summaries"]
    assert set(summaries.keys()) == set(AGENT_IDS), (
        f"expected 5 agents in summaries, got {sorted(summaries.keys())}"
    )
    for aid in AGENT_IDS:
        assert isinstance(summaries[aid], list)
        assert len(summaries[aid]) == 3
        for item in summaries[aid]:
            assert "ts" in item
            assert "text" in item
            # 默认 1h 内,只含 period_end < now 的摘要(mock 全是 -1h,都满足)


def test_invalid_ts_returns_400(client_with_ctx):
    """GET /api/director/replay?ts=garbage → 400(不抛 500)。"""
    client, _ctx, _now = client_with_ctx
    resp = client.get("/api/director/replay?ts=garbage")
    assert resp.status_code == 400
    body = resp.json()
    # 400 应有 detail 字段
    assert "detail" in body


def test_default_ts_is_one_hour_ago(client_with_ctx):
    """GET /api/director/replay 不传 ts → 服务端默认 now-1h,event_store.since ≈ now-1h。

    验证方法:check event_store.list_events 被调,since 与「调用前 30 秒」的差值 < 60s。
    """
    client, ctx, _now = client_with_ctx
    before = datetime.now() - timedelta(hours=1)
    resp = client.get("/api/director/replay")
    assert resp.status_code == 200
    after = datetime.now() - timedelta(hours=1)

    call_args = ctx["event_store"].list_events.call_args
    assert call_args is not None
    since = (call_args.kwargs.get("since") if call_args.kwargs else call_args.args[1])
    assert since is not None
    # since 应在 [before, after] 区间(默认 now-1h)
    assert before <= since <= after, (
        f"since={since} not in [{before}, {after}]"
    )
    # limit 默认 50
    limit = (call_args.kwargs.get("limit") if call_args.kwargs else (call_args.args[3] if len(call_args.args) > 3 else None))
    assert limit == 50
