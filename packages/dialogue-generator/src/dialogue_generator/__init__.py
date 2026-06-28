"""dialogue-generator: 触发判断 + LLM 对话生成(L2 能力层)"""
from .trigger import DialogueTrigger
from .generator import DialogueGenerator

__all__ = ["DialogueTrigger", "DialogueGenerator"]
