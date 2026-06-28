"""DialogueTrigger 单元测试。"""
from dialogue_generator.trigger import DialogueTrigger


def test_trigger_social_location():
    """社交场所 + 社交动作 → 应该开始对话。"""
    t = DialogueTrigger()
    assert t.should_start(action_a_name="idle", action_b_name="eat", location="客厅") is True


def test_trigger_no_social_location():
    """非社交场所 → 不应该开始对话。"""
    t = DialogueTrigger()
    assert t.should_start(action_a_name="idle", action_b_name="eat", location="李四家") is False


def test_trigger_no_social_action():
    """社交场所 + 无社交动作 → 不应该开始对话。"""
    t = DialogueTrigger()
    assert t.should_start(action_a_name="sleep", action_b_name="sleep", location="客厅") is False
