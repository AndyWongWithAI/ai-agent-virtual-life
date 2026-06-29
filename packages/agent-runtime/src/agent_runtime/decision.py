"""决策核心:用 LLM 决定智能体下一步动作

如果 schedule_lock 给出 forced_action,则直接返回,不调 LLM。
否则,组织 prompt + json_schema 调 LLM,解析返回 {reasoning, action}。
"""
from llm_client import LLMClient
from virtual_world_engine import format_for_prompt, LABELS_ZH

from .actions import Action

SCHEMA = {"required": ["reasoning", "action"]}

PROMPT_TEMPLATE = """你是 {name},一个虚拟小镇居民。当前:
- 时间:{now_str} ({weekday})
- 状态:{status_bar}(4 维:饱/累/孤独/快乐,0-100,越低越好除快乐)
- 位置:{location}
- 邻接:{adjacency}
- 天气:{weather}
- 近期反思:{recent_summary}
{user_command_section}
可去的地点只有(go_to 只能选这些):{legal_targets}

请决定你接下来要做什么。从以下动作里选一个:go_to(去某地,target 必须是上面的合法地点)、talk_to(跟某人说话)、eat(吃饭)、sleep(睡觉)、work(工作)、idle(发呆)。

直接输出 JSON,不要思考、不要解释。格式:
{{"reasoning":"<为什么,30字内>","action":{{"name":"<动作名>","target":"<合法地点或人名,无则null>","params":{{}}}}}}
"""


class DecisionMaker:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def decide(self, *, name: str, now_str: str, weekday: str, status_bar: "str | dict",
                     location: str, adjacency: list[str], weather: str,
                     recent_summary: str, forced_action: Action | None,
                     legal_targets: list[str] | None = None,
                     user_command: str | None = None) -> Action:
        if forced_action is not None:
            return forced_action
        # 任务 #114:status_bar 内部是 dict(英文 key:hunger/fatigue/loneliness/happiness),
        # 用 status.format_for_prompt 转中文 label 后注入 prompt。
        # 仍接受 str(向后兼容,旧调用者或测试用)
        if isinstance(status_bar, dict):
            status_bar_str = format_for_prompt(status_bar, lang="zh")
        else:
            status_bar_str = status_bar
        # V5:用户指令段(若有)
        if user_command:
            user_command_section = f"- 【用户指令】:{user_command}\n请优先响应这条用户指令\n"
        else:
            user_command_section = ""
        prompt = PROMPT_TEMPLATE.format(
            name=name, now_str=now_str, weekday=weekday, status_bar=status_bar_str,
            location=location, adjacency=", ".join(adjacency) or "无",
            weather=weather, recent_summary=recent_summary or "无",
            user_command_section=user_command_section,
            legal_targets="、".join(legal_targets) if legal_targets else "(无限制)",
        )
        result = await self.llm.call(
            [{"role": "user", "content": prompt}],
            max_tokens=600, json_schema=SCHEMA,
        )
        a = result["action"]
        return Action(name=a["name"], target=a.get("target"), params=a.get("params"))