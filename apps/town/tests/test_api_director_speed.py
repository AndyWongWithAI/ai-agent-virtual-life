"""阶段 2 块 2:倍速控制 API + run_tick sleep 缩放测试。

4 测试:
1. 默认 speed=1.0
2. POST {action: speed, factor: 2.0} → state.speed=2.0
3. invalid factor → 400/422
4. tick_loop 内 sleep 时长 = interval / speed(mock asyncio.sleep)
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_director():
    """最小 ctx + reset director state。"""
    from town import main as town_main
    from town import director

    director.set_paused(False)
    director.set_speed(1.0)

    personas = [{"id": "lisi", "name": "李四", "persona": "32岁程序员"}]
    ctx = {
        "personas": personas,
        "agents": {"lisi": MagicMock()},
        "event_store": MagicMock(),
        "ltm": MagicMock(),
        "stm": MagicMock(),
        "world": MagicMock(),
        "bus": MagicMock(),
        "trigger": MagicMock(),
        "dialogue_gen": MagicMock(),
        "reflector": MagicMock(),
    }
    with patch("town.main.ctx", ctx):
        yield TestClient(town_main.app)


def test_speed_default_is_1(client_with_director):
    """GET /api/director/state 默认 speed=1.0。"""
    resp = client_with_director.get("/api/director/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["speed"] == 1.0
    assert data["paused"] is False


def test_set_speed_2x(client_with_director):
    """POST /api/director/control {action: speed, factor: 2.0} → state.speed=2.0。"""
    resp = client_with_director.post(
        "/api/director/control",
        json={"action": "speed", "factor": 2.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"]["speed"] == 2.0

    # GET state 也应同步
    state = client_with_director.get("/api/director/state").json()
    assert state["speed"] == 2.0


def test_invalid_factor_rejected(client_with_director):
    """POST factor=99 → 400/422。"""
    resp = client_with_director.post(
        "/api/director/control",
        json={"action": "speed", "factor": 99},
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_run_tick_uses_speed_factor():
    """tick_loop 内 asyncio.sleep 时长 = interval / speed。

    4x 倍速 → sleep(60/4=15);0.5x 倍速 → sleep(60/0.5=120)。

    实现方式:把 asyncio.sleep 替换为一个把参数 append 到 list 的 stub,
    然后用 pytest.raises 让 run_tick 内部 raise SystemExit 中断 while True。
    实际更可靠:把 sleep 替换为「第一次 OK,第二次 raise」,这样 tick_loop 一定
    在第二次 sleep 时跳出。
    """
    from town import main as town_main
    from town import director

    ctx = {
        "personas": [],
        "agents": {},
        "event_store": MagicMock(),
        "ltm": MagicMock(),
        "stm": MagicMock(),
        "world": MagicMock(),
        "bus": MagicMock(),
        "trigger": MagicMock(),
        "dialogue_gen": MagicMock(),
        "reflector": MagicMock(),
    }

    # 共享 mock sleep:记录所有参数,第二次抛错跳出
    sleep_args_log: list[float] = []
    call_count = {"n": 0}

    async def mock_sleep(secs):
        sleep_args_log.append(secs)
        call_count["n"] += 1
        if call_count["n"] >= 2:
            # 用 BaseException 绕过 except Exception 捕获
            raise BaseException("STOP_TICK_LOOP")

    # 4x 倍速
    director.set_speed(4.0)
    sleep_args_log.clear()
    call_count["n"] = 0

    with patch("town.main.ctx", ctx):
        with patch("town.main.asyncio.sleep", new=mock_sleep):
            with patch("town.main.run_tick", new=AsyncMock()):
                with pytest.raises(BaseException, match="STOP_TICK_LOOP"):
                    await town_main.tick_loop()
    # sleep_args_log 应为 [3.0, 15.0](warmup + interval/4)
    assert sleep_args_log == [3.0, 15.0], f"4x: expected [3, 15], got {sleep_args_log}"

    # 0.5x 倍速
    director.set_speed(0.5)
    sleep_args_log.clear()
    call_count["n"] = 0

    with patch("town.main.ctx", ctx):
        with patch("town.main.asyncio.sleep", new=mock_sleep):
            with patch("town.main.run_tick", new=AsyncMock()):
                with pytest.raises(BaseException, match="STOP_TICK_LOOP"):
                    await town_main.tick_loop()
    # sleep_args_log 应为 [3.0, 120.0]
    assert sleep_args_log == [3.0, 120.0], f"0.5x: expected [3, 120], got {sleep_args_log}"