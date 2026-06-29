"""任务 #114:状态条 4 维命名规范 + i18n 单测"""
import pytest
from virtual_world_engine import (
    STATUS_KEYS,
    LABELS_ZH,
    LABELS_EN,
    get_labels,
    format_for_prompt,
)


def test_status_keys_are_4_canonical_english_names():
    """STATUS_KEYS 必须稳定英文,4 维"""
    assert STATUS_KEYS == ("hunger", "fatigue", "loneliness", "happiness")


def test_labels_zh_maps_all_4_keys():
    """中文 label 字典必须覆盖全部 4 维"""
    assert set(LABELS_ZH.keys()) == set(STATUS_KEYS)
    assert LABELS_ZH["hunger"] == "饱"
    assert LABELS_ZH["fatigue"] == "累"
    assert LABELS_ZH["loneliness"] == "孤独"
    assert LABELS_ZH["happiness"] == "快乐"


def test_labels_en_maps_all_4_keys():
    """英文 label 字典必须覆盖全部 4 维"""
    assert set(LABELS_EN.keys()) == set(STATUS_KEYS)


def test_get_labels_zh():
    """get_labels('zh') 返回中文 label 字典"""
    labels = get_labels("zh")
    assert labels == LABELS_ZH


def test_get_labels_en():
    """get_labels('en') 返回英文 label 字典"""
    labels = get_labels("en")
    assert labels == LABELS_EN


def test_get_labels_unsupported_raises():
    """不支持的语言抛 ValueError"""
    with pytest.raises(ValueError, match="unsupported lang"):
        get_labels("fr")


def test_format_for_prompt_zh_contains_all_4_pairs():
    """format_for_prompt(zh) 注入 LLM 的可读字符串,例如 '饱 70, 累 40, 孤独 30, 快乐 60'"""
    status = {"hunger": 70, "fatigue": 40, "loneliness": 30, "happiness": 60}
    result = format_for_prompt(status, lang="zh")
    assert "饱 70" in result
    assert "累 40" in result
    assert "孤独 30" in result
    assert "快乐 60" in result
    # 顺序按 STATUS_KEYS
    assert result.index("饱") < result.index("累") < result.index("孤独") < result.index("快乐")


def test_format_for_prompt_en_uses_english_labels():
    """format_for_prompt(en) 用英文 label"""
    status = {"hunger": 70, "fatigue": 40, "loneliness": 30, "happiness": 60}
    result = format_for_prompt(status, lang="en")
    assert "Hunger 70" in result
    assert "Happiness 60" in result
