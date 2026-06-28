from datetime import datetime


class TickScheduler:
    DAY_INTERVAL = 60  # 秒
    NIGHT_INTERVAL = 300  # 秒

    def interval_for(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        h = now.hour
        if h >= 23 or h < 6:
            return self.NIGHT_INTERVAL
        return self.DAY_INTERVAL

    def is_night(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        return now.hour >= 23 or now.hour < 6