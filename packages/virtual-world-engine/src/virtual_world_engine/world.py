from .space import DEFAULT_LOCATIONS
from .adjacency import neighbors
from .clock import WorldClock


class World:
    def __init__(self):
        self.clock = WorldClock()
        self._places: dict[str, str] = {}  # agent_id -> location

    def place(self, agent_id: str, location: str):
        assert location in DEFAULT_LOCATIONS, f"Unknown location: {location}"
        self._places[agent_id] = location

    def location_of(self, agent_id: str) -> str:
        return self._places.get(agent_id, "李四家")  # 默认起始位置

    def neighbors_of(self, agent_id: str) -> list[str]:
        """同位置的其他 agent 名 + 该位置可去的地方"""
        my_loc = self.location_of(agent_id)
        co_residents = [a for a, loc in self._places.items() if loc == my_loc and a != agent_id]
        return co_residents + neighbors(my_loc)

    def snapshot(self, agent_id: str) -> dict:
        loc = self.location_of(agent_id)
        return {
            "location": loc,
            "adjacency": self.neighbors_of(agent_id),
            "now_str": self.clock.now_str(),
            "weekday": self.clock.weekday_cn(),
            "weather": "晴",  # MVP 先固定
            # 4 维状态内部 key 用英文(stable),label 走 i18n(任务 #114)
            # 真实计算在任务 #113,目前仍是占位值
            "status_bar": {
                "hunger": 70,
                "fatigue": 40,
                "loneliness": 30,
                "happiness": 60,
            },
            "legal_targets": list(DEFAULT_LOCATIONS),  # AD1/AD7:I2 fix 单一事实源
        }
