"""成本追踪(MiniMax M3 按 CNY 计费)"""
from datetime import date


class BudgetExceeded(Exception):
    """日 LLM 成本超预算时抛出"""
    pass


class BudgetTracker:
    """基于 Redis 的日成本计数器,单位 ¥ (CNY)"""

    def __init__(self, redis, daily_budget_cny: float):
        self.redis = redis
        self.daily_budget_cny = daily_budget_cny

    async def record_and_check(self, cost_cny: float) -> None:
        """累加当日成本,超阈值抛 BudgetExceeded

        key 设计:llm_cost:YYYY-MM-DD,25h 过期(覆盖跨日清零)
        """
        key = f"llm_cost:{date.today().isoformat()}"
        new_total = await self.redis.incrbyfloat(key, cost_cny)
        await self.redis.expire(key, 90000)  # 25h
        if new_total > self.daily_budget_cny:
            raise BudgetExceeded(
                f"Daily LLM budget ¥{self.daily_budget_cny} exceeded (now ¥{new_total:.4f})"
            )