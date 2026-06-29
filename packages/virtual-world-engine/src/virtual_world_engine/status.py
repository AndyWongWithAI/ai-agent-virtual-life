"""agent 4 维状态命名规范(任务 #114)

设计原则(CLAUDE.md):
- 内部 key 用英文(稳定,跨项目可复用 L1 资产)
- label 走 i18n 字典(中文默认,可扩展英文/日文等)
- 所有上层(decision / API / 前端)都按 schema 取,不硬编码中文

Schema(4 维):
- hunger(饥饿度,0-100,数值越大越饿)
- fatigue(疲劳度,0-100,数值越大越累)
- loneliness(孤独度,0-100,数值越大越孤独)
- happiness(快乐度,0-100,数值越大越快乐)

注意 4 个维度方向不一致(hunger/fatigue/loneliness 是"越低越好",
happiness 是"越高越好"),LLM prompt 要明确。
"""
from typing import TypedDict


# 4 维状态内部 key 名(英文,稳定)
STATUS_KEYS: tuple[str, ...] = ("hunger", "fatigue", "loneliness", "happiness")

# 初始值(任务 #113):中性状态,50 ± 10 区间,既不极端
INITIAL_STATUS: dict[str, int] = {
    "hunger": 50,        # 中等饿
    "fatigue": 30,       # 较精神(刚睡醒)
    "loneliness": 40,    # 中等
    "happiness": 60,     # 中等偏上
}

# 时间衰减/动作影响的数值(任务 #113,经过调参)
# 每个 tick 给所有 agent 累加
TICK_DECAY: dict[str, int] = {
    "hunger": 3,         # 每 tick +3 饿
    "fatigue": 2,        # +2 累
    "loneliness": 2,     # +2 孤独
    "happiness": -1,     # -1 快乐
}

# 动作影响(apply_action 一次性变化)
ACTION_EFFECTS: dict[str, dict[str, int]] = {
    "eat":     {"hunger": -40, "happiness": 5},
    "sleep":   {"fatigue": -60},
    "talk_to": {"loneliness": -25, "happiness": 10},
    "work":    {"fatigue": 20, "happiness": -5},
    "go_to":   {},  # 单纯移动不直接改变状态(邻接效应通过 dialogue 体现)
    "idle":    {},
}

# 中文 label 默认映射(V2 前端展示用)
LABELS_ZH: dict[str, str] = {
    "hunger": "饱",
    "fatigue": "累",
    "loneliness": "孤独",
    "happiness": "快乐",
}

# 英文 label(预留 i18n)
LABELS_EN: dict[str, str] = {
    "hunger": "Hunger",
    "fatigue": "Fatigue",
    "loneliness": "Loneliness",
    "happiness": "Happiness",
}


class StatusBar(TypedDict):
    """4 维状态条的强类型(0-100 整数)"""
    hunger: int
    fatigue: int
    loneliness: int
    happiness: int


def get_labels(lang: str = "zh") -> dict[str, str]:
    """按语言返回 label 映射"""
    if lang == "zh":
        return LABELS_ZH
    if lang == "en":
        return LABELS_EN
    raise ValueError(f"unsupported lang: {lang}, supported: ['zh', 'en']")


def format_for_prompt(status: dict, lang: str = "zh") -> str:
    """LLM prompt 用的可读字符串,例如 '饱 70, 累 40, 孤独 30, 快乐 60'

    LLM 对中文字段理解更直接,默认中文;英文场景传 lang='en'。
    """
    labels = get_labels(lang)
    return ", ".join(f"{labels[k]} {status[k]}" for k in STATUS_KEYS)
