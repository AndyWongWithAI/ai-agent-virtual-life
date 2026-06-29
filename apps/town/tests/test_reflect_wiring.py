"""V6 反思调度器接通测试

验证 town.main.run_tick / run_dialogue 与 memory_reflection.Reflector 之间的
事件接线:
- run_tick 把 decision append 到 STM
- run_tick 末尾给每个 agent 调 reflector.maybe_reflect 并传 bus
- run_dialogue 把 dialogue message append 到 STM

不依赖真 Redis/Postgres/LLM,所有 ctx 字段都是 MagicMock/AsyncMock。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from memory_reflection import Event


@pytest.fixture
def fake_ctx():
    """最小 ctx 字典,只装 tick_loop 用的几个 key"""
    return {
        "agents": {
            "lisi": MagicMock(
                decide=AsyncMock(
                    return_value=MagicMock(
                        name="idle", target=None, to_dict=lambda: {"name": "idle"}
                    )
                )
            ),
            "wangwu": MagicMock(
                decide=AsyncMock(
                    return_value=MagicMock(
                        name="idle", target=None, to_dict=lambda: {"name": "idle"}
                    )
                )
            ),
        },
        "world": MagicMock(
            snapshot=lambda aid: {"status_bar": "", "location": "客厅"},
            location_of=lambda aid: "客厅",
        ),
        "event_store": MagicMock(
            append=AsyncMock(),
            create_dialogue=AsyncMock(return_value=1),
            add_dialogue_message=AsyncMock(),
        ),
        "stm": MagicMock(add=AsyncMock()),
        "ltm": MagicMock(
            add_summary=AsyncMock(),
            recent_summaries=AsyncMock(return_value=[]),
            redis=MagicMock(
                get=AsyncMock(return_value=None),
                set=AsyncMock(),
                expire=AsyncMock(),
            ),
        ),
        "reflector": MagicMock(maybe_reflect=AsyncMock(return_value=None)),
        "bus": MagicMock(publish=AsyncMock(), subscribe=MagicMock()),
        "trigger": MagicMock(should_start=MagicMock(return_value=False)),
        "dialogue_gen": MagicMock(generate=AsyncMock()),
        "personas": [
            {"id": "lisi", "name": "李四"},
            {"id": "wangwu", "name": "王五"},
        ],
    }


@pytest.mark.asyncio
async def test_run_tick_appends_decision_to_stm(fake_ctx):
    """每个 decision 后必须 append 到 STM(让 Reflector 有数据可读)"""
    from town.main import run_tick

    with patch("town.main.ctx", fake_ctx):
        await run_tick()
    # 2 个 agent × 1 decision = 2 次 add
    assert fake_ctx["stm"].add.await_count == 2
    for call in fake_ctx["stm"].add.call_args_list:
        ev = call.args[0]
        assert isinstance(ev, Event)
        assert ev.kind == "decision"


@pytest.mark.asyncio
async def test_run_tick_calls_reflector_with_bus(fake_ctx):
    """run_tick 末尾必须给每个 agent 调 reflector.maybe_reflect 并传 bus"""
    from town.main import run_tick

    with patch("town.main.ctx", fake_ctx):
        await run_tick()
    assert fake_ctx["reflector"].maybe_reflect.await_count == 2
    for call in fake_ctx["reflector"].maybe_reflect.call_args_list:
        assert call.kwargs.get("bus") is fake_ctx["bus"]


@pytest.mark.asyncio
async def test_run_dialogue_appends_to_stm(fake_ctx):
    """run_dialogue 每条 message 给 speaker append 到 STM"""
    fake_ctx["dialogue_gen"].generate.return_value = [
        ("李四", "你好"),
        ("王五", "你好啊"),
    ]
    fake_ctx["trigger"].should_start.return_value = True
    from town.main import run_dialogue

    # 触发 trigger:让 run_dialogue 被调,需要让 occupants >= 2 且 trigger=True
    # 显式直接调 run_dialogue 来验证其行为
    with patch("town.main.ctx", fake_ctx):
        await run_dialogue("lisi", "wangwu", "客厅")
    # 2 条 message × 1 stm.add = 2
    assert fake_ctx["stm"].add.await_count >= 2
    for call in fake_ctx["stm"].add.call_args_list:
        ev = call.args[0]
        assert ev.kind == "dialogue"


# --- I12 fix:Reflector _set_last 失败 re-raise 后 run_tick 必须 continue (task #83) ---


@pytest.mark.asyncio
async def test_run_tick_continues_when_set_last_fails(fake_ctx):
    """_set_last 失败 re-raise 后,run_tick 必须 continue(不能 crash 其他 agent)

    验证 town main loop 的 try/except 包裹保证 tick 不被反射器异常打断。
    """
    # mock reflector.maybe_reflect 抛异常(模拟 _set_last 失败 re-raise 上来的场景)
    fake_ctx["reflector"].maybe_reflect = AsyncMock(
        side_effect=Exception("redis down")
    )
    from town.main import run_tick

    with patch("town.main.ctx", fake_ctx):
        # 不能 crash
        await run_tick()
    # 每个 agent 都尝试过 maybe_reflect(2 个 agent)
    assert fake_ctx["reflector"].maybe_reflect.await_count == 2


# --- V5:指令面板 — run_tick 从 commands dict 弹指令传给 agent.decide ---


@pytest.mark.asyncio
async def test_run_tick_pops_user_command_into_decide(fake_ctx):
    """run_tick 必须从 commands dict 弹一条指令传给 agent.decide(user_command=)"""
    from town.main import commands as global_commands

    # 前后清理 global commands(避免污染其他测试)
    global_commands.clear()
    global_commands["lisi"] = ["去买菜"]
    try:
        fake_ctx["agents"]["lisi"].decide = AsyncMock(
            return_value=MagicMock(
                name="go_to", target="公园",
                to_dict=lambda: {"name": "go_to", "target": "公园"},
            )
        )
        from town.main import run_tick

        with patch("town.main.ctx", fake_ctx), patch("town.main.commands", global_commands):
            await run_tick()

        # lisi 的 decide 必须收到 user_command="去买菜"
        call_kwargs = fake_ctx["agents"]["lisi"].decide.call_args.kwargs
        assert call_kwargs.get("user_command") == "去买菜"
        # 队列已 pop(空 list)
        assert global_commands["lisi"] == []
    finally:
        global_commands.clear()