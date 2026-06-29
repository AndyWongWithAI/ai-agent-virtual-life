"""反思摘要器:每 6 小时触发,把过去 6h 短期事件压缩成反思摘要
I9 fix:last_reflect 持久化到 Redis,重启后不立刻全部触发
I10 fix:events_text 按时间正序输出,LLM 读起来是"早→晚"
"""
import logging
from datetime import datetime, timedelta, timezone

from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .models import Summary

logger = logging.getLogger(__name__)

LAST_REFLECT_TTL_SEC = 7 * 86400  # 7d,留余量;触发条件是 6h


class Reflector:
    """每 6 小时触发一次,把过去 6h 短期事件压缩成反思摘要"""

    def __init__(self, llm_client, stm: ShortTermMemory, ltm: LongTermMemory):
        self.llm = llm_client
        self.stm = stm
        self.ltm = ltm

    def _redis_key(self, agent_id: str) -> str:
        return f"reflect:last:{agent_id}"

    async def _get_last(self, agent_id: str) -> datetime | None:
        # I12 fix:去掉内存缓存,每次直接读 Redis(避免 stale 值卡住 6h gate)
        try:
            raw = await self.ltm.redis.get(self._redis_key(agent_id))
        except Exception:
            logger.exception("redis get last_reflect failed")
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        # 兼容 mock / 非 str 类型(测试或异常 driver)
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    async def _set_last(self, agent_id: str, ts: datetime) -> None:
        # I12 fix:写 Redis 失败必须 re-raise,避免上层以为成功
        try:
            await self.ltm.redis.set(
                self._redis_key(agent_id),
                ts.isoformat(),
                ex=LAST_REFLECT_TTL_SEC,
            )
        except Exception:
            logger.exception("redis set last_reflect failed")
            raise

    async def maybe_reflect(
        self,
        agent_id: str,
        now: datetime | None = None,
        bus=None,
    ) -> str | None:
        now = now or datetime.now()
        last = await self._get_last(agent_id)
        if last and (now - last) < timedelta(hours=6):
            return None

        events = await self.stm.recent(agent_id, n=100)
        if not events:
            return None

        # 按时间倒序选过去 6h
        window = [e for e in events if now - e.ts <= timedelta(hours=6)]
        if len(window) < 3:
            return None

        # I10 fix:按时间正序输出,LLM 看到的叙事是"早→晚"
        window.sort(key=lambda e: e.ts)
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
        await self._set_last(agent_id, now)
        # V6:广播反思事件到 bus(让前端能收到),失败不 crash(tick_loop 必须继续)
        if bus is not None:
            try:
                # local import 避免循环依赖(memory_reflection -> event_bus 单向)
                from event_bus import Topic

                await bus.publish(
                    Topic.MEMORY_REFLECT,
                    {
                        "topic": Topic.MEMORY_REFLECT.value,
                        "agent_id": agent_id,
                        "period_start": period_start.isoformat(),
                        "period_end": now.isoformat(),
                        "text": summary_text,
                    },
                )
            except Exception:
                logger.exception("reflector publish failed for %s", agent_id)
        return summary_text
