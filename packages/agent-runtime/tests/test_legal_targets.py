"""B5 regression:legal_targets 注入 prompt + World snapshot SSOT(AD1)

背景:LLM 之前会输出 '去公司' 但世界只有 5 个合法地点。
修复:decision.py prompt 注入 legal_targets;World.snapshot() 暴露 legal_targets。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent_runtime.decision import DecisionMaker
from agent_runtime.actions import Action
from virtual_world_engine.space import DEFAULT_LOCATIONS


@pytest.mark.asyncio
async def test_prompt_includes_legal_targets():
    """prompt 必须包含 legal_targets,LLM 才知道能去哪些地点"""
    dm = DecisionMaker.__new__(DecisionMaker)
    dm.llm = MagicMock()
    dm.llm.call = AsyncMock(return_value={
        "reasoning": "去公园散步",
        "action": {"name": "go_to", "target": "公园", "params": {}},
    })
    await dm.decide(
        name="李四", now_str="2026-06-29 14:30", weekday="一",
        status_bar="饱 70", location="李四家",
        adjacency=["客厅", "厨房"], weather="晴",
        recent_summary="无", forced_action=None,
        legal_targets=DEFAULT_LOCATIONS,
    )
    prompt = dm.llm.call.call_args[0][0][0]["content"]
    for loc in DEFAULT_LOCATIONS:
        assert loc in prompt, f"prompt 缺 legal_target {loc}"
    # 还应该明示"go_to 只能选这些"
    assert "go_to" in prompt and "合法" in prompt


@pytest.mark.asyncio
async def test_legal_targets_none_fallback():
    """不传 legal_targets 时 prompt 应有 fallback 文案,不崩"""
    dm = DecisionMaker.__new__(DecisionMaker)
    dm.llm = MagicMock()
    dm.llm.call = AsyncMock(return_value={
        "reasoning": "x", "action": {"name": "idle", "target": None, "params": {}},
    })
    await dm.decide(
        name="x", now_str="2026-06-29 14:30", weekday="一",
        status_bar="x", location="x", adjacency=[], weather="晴",
        recent_summary="无", forced_action=None,
        legal_targets=None,
    )
    prompt = dm.llm.call.call_args[0][0][0]["content"]
    assert "无限制" in prompt


def test_world_snapshot_exposes_legal_targets():
    """AD1 fix:World.snapshot 必须有 legal_targets 字段,消除 main.py 硬编码"""
    from virtual_world_engine import World
    w = World()
    w.place("a1", "李四家")
    snap = w.snapshot("a1")
    assert "legal_targets" in snap
    assert set(snap["legal_targets"]) == set(DEFAULT_LOCATIONS)


def test_default_locations_has_five_entries():
    """回归:确保 5 个地点不变(如果改了,记得改 personas 起点 + 测试)"""
    assert len(DEFAULT_LOCATIONS) == 5
    expected = {"李四家", "王五家", "客厅", "厨房", "公园"}
    assert set(DEFAULT_LOCATIONS) == expected