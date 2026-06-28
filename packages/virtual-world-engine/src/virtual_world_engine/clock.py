from datetime import datetime


class WorldClock:
    """世界时间 = 系统真实时间"""
    def now(self) -> datetime:
        return datetime.now()

    def now_str(self) -> str:
        return self.now().strftime("%Y-%m-%d %H:%M:%S")

    def weekday_cn(self) -> str:
        return ["一", "二", "三", "四", "五", "六", "日"][self.now().weekday()]
