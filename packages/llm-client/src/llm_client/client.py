"""MiniMax M3 LLM 客户端(OpenAI 兼容,CNY 计费)

设计:
- AsyncOpenAI 适配 MiniMax M3 OpenAI 兼容端点
- 限速:TokenBucket 防止突发超 QPS
- 重试:retry_with_backoff 处理 5xx/网络抖动
- 降级:json_schema 校验失败抛 ValueError,由上游业务决定 fallback
- 成本:基于 usage 估算 CNY,写入 Redis 计数器,超预算抛 BudgetExceeded
  I3 fix:usage 为 None 时按 max_tokens 悲观估算(防免费泄漏)
  I4 fix:cost 累加在每次 attempt 内部(retry 3 次 → 累加 3 次)
"""
import json
import logging
import redis.asyncio as redis_async
from openai import AsyncOpenAI

from .rate_limiter import TokenBucket
from .retry import retry_with_backoff
from .budget import BudgetTracker, BudgetExceeded

logger = logging.getLogger(__name__)


class LLMClient:
    """MiniMax M3 (OpenAI 兼容) 客户端,统一处理限速/重试/降级/成本

    参数:
        api_key: MiniMax API key(prod 从 env 注入;测试用占位符)
        redis_url: Redis 连接串,用于 cost counter
        daily_budget_cny: 日预算,默认 ¥20
        base_url: MiniMax OpenAI 兼容端点
        model: 模型名,默认 MiniMax-M3
        rate_per_sec: 令牌桶速率,默认 5/s
    """

    def __init__(
        self,
        api_key: str,
        redis_url: str,
        daily_budget_cny: float = 20.0,
        base_url: str = "https://api.minimax.chat/v1",
        model: str = "MiniMax-M3",
        rate_per_sec: float = 5.0,
    ):
        # 注意:cost 单位为 CNY(¥),因为 MiniMax 按人民币计费
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.redis = redis_async.from_url(redis_url)
        self.budget = BudgetTracker(self.redis, daily_budget_cny)
        self.bucket = TokenBucket(rate_per_sec=rate_per_sec, capacity=int(rate_per_sec * 2))
        self.model = model

    async def _raw_call(self, messages, max_tokens: int):
        """调用 MiniMax M3 chat completion,返回 (text, cost_cny)

        messages: [{"role": "user|assistant|system", "content": str}]
        cost 估算:¥1/M input + ¥2/M output(MiniMax M3 参考价量级)
        I3 fix:usage 缺失时按 max_tokens 上限估算(悲观计费),防免费泄漏
        """
        resp = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=messages,
        )
        text = resp.choices[0].message.content or ""
        if resp.usage:
            in_tok = resp.usage.prompt_tokens
            out_tok = resp.usage.completion_tokens
        else:
            # I3 fix:悲观估算 — input 按 messages 字符数 / 4 粗估(英文 1tok≈4char),
            # output 按 max_tokens 上限。WARN 日志,方便排障发现 MiniMax M3 的偶发 bug。
            in_tok = sum(len(m.get("content", "")) for m in messages) // 4
            out_tok = max_tokens
            logger.warning(
                "LLM usage is None, falling back to pessimistic estimate"
                " (in_tok≈%d, out_tok=%d)",
                in_tok, out_tok,
            )
        cost_cny = (in_tok * 1 + out_tok * 2) / 1_000_000
        return text, cost_cny

    async def call(
        self,
        messages,
        *,
        max_tokens: int = 1024,
        json_schema: dict | None = None,
    ) -> str | dict:
        """主入口:限速 -> 重试调用 -> 累加 cost -> 可选 JSON 校验

        返回:
            - 无 json_schema:返回 str(LLM 文本)
            - 有 json_schema:返回 dict(顶层 key 必在 required 中)
        异常:
            BudgetExceeded:日预算耗尽
            ValueError:JSON 解析或 schema 校验失败

        I4 fix:每次 attempt 累加 cost(retry 3 次 → 累加 3 次)。
        实现:把 record_and_check 放在 retry 闭包内,每次 coro_factory() 调用
        后立即累加。
        """
        await self.bucket.acquire()
        accumulated = 0.0

        async def _attempt():
            nonlocal accumulated
            text, cost = await self._raw_call(messages, max_tokens)
            accumulated += cost
            # 累加完再 check,这样 retry 中超预算能及时抛 BudgetExceeded
            await self.budget.record_and_check(cost)
            return text

        text = await retry_with_backoff(_attempt, max_attempts=3)
        if json_schema:
            try:
                parsed = json.loads(text)
                for k in json_schema.get("required", []):
                    assert k in parsed, f"Missing key {k}"
                return parsed
            except (json.JSONDecodeError, AssertionError) as e:
                raise ValueError(f"LLM JSON parse fail: {e}; raw={text[:200]}")
        return text

    async def aclose(self):
        """关闭资源(Redis 连接 + OpenAI HTTP 连接池)"""
        await self.redis.aclose()
        await self.client.close()
