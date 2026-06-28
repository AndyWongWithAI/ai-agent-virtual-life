"""数据模型:Event(短期事件)、Summary(长期反思摘要)

用 Pydantic BaseModel 是因为 short_term/long_term 需要 model_dump_json /
model_validate_json 来序列化到 Redis(plain @dataclass 不支持)。
"""
from datetime import datetime
from pydantic import BaseModel, Field


class Event(BaseModel):
    """短期记忆中的单条事件

    kind: "decision" | "dialogue" | "observation" | "instruction"
    """
    agent_id: str
    kind: str
    content: str
    ts: datetime = Field(default_factory=datetime.now)


class Summary(BaseModel):
    """长期记忆中的反思摘要(覆盖 6h 窗口)"""
    agent_id: str
    period_start: datetime
    period_end: datetime
    text: str  # 中文反思摘要