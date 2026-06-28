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
            "status_bar": "饱 70, 累 40, 孤独 30, 快乐 60",  # MVP 占位,后续接真实状态
        }
