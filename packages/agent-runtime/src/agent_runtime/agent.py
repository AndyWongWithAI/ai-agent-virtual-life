"""智能体组装:感知(world snapshot) → 决策(schedule/decision) → 行动(Action)

组装 LLM 客户端 + 短期/长期记忆 + 反思器 + 作息锁 + 决策核心。
decide() 是核心入口,tick 调度器每 60s/300s 调用一次。
"""
import logging
from datetime import datetime

from llm_client import LLMClient
from memory_reflection import ShortTermMemory, LongTermMemory, Reflector

from .decision import DecisionMaker
from .schedule import ScheduleLock
from .actions import Action

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, agent_id: str, name: str, persona: dict,
                 llm: LLMClient, stm: ShortTermMemory, ltm: LongTermMemory,
                 reflector: Reflector):
        self.agent_id = agent_id
        self.name = name
        self.persona = persona
        self.llm = llm
        self.stm = stm
        self.ltm = ltm
        self.reflector = reflector
        self.decision_maker = DecisionMaker(llm)
        self.schedule = ScheduleLock()

    async def decide(self, world_snapshot: dict) -> Action:
        now = datetime.now()
        forced = self.schedule.forced_action(now)
        recent = await self.stm.recent(self.agent_id, n=10)
        summaries = await self.ltm.recent_summaries(self.agent_id, n=2)
        recent_summary = "\n".join(s.text for s in summaries)
        action = await self.decision_maker.decide(
            name=self.name,
            now_str=now.strftime("%Y-%m-%d %H:%M"),
            weekday=["一", "二", "三", "四", "五", "六", "日"][now.weekday()],
            status_bar=world_snapshot["status_bar"],
            location=world_snapshot["location"],
            adjacency=world_snapshot.get("adjacency", []),
            weather=world_snapshot.get("weather", "晴"),
            recent_summary=recent_summary,
            forced_action=forced,
            legal_targets=world_snapshot.get("legal_targets", []),
        )
        # V6:反思调度已上移到 town.main.run_tick 末尾(带 bus 引用),
        # 这里不再触发(避免双重触发 + 拿不到 bus)。
        return action