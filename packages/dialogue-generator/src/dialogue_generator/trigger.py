"""对话触发判断:两个 agent 是否应该开始一段对话。"""
SOCIAL_LOCATIONS = {"客厅", "厨房", "公园"}
SOCIAL_ACTIONS = {"talk_to", "eat", "idle"}


class DialogueTrigger:
    """判断两个 agent 在同一地点是否应该触发对话。

    触发条件:地点在社交场所 + 任一 agent 动作属于社交动作。
    """

    def should_start(self, *, action_a_name: str, action_b_name: str, location: str) -> bool:
        if location not in SOCIAL_LOCATIONS:
            return False
        return action_a_name in SOCIAL_ACTIONS or action_b_name in SOCIAL_ACTIONS
