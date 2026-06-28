"""指数退避重试(MiniMax M3 接口偶发 5xx/限流保护)"""
import asyncio
import random
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


async def retry_with_backoff(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """coro_factory() -> coroutine; raise last exception on failure

    退避策略:base_delay * 2^attempt + 随机抖动(0~0.5s),避免雷击
    注意:BudgetExceeded 不应被吞;调用方需要在 coro_factory 内部
    显式处理 budget 累加(本版本推荐把 record_and_check 放在 caller,
    即每次 attempt 累加 1 次,见 client.call 注释)。
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
