"""Task 2: LLMClient 基础单测

说明:
- 不依赖真实 MiniMax M3 API,通过 mock _raw_call 隔离
- 不依赖真实 Redis,budget 测试用充分小预算+大 cost 触发 BudgetExceeded
"""
import pytest
from unittest.mock import AsyncMock, patch

from llm_client.client import LLMClient
from llm_client.budget import BudgetExceeded


@pytest.mark.asyncio
async def test_call_returns_text():
    """正常调用:_raw_call 返回 ("text", cost) 时,call 应返回 text"""
    client = LLMClient(
        api_key="test",
        redis_url="redis://localhost:6380/0",
        daily_budget_cny=10.0,
    )
    with patch.object(client, "_raw_call", AsyncMock(return_value=("hello", 0.001))):
        result = await client.call([{"role": "user", "content": "hi"}])
    assert result == "hello"


@pytest.mark.asyncio
async def test_budget_exceeded_raises():
    """超预算:_raw_call 返回大 cost 时,BudgetTracker 应抛 BudgetExceeded"""
    client = LLMClient(
        api_key="test",
        redis_url="redis://localhost:6380/0",
        daily_budget_cny=0.0001,  # 极小预算,1.0 cost 必超
    )
    with patch.object(client, "_raw_call", AsyncMock(return_value=("x", 1.0))):
        with pytest.raises(BudgetExceeded):
            await client.call([{"role": "user", "content": "hi"}])
    await client.aclose()


@pytest.mark.asyncio
async def test_call_returns_dict_when_json_schema():
    """json_schema 模式:LLM 返回 JSON 文本时,call 应解析并校验 required keys"""
    client = LLMClient(
        api_key="test",
        redis_url="redis://localhost:6380/0",
        daily_budget_cny=10.0,
    )
    raw_json = '{"action": "wave", "target": "alice"}'
    with patch.object(client, "_raw_call", AsyncMock(return_value=(raw_json, 0.001))):
        result = await client.call(
            [{"role": "user", "content": "act"}],
            json_schema={"required": ["action", "target"]},
        )
    assert result == {"action": "wave", "target": "alice"}
    await client.aclose()


@pytest.mark.asyncio
async def test_call_json_missing_required_raises():
    """json_schema 模式:JSON 缺 required key 时,应抛 ValueError"""
    client = LLMClient(
        api_key="test",
        redis_url="redis://localhost:6380/0",
        daily_budget_cny=10.0,
    )
    raw_json = '{"action": "wave"}'  # 缺 target
    with patch.object(client, "_raw_call", AsyncMock(return_value=(raw_json, 0.001))):
        with pytest.raises(ValueError, match="Missing key target"):
            await client.call(
                [{"role": "user", "content": "act"}],
                json_schema={"required": ["action", "target"]},
            )
    await client.aclose()