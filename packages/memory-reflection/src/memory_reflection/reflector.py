"""反思摘要器:每 6 小时触发,把过去 6h 短期事件压缩成反思摘要
I9 fix:last_reflect 持久化到 Redis,重启后不立刻全部触发
I10 fix:events_text 按时间正序输出,LLM 读起来是"早→晚"
任务 #123(Bug2):strip 思考链(some LLM 走 chain-of-thought,反射不接受纯文本 strip)
任务 #124(Bug3):反思 prompt 改为中性行为描述,不下判断
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .models import Summary

logger = logging.getLogger(__name__)

# 任务 #123:strip 推理类模型的 chain-of-thought 包裹块(Qwen QwQ / DeepSeek R1 等),
# 文本里的思考内容不应进入 LTM(用户看到会困惑,产品定位是「反思摘要」非「思考日志」)
_THINK_BLOCK_RE = re.compile(r"<\s*(?:think|analysis|reasoning)\s*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", re.DOTALL)

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

    @staticmethod
    def _strip_think_blocks(text: str) -> str:
        """任务 #123:剥离推理类 LLM 的 chain-of-thought 包裹块(如 `` / ``)。

        反射调用 ``llm.call(..., json_schema=None)``,不走 llm-client 的 json-strip 分支,
        故 thinking 块会作为文本块落到 LTM。这里在 reflector 边界清洗,无论上游
        LLM 是否使用 chain-of-thought,落库文本纯干净。
        """
        if not text:
            return text
        cleaned = _THINK_BLOCK_RE.sub("", text)
        # 合并多空行,去首尾空白
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned or "(空摘要)"

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
        # 任务 #124(Bug3):中性化反思 prompt — 游戏机制本身允许 wake_up↔bed 等循环,
        # LLM 把这种循环判读成「卡死/异常」会误导用户。改为客观行为模式描述。
        prompt = (
            "你是一个 AI 智能体的记忆事实提取器。阅读下面这个智能体过去 6 小时的事件,"
            "生成一份客观行为摘要。**只描述做了什么,不下判断、不下诊断、不假设动机**。"
            "避免使用「卡顿/异常/犹豫/困倦」等主观词;允许出现的描述:动作频次、"
            "主要动作类型、地点分布、出现过哪些其他角色。"
            "中文输出 2-3 句,不超过 150 字。\n\n"
            f"【事件】\n{events_text}\n\n【行为摘要】"
        )
        summary_text = await self.llm.call(
            [{"role": "user", "content": prompt}], max_tokens=300
        )
        # 任务 #123(Bug2):strip 思考链(llm_client 的 strip 只走 json_schema 路径,
        # reflector 调 llm.call() 不带 json_schema,thinking 块直接落到 LTM)。
        # 边边界清洗,所有未来 LLM 配置变更不再重蹈。
        summary_text = self._strip_think_blocks(summary_text)

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
