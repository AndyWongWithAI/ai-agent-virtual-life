# packages/event-bus/src/event_bus/bus.py
import json
import asyncio
import logging
import redis.asyncio as redis_async

from .topics import Topic

logger = logging.getLogger(__name__)


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

    async def _listen_once(self):
        """单次订阅-消费-pubsub.listen() 循环;抛异常时由 run_forever 重连。"""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(*self.handlers.keys())
        try:
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
                    except Exception:
                        # 静默丢失:publish 已成功,Rabbit/Redis 不会重发;这里至少留 stack
                        logger.exception("handler error on topic %s", t)
        finally:
            try:
                await pubsub.aclose()
            except Exception:
                pass

    async def run_forever(self):
        """Long-running listener loop; 外层 while True 自动重连。"""
        while True:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # pubsub.listen 抛异常(Redis 重启/网络抖动)→ 1s 后重连
                logger.exception("event-bus listener crashed, reconnecting in 1s")
                await asyncio.sleep(1)

    async def aclose(self):
        await self.redis.aclose()
