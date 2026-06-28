# packages/event-bus/src/event_bus/topics.py
from enum import Enum


class Topic(str, Enum):
    WORLD_TICK = "world.tick"
    AGENT_DECISION = "agent.decision"
    DIALOGUE_START = "dialogue.start"
    DIALOGUE_MESSAGE = "dialogue.message"
    MEMORY_REFLECT = "memory.reflect"
