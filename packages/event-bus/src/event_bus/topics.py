# packages/event-bus/src/event_bus/topics.py
from enum import Enum


class Topic(str, Enum):
    WORLD_TICK = "world.tick"
    AGENT_DECISION = "agent.decision"
    DIALOGUE_START = "dialogue.start"
    DIALOGUE_MESSAGE = "dialogue.message"
    MEMORY_REFLECT = "memory.reflect"
    # 阶段 2 块 1(任务 T10):导演注入场景事件。下个 tick 注入所有 agent LLM prompt。
    DIRECTOR_SCENE = "director.scene"
