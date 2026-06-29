"""对话触发判断:两个 agent 是否应该开始一段对话。"""
import random

SOCIAL_LOCATIONS = {"客厅", "厨房", "公园", "王五家", "李四家"}
SOCIAL_ACTIONS = {"talk_to", "eat", "idle"}

# 30% 触发概率:即使条件满足也只 30% 真触发,避免刷屏。
DIALOGUE_TRIGGER_PROBABILITY = 0.3


class DialogueTrigger:
    """判断两个 agent 在同一地点是否应该触发对话。

    触发条件:地点在社交场所 + 任一 agent 动作属于社交动作 + 概率门命中。
    """

    def should_start(self, *, action_a_name: str, action_b_name: str, location: str) -> bool:
        if location not in SOCIAL_LOCATIONS:
            return False
        if not (action_a_name in SOCIAL_ACTIONS or action_b_name in SOCIAL_ACTIONS):
            return False
        # 概率门:即使条件满足也只 30% 真触发,避免刷屏
        return random.random() < DIALOGUE_TRIGGER_PROBABILITY
