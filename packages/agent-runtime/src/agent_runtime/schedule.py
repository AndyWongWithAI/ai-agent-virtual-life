"""作息锁:根据真实时间决定强制动作,无需调 LLM

规则:
- 23-6 强制睡眠
- 6-8 起床
- 12 点整 午饭
- 18 点整 回家
- 周末睡晚一点(简化版先不开)
"""
from datetime import datetime

from .actions import Action

WEEKEND_WAKE_LATER = False  # 周末睡晚一点(简化版先不开)


class ScheduleLock:
    """根据真实时间决定强制动作;返回 None 表示不强制"""

    def is_asleep(self, now: datetime) -> bool:
        h = now.hour
        return h >= 23 or h < 6

    def forced_action(self, now: datetime) -> Action | None:
        h, m = now.hour, now.minute
        if h >= 23 or h < 6:
            return Action(name="sleep", target="bed")
        if h == 7:
            return Action(name="wake_up", target="bed")
        if h == 12 and m < 5:
            return Action(name="eat", target="kitchen", params={"meal": "lunch"})
        if h == 18 and m < 5:
            return Action(name="go_home", target="home")
        return None