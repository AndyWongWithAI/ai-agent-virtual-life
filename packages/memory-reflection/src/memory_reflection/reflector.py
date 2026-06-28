"""反思摘要器:每 6 小时触发,把过去 6h 短期事件压缩成反思摘要"""
from datetime import datetime, timedelta

from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .models import Summary


class Reflector:
    """每 6 小时触发一次,把过去 6h 短期事件压缩成反思摘要"""

    def __init__(self, llm_client, stm: ShortTermMemory, ltm: LongTermMemory):
        self.llm = llm_client
        self.stm = stm
        self.ltm = ltm
        self.last_reflect: dict[str, datetime] = {}

    async def maybe_reflect(self, agent_id: str, now: datetime | None = None) -> str | None:
        now = now or datetime.now()
        last = self.last_reflect.get(agent_id)
        if last and (now - last) < timedelta(hours=6):
            return None

        events = await self.stm.recent(agent_id, n=100)
        if not events:
            return None

        # 按时间倒序选过去 6h
        window = [e for e in events if now - e.ts <= timedelta(hours=6)]
        if len(window) < 3:
            return None

        events_text = "\n".join(
            f"- {e.ts.strftime('%H:%M')} [{e.kind}] {e.content}" for e in window[:50]
        )
        prompt = (
            "你是一个 AI 智能体的记忆反思系统。请阅读下面这个智能体过去 6 小时的事件,"
            "用 2-3 句话总结他/她的关键行为模式、情绪倾向、与谁关系有变化。"
            "中文输出,150 字以内。\n\n"
            f"【事件】\n{events_text}\n\n【反思摘要】"
        )
        summary_text = await self.llm.call(
            [{"role": "user", "content": prompt}], max_tokens=300
        )

        period_start = min(e.ts for e in window)
        await self.ltm.add_summary(
            Summary(
                agent_id=agent_id,
                period_start=period_start,
                period_end=now,
                text=summary_text,
            )
        )
        self.last_reflect[agent_id] = now
        return summary_text