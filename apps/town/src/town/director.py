"""导演状态管理 — 暂停/倍速/场景注入的 SSOT 模块(L2)。

所有导演面板相关的可读写状态收敛到本模块 dict,避免散落在 main.py 各处。
线程/异步安全:FastAPI 单进程 + asyncio 单线程,模块级 dict 可直接读写,
不需要 asyncio.Lock(详见 CLAUDE.md 复用原则,本模块是 town 自治状态,非
跨服务共享,故不需要走 event_bus/Redis)。
"""
from typing import Any

_director_state: dict[str, Any] = {
    "paused": False,
    "speed": 1.0,
    "last_scene": None,  # {"kind": str, "content": str, "ts": float}
}


def get_state() -> dict:
    """返回 state 浅拷贝,调用方改不动内部 dict。"""
    return _director_state.copy()


def set_paused(paused: bool) -> None:
    _director_state["paused"] = bool(paused)


def set_speed(factor: float) -> None:
    """倍速档位固定 4 档(0.5/1.0/2.0/4.0),挡外的 raise 防止 typo 静默生效。"""
    if factor not in (0.5, 1.0, 2.0, 4.0):
        raise ValueError(f"factor must be in (0.5, 1.0, 2.0, 4.0), got {factor}")
    _director_state["speed"] = factor


def set_scene(kind: str, content: str) -> None:
    """注入导演场景。run_tick() 头部读 last_scene,append 到 LLM system prompt。"""
    import time
    _director_state["last_scene"] = {
        "kind": str(kind),
        "content": str(content),
        "ts": time.time(),
    }


def clear_scene() -> None:
    """清除已注入的场景。run_tick 注入后可选调用(保留 N tick 设计见 §决策清单)。"""
    _director_state["last_scene"] = None
