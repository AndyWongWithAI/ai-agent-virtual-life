"""DecisionMaker 接受 dict 或 str 的 status_bar(V2 task #84)。

背景:World.snapshot 现在返回 status_bar 是 dict(便于前端可视化),
但 LLM prompt 仍需要字符串格式。DecisionMaker 内部做 dict→str format,
同时保持向后兼容(老调用者传 str 仍能用)。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent_runtime.decision import DecisionMaker


@pytest.mark.asyncio
async def test_decision_maker_accepts_dict_status_bar():
    """status_bar 是 dict 时也能 format 成字符串注入 prompt"""
    llm = MagicMock()
    captured = {}

    async def _capture(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return {"reasoning": "ok", "action": {"name": "idle", "target": None, "params": {}}}

    llm.call = AsyncMock(side_effect=_capture)
    dm = DecisionMaker(llm)
    action = await dm.decide(
        name="李四", now_str="2026-06-29 14:00", weekday="一",
        status_bar={"饱": 70, "累": 40, "孤独": 30, "快乐": 60},
        location="李四家", adjacency=["客厅"], weather="晴",
        recent_summary="", forced_action=None, legal_targets=["李四家", "客厅"],
    )
    # prompt 必须含 "饱 70" 这种格式
    assert "饱" in captured["prompt"]
    assert "70" in captured["prompt"]


@pytest.mark.asyncio
async def test_decision_maker_still_accepts_string_status_bar():
    """向后兼容:str 也能用"""
    llm = MagicMock()

    async def _capture(messages, **kw):
        return {"reasoning": "ok", "action": {"name": "idle", "target": None, "params": {}}}

    llm.call = AsyncMock(side_effect=_capture)
    dm = DecisionMaker(llm)
    action = await dm.decide(
        name="李四", now_str="2026-06-29 14:00", weekday="一",
        status_bar="饱 70, 累 40",  # 旧格式字符串
        location="李四家", adjacency=[], weather="晴",
        recent_summary="", forced_action=None, legal_targets=[],
    )
    assert action.name == "idle"


# --- V5:用户指令注入 prompt(指令面板 task #85) ---


@pytest.mark.asyncio
async def test_decision_maker_includes_user_command_in_prompt():
    """user_command 必须注入 prompt"""
    llm = MagicMock()
    captured = {}

    async def _capture(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return {"reasoning": "ok", "action": {"name": "go_to", "target": "公园", "params": {}}}

    llm.call = AsyncMock(side_effect=_capture)
    dm = DecisionMaker(llm)
    await dm.decide(
        name="李四", now_str="2026-06-29 14:00", weekday="一",
        status_bar="饱 70", location="李四家", adjacency=[], weather="晴",
        recent_summary="", forced_action=None, legal_targets=["李四家", "公园"],
        user_command="去买菜",
    )
    assert "去买菜" in captured["prompt"]
    assert "用户指令" in captured["prompt"]


@pytest.mark.asyncio
async def test_decision_maker_no_user_command_section_when_none():
    """user_command=None 时不渲染指令段"""
    llm = MagicMock()

    async def _capture(messages, **kw):
        return {"reasoning": "ok", "action": {"name": "idle", "target": None, "params": {}}}

    llm.call = AsyncMock(side_effect=_capture)
    dm = DecisionMaker(llm)
    await dm.decide(
        name="李四", now_str="2026-06-29 14:00", weekday="一",
        status_bar="饱 70", location="李四家", adjacency=[], weather="晴",
        recent_summary="", forced_action=None, legal_targets=["李四家"],
    )
    # 不能含"用户指令"
    captured_prompt = llm.call.call_args[0][0][0]["content"]
    assert "用户指令" not in captured_prompt
