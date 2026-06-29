from .space import DEFAULT_LOCATIONS
from .adjacency import neighbors
from .clock import WorldClock
from .status import INITIAL_STATUS, TICK_DECAY, ACTION_EFFECTS, STATUS_KEYS


def _clamp(v: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, v))


class World:
    def __init__(self):
        self.clock = WorldClock()
        self._places: dict[str, str] = {}  # agent_id -> location
        # 任务 #113:World 拥有 4 维状态(单进程内存,SSOT)
        self._statuses: dict[str, dict[str, int]] = {}

    def _ensure_status(self, agent_id: str) -> dict[str, int]:
        """agent 首次出现时初始化为 INITIAL_STATUS(中性,克隆避免共享)"""
        if agent_id not in self._statuses:
            self._statuses[agent_id] = dict(INITIAL_STATUS)
        return self._statuses[agent_id]

    def place(self, agent_id: str, location: str):
        assert location in DEFAULT_LOCATIONS, f"Unknown location: {location}"
        self._places[agent_id] = location
        # 登记该 agent 的状态(首次出现初始化)
        self._ensure_status(agent_id)

    def location_of(self, agent_id: str) -> str:
        return self._places.get(agent_id, "李四家")  # 默认起始位置

    def neighbors_of(self, agent_id: str) -> list[str]:
        """同位置的其他 agent 名 + 该位置可去的地方"""
        my_loc = self.location_of(agent_id)
        co_residents = [a for a, loc in self._places.items() if loc == my_loc and a != agent_id]
        return co_residents + neighbors(my_loc)

    def apply_action(self, agent_id: str, action_name: str) -> None:
        """任务 #113:agent 做某动作时,直接调整其 4 维状态(一次性变化)。

        设计:World 是状态所有者(SSOT),不依赖外部调用方。
        action_name 来自 agent.decide() 的 Action.name,eat/sleep/talk_to
        都有正向调整;work 是负向;go_to/idle 不直接变(衰减在 tick_decay 中)。

        clamp 到 0-100,所有值非负。
        """
        s = self._ensure_status(agent_id)
        effects = ACTION_EFFECTS.get(action_name, {})
        for k, delta in effects.items():
            if k in s:
                s[k] = _clamp(s[k] + delta)

    def tick_decay(self) -> None:
        """任务 #113:每个 tick 给所有 agent 累加(模拟时间流逝)。

        hunger/fatigue/loneliness 涨,happiness 略降。
        tick_loop 每次跑 run_tick 前调一次(在 main.py 接管)。
        """
        for aid in self._statuses:
            for k, delta in TICK_DECAY.items():
                self._statuses[aid][k] = _clamp(self._statuses[aid][k] + delta)

    def status_of(self, agent_id: str) -> dict[str, int]:
        """读 agent 当前 4 维状态(0-100),首次读返回 INITIAL_STATUS 副本"""
        return dict(self._ensure_status(agent_id))

    def snapshot(self, agent_id: str) -> dict:
        loc = self.location_of(agent_id)
        return {
            "location": loc,
            "adjacency": self.neighbors_of(agent_id),
            "now_str": self.clock.now_str(),
            "weekday": self.clock.weekday_cn(),
            "weather": "晴",  # MVP 先固定
            # 任务 #114:内部英文 key;任务 #113:真实计算的状态
            "status_bar": self.status_of(agent_id),
            "legal_targets": list(DEFAULT_LOCATIONS),  # AD1/AD7:I2 fix 单一事实源
        }
