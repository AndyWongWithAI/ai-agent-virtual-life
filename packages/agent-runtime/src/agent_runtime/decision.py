"""决策核心:用 LLM 决定智能体下一步动作

如果 schedule_lock 给出 forced_action,则直接返回,不调 LLM。
否则,组织 prompt + json_schema 调 LLM,解析返回 {reasoning, action}。
"""
from llm_client import LLMClient

from .actions import Action

SCHEMA = {"required": ["reasoning", "action"]}

PROMPT_TEMPLATE = """你是 {name},一个虚拟小镇居民。当前:
- 时间:{now_str} ({weekday})
- 状态:{status_bar}
- 位置:{location}
- 邻接:{adjacency}
- 天气:{weather}
- 近期反思:{recent_summary}

请决定你接下来要做什么。从以下动作里选一个:go_to(去某地)、talk_to(跟某人说话)、eat(吃饭)、sleep(睡觉)、work(工作)、idle(发呆)。

只输出 JSON,格式:
{{"reasoning":"<为什么,30字内>","action":{{"name":"<动作名>","target":"<地点或人名,无则null>","params":{{}}}}}}
"""


class DecisionMaker:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def decide(self, *, name: str, now_str: str, weekday: str, status_bar: str,
                     location: str, adjacency: list[str], weather: str,
                     recent_summary: str, forced_action: Action | None) -> Action:
        if forced_action is not None:
            return forced_action
        prompt = PROMPT_TEMPLATE.format(
            name=name, now_str=now_str, weekday=weekday, status_bar=status_bar,
            location=location, adjacency=", ".join(adjacency) or "无",
            weather=weather, recent_summary=recent_summary or "无",
        )
        result = await self.llm.call(
            [{"role": "user", "content": prompt}],
            max_tokens=300, json_schema=SCHEMA,
        )
        a = result["action"]
        return Action(name=a["name"], target=a.get("target"), params=a.get("params"))