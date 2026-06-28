"""行动数据类:Action 表示一个智能体决定要做的事"""
from dataclasses import dataclass


@dataclass
class Action:
    name: str  # "go_to"|"talk_to"|"eat"|"sleep"|"work"|"idle"
    target: str | None = None
    params: dict | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "target": self.target, "params": self.params or {}}