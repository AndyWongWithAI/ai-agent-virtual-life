"""Task 2: LLMClient 基础单测

说明:
- 不依赖真实 MiniMax M3 API,通过 mock _raw_call 隔离
- 不依赖真实 Redis,budget 测试用 mock record_and_check 隔离
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from llm_client.client import LLMClient
from llm_client.budget import BudgetExceeded

# CI runner(redis:6379)与本机(redis:6380)端口差异:统一读 REDIS_URL 环境变量,
# 缺省回退到本机默认 6380,CI 上 workflow 会注入 REDIS_URL=redis://localhost:6379/0。
TEST_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")


def _patch_record(client):
    """helper:patch budget.record_and_check 避免真连 Redis,所有测试统一用法。"""
    return patch.object(client.budget, "record_and_check", AsyncMock())


@pytest.mark.asyncio
async def test_call_returns_text():
    """正常调用:_raw_call 返回 ("text", cost) 时,call 应返回 text"""
    client = LLMClient(
        api_key="test",
        redis_url=TEST_REDIS_URL,
        daily_budget_cny=10.0,
    )
    with _patch_record(client), \
         patch.object(client, "_raw_call", AsyncMock(return_value=("hello", 0.001))):
        result = await client.call([{"role": "user", "content": "hi"}])
    assert result == "hello"
    await client.aclose()


@pytest.mark.asyncio
async def test_budget_exceeded_raises():
    """超预算:_raw_call 返回大 cost 时,BudgetTracker 应抛 BudgetExceeded

    不 mock record_and_check,让它真用 budget(小预算 + 大 cost → 必超)。
    """
    client = LLMClient(
        api_key="test",
        redis_url=TEST_REDIS_URL,
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
        redis_url=TEST_REDIS_URL,
        daily_budget_cny=10.0,
    )
    raw_json = '{"action": "wave", "target": "alice"}'
    with _patch_record(client), \
         patch.object(client, "_raw_call", AsyncMock(return_value=(raw_json, 0.001))):
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
        redis_url=TEST_REDIS_URL,
        daily_budget_cny=10.0,
    )
    raw_json = '{"action": "wave"}'  # 缺 target
    with _patch_record(client), \
         patch.object(client, "_raw_call", AsyncMock(return_value=(raw_json, 0.001))):
        with pytest.raises(ValueError, match="Missing key target"):
            await client.call(
                [{"role": "user", "content": "act"}],
                json_schema={"required": ["action", "target"]},
            )
    await client.aclose()


# === I3 fix:usage=None 时按 max_tokens 悲观估算 ===
@pytest.mark.asyncio
async def test_raw_call_uses_max_tokens_when_usage_is_none():
    """I3:resp.usage is None → cost 按 max_tokens 上限估算(in_tok 用消息字符数 / 4 粗估)"""
    client = LLMClient(
        api_key="test",
        redis_url=TEST_REDIS_URL,
        daily_budget_cny=10.0,
    )
    # 构造一个 response 对象,usage=None
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "hi"
    fake_response.usage = None
    with patch.object(
        client.client.chat.completions,
        "create",
        AsyncMock(return_value=fake_response),
    ):
        text, cost = await client._raw_call(
            [{"role": "user", "content": "abcd"}],  # 4 chars → 1 in_tok 粗估
            max_tokens=512,
        )
    # cost = (in_tok * 1 + out_tok * 2) / 1e6 = (1 + 512*2) / 1e6 = 0.001025
    assert text == "hi"
    assert cost == pytest.approx((1 + 512 * 2) / 1_000_000)
    await client.aclose()


@pytest.mark.asyncio
async def test_raw_call_uses_real_usage_when_present():
    """I3 对照:resp.usage 正常时,应使用真实值,不用悲观估算"""
    client = LLMClient(
        api_key="test",
        redis_url=TEST_REDIS_URL,
        daily_budget_cny=10.0,
    )
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "hi"
    fake_response.usage = MagicMock()
    fake_response.usage.prompt_tokens = 10
    fake_response.usage.completion_tokens = 20
    with patch.object(
        client.client.chat.completions,
        "create",
        AsyncMock(return_value=fake_response),
    ):
        text, cost = await client._raw_call(
            [{"role": "user", "content": "x" * 1000}],  # 长消息,但 usage 覆盖
            max_tokens=512,
        )
    # cost = (10*1 + 20*2) / 1e6 = 0.00005
    assert cost == pytest.approx((10 * 1 + 20 * 2) / 1_000_000)
    await client.aclose()


# === I4 fix:retry 每次 attempt 累加 cost ===
@pytest.mark.asyncio
async def test_call_accumulates_cost_per_retry_attempt():
    """I4:retry 中每次成功 attempt 累加 1 次 cost,失败 attempt 不累加"""
    client = LLMClient(
        api_key="test",
        redis_url=TEST_REDIS_URL,
        daily_budget_cny=10.0,
    )
    # 模拟 _raw_call 失败 2 次,第 3 次成功
    call_count = {"n": 0}

    async def flaky_raw(messages, max_tokens):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("transient 5xx")
        return ("ok", 0.002)

    record_calls: list[float] = []

    async def record_check(cost):
        record_calls.append(cost)

    with patch.object(client, "_raw_call", AsyncMock(side_effect=flaky_raw)), \
         patch.object(client.budget, "record_and_check", AsyncMock(side_effect=record_check)):
        result = await client.call([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert call_count["n"] == 3
    # 失败 2 次不进 record,成功 1 次累加
    assert len(record_calls) == 1
    assert record_calls[0] == 0.002
    await client.aclose()


@pytest.mark.asyncio
async def test_call_records_each_successful_attempt():
    """I4 对照:每次 attempt 都成功(0 retry 路径)只累加 1 次"""
    client = LLMClient(
        api_key="test",
        redis_url=TEST_REDIS_URL,
        daily_budget_cny=10.0,
    )
    record_calls: list[float] = []

    async def record_check(cost):
        record_calls.append(cost)

    with patch.object(client, "_raw_call", AsyncMock(return_value=("ok", 0.005))), \
         patch.object(client.budget, "record_and_check", AsyncMock(side_effect=record_check)):
        result = await client.call([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert len(record_calls) == 1
    assert record_calls[0] == 0.005
    await client.aclose()


# === B4/B6 fix:R1 类思考模型 <think> 标签 ===
@pytest.mark.asyncio
async def test_think_wrapped_json_parsed():
    """B4: LLM 返回 <think>...</think>\n{json} → 正确剥离 think,解析 JSON"""
    client = LLMClient(
        api_key="test", redis_url=TEST_REDIS_URL, daily_budget_cny=10.0,
    )
    wrapped = (
        '<think>分析中:李四在家,决定去公园</think>'
        '{"reasoning":"去公园散步","action":{"name":"go_to","target":"公园","params":{}}}'
    )
    with _patch_record(client), \
         patch.object(client, "_raw_call", AsyncMock(return_value=(wrapped, 0.001))):
        result = await client.call(
            [{"role": "user", "content": "hi"}],
            json_schema={"required": ["reasoning", "action"]},
        )
    assert result["reasoning"] == "去公园散步"
    assert result["action"]["target"] == "公园"
    await client.aclose()


@pytest.mark.asyncio
async def test_think_no_close_tag_brace_fallback():
    """B6: max_tokens 截断导致 <think> 无 </think> 闭合,从首个 { 开始 fallback 解析"""
    client = LLMClient(
        api_key="test", redis_url=TEST_REDIS_URL, daily_budget_cny=10.0,
    )
    truncated = (
        "<think>还在思考中...但是 max_tokens 截断了\n"
        '{"reasoning":"简短决策","action":{"name":"idle","target":null,"params":{}}}'
    )
    with _patch_record(client), \
         patch.object(client, "_raw_call", AsyncMock(return_value=(truncated, 0.001))):
        result = await client.call(
            [{"role": "user", "content": "hi"}],
            json_schema={"required": ["reasoning", "action"]},
        )
    assert result["reasoning"] == "简短决策"
    assert result["action"]["name"] == "idle"
    await client.aclose()


@pytest.mark.asyncio
async def test_think_close_tag_must_be_last_occurrence():
    """B4 边界:LLM 在 think 块结束后又输出 {text},应取最后一个 </think> 之后"""
    client = LLMClient(
        api_key="test", redis_url=TEST_REDIS_URL, daily_budget_cny=10.0,
    )
    text_with_two_thinks = (
        '<think>first thought</think>中间废话{not_json}'
        '<think>second thought</think>'
        '{"reasoning":"最终决策","action":{"name":"sleep","target":"bed","params":{}}}'
    )
    with _patch_record(client), \
         patch.object(client, "_raw_call", AsyncMock(return_value=(text_with_two_thinks, 0.001))):
        result = await client.call(
            [{"role": "user", "content": "hi"}],
            json_schema={"required": ["reasoning", "action"]},
        )
    assert result["reasoning"] == "最终决策"
    await client.aclose()