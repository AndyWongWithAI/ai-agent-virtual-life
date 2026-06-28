"""令牌桶限速器(MiniMax M3 接口限速保护)"""
import asyncio
import time


class TokenBucket:
    """每秒 N 个请求的令牌桶,异步安全

    算法:
    - 桶容量 = capacity(允许突发)
    - 令牌以 rate/sec 速率匀速补充
    - acquire():有令牌直接取;否则 sleep 等待
    """

    def __init__(self, rate_per_sec: float, capacity: int):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait = (1 - self.tokens) / self.rate
        await asyncio.sleep(wait)
        async with self.lock:
            self.tokens -= 1