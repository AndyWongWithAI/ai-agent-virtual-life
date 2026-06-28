"""对话生成:用 LLM 生成两个 agent 之间的简短对话。"""
from llm_client import LLMClient

PROMPT = """请生成 {a_name} 和 {b_name} 在【{location}】的一段简短对话(2-4 轮)。两人性格:
- {a_name}: {a_persona}
- {b_name}: {b_persona}

只输出 JSON,格式:
{{"messages":[{{"agent":"{a_name}","content":"..."}},{{"agent":"{b_name}","content":"..."}}]}}
中文,口语化,每条 20 字内。
"""


class DialogueGenerator:
    """调用 LLM 生成两个 agent 之间的对话。"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def generate(
        self,
        *,
        a_name: str,
        b_name: str,
        a_persona: str,
        b_persona: str,
        location: str,
    ) -> list[tuple[str, str]]:
        prompt = PROMPT.format(
            a_name=a_name,
            b_name=b_name,
            location=location,
            a_persona=a_persona,
            b_persona=b_persona,
        )
        result = await self.llm.call(
            [{"role": "user", "content": prompt}],
            max_tokens=400,
            json_schema={"required": ["messages"]},
        )
        return [(m["agent"], m["content"]) for m in result["messages"]]
