"""指数退避重试(MiniMax M3 接口偶发 5xx/限流保护)"""
import asyncio
import random


async def retry_with_backoff(coro_factory, *, max_attempts: int = 3, base_delay: float = 1.0):
    """coro_factory() -> coroutine; raise last exception on failure

    退避策略:base_delay * 2^attempt + 随机抖动(0~0.5s),避免雷击
    注意:重试时 BudgetExceeded 不应被吞——它会在 budget.record_and_check 处
    自然产生(因为 raw_call 已在 retry 外部被消费过一次),所以这里吞所有
    Exception 是安全的;但调用方需保证 coro_factory 内部不重复累加 budget。
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                await asyncio.sleep(delay)
    raise last_exc