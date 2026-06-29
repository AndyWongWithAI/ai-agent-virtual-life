"""DialogueTrigger 单元测试。"""
import random
from unittest.mock import patch

from dialogue_generator.trigger import DialogueTrigger, DIALOGUE_TRIGGER_PROBABILITY


def test_trigger_social_location():
    """社交场所 + 社交动作 → 应该开始对话(概率命中时)。"""
    t = DialogueTrigger()
    # 用 patch 强制 random.random() 返回 0.05(< 0.3 必触发)
    with patch("dialogue_generator.trigger.random.random", return_value=0.05):
        assert t.should_start(action_a_name="idle", action_b_name="eat", location="客厅") is True


def test_trigger_home_location():
    """家访也算社交场所(放宽):王五家 + idle → 应该开始对话。"""
    t = DialogueTrigger()
    with patch("dialogue_generator.trigger.random.random", return_value=0.05):
        assert t.should_start(action_a_name="idle", action_b_name="idle", location="王五家") is True


def test_trigger_no_social_location():
    """非社交场所 → 不应该开始对话。"""
    t = DialogueTrigger()
    assert t.should_start(action_a_name="idle", action_b_name="eat", location="公司") is False


def test_trigger_no_social_action():
    """社交场所 + 无社交动作 → 不应该开始对话。"""
    t = DialogueTrigger()
    assert t.should_start(action_a_name="sleep", action_b_name="sleep", location="客厅") is False


def test_trigger_probability_gate_blocks_when_random_high():
    """概率门:random > 0.3 → 不触发。"""
    t = DialogueTrigger()
    with patch("dialogue_generator.trigger.random.random", return_value=0.9):
        assert t.should_start(action_a_name="idle", action_b_name="idle", location="客厅") is False


def test_trigger_probability_gate_allows_when_random_low():
    """概率门:random < 0.3 → 触发。"""
    t = DialogueTrigger()
    with patch("dialogue_generator.trigger.random.random", return_value=0.1):
        assert t.should_start(action_a_name="idle", action_b_name="idle", location="客厅") is True


def test_trigger_default_probability_is_30_percent():
    """默认概率必须 = 0.3(防止有人误改成 100% 刷屏)。"""
    assert DIALOGUE_TRIGGER_PROBABILITY == 0.3
