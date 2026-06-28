# packages/event-bus/src/event_bus/bus.py
import json
import asyncio
import redis.asyncio as redis_async

from .topics import Topic


class EventBus:
    def __init__(self, redis_url: str):
        self.redis = redis_async.from_url(redis_url)
        self.handlers: dict[str, list] = {}

    async def publish(self, topic: Topic | str, payload: dict):
        t = topic.value if isinstance(topic, Topic) else topic
        await self.redis.publish(t, json.dumps(payload, ensure_ascii=False, default=str))

    def subscribe(self, topic: Topic | str, handler):
        """handler(payload: dict) -> None or async coroutine"""
        t = topic.value if isinstance(topic, Topic) else topic
        self.handlers.setdefault(t, []).append(handler)

    async def run_forever(self):
        """Long-running listener loop; each handler runs in its own task"""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(*self.handlers.keys())
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            t = msg["channel"].decode() if isinstance(msg["channel"], bytes) else msg["channel"]
            data = json.loads(msg["data"])
            for h in self.handlers.get(t, []):
                try:
                    r = h(data)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception as e:
                    print(f"[event-bus] handler error on {t}: {e}")

    async def aclose(self):
        await self.redis.aclose()
