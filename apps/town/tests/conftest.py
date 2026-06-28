"""E2E 测试 fixtures:用 stub ctx 替代真 bootstrap,避免连真 Redis/Postgres/LLM。

背景:
- main.py 的 @app.on_event("startup") 会调 bootstrap(),里面要连 Redis (6379)、
  Postgres (5432)、LLMClient。测试环境没这些服务(本机端口 5433/6380)。
- 替代方案:monkey-patch town.main.bootstrap,返回一个固定 dict,里面 5 个 agent
  起始位置与 personas.yaml 一致;bus 用 fake(EventBus 不连真 Redis,直接调 handler)。
- 这样 ASGITransport(client) 启动时 startup 也不会真去连外部服务,测试稳定可跑。
"""
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport

# 测试环境 env(避免真 LLM 客户端连不上)
import os
os.environ.setdefault("MINIMAX_API_KEY", "test-fake-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://town:town_dev_pwd@localhost:5433/town")


class FakeBus:
    """最小 EventBus 替身:不连真 Redis,只存 handler 列表供 publish 同步派发。"""

    def __init__(self):
        self.handlers: dict[str, list] = {}
        self.redis = None  # bootstrap 引用了 bus.redis,占位即可

    def subscribe(self, topic, handler):
        t = topic.value if hasattr(topic, "value") else topic
        self.handlers.setdefault(t, []).append(handler)

    async def publish(self, topic, payload):
        t = topic.value if hasattr(topic, "value") else topic
        for h in self.handlers.get(t, []):
            r = h(payload)
            if asyncio.iscoroutine(r):
                await r

    async def run_forever(self):
        # 真实服务里后台跑;测试用 stub,启动后立即返回
        await asyncio.sleep(0)


def _make_stub_ctx():
    """构造与 bootstrap() 等价的 dict(只装 main.py /api/agents 用到的字段)。

    - personas:从 personas.yaml 读
    - agents:5 个对象(只占位,主路由不读 agent 内部)
    - world:已 place 5 个 agent 起始位置
    - bus / event_store / trigger / dialogue_gen:都是 stub
    """
    from pathlib import Path
    import yaml
    from virtual_world_engine import World

    personas = yaml.safe_load(
        Path(__file__).parent.parent.joinpath("src/town/personas.yaml").read_text(
            encoding="utf-8"
        )
    )["agents"]

    world = World()
    agents: dict = {}
    for p in personas:
        world.place(p["id"], p["start_location"])
        agents[p["id"]] = type("A", (), {"name": p["name"], "persona": p["persona"]})()

    # 其余字段 main.py 路由用不到,装个空 stub 即可
    class _Stub:
        async def append(self, **kw): return 1
        async def create_dialogue(self, loc): return 1
        async def add_dialogue_message(self, *a, **kw): pass
        async def aclose(self): pass

    return {
        "personas": personas,
        "agents": agents,
        "world": world,
        "bus": FakeBus(),
        "event_store": _Stub(),
        "trigger": type("T", (), {"should_start": lambda self, **kw: False})(),
        "dialogue_gen": type("D", (), {"generate": lambda self, **kw: asyncio.sleep(0, result=[])})(),
        "llm": None,
        "stm": None,
        "ltm": None,
        "reflector": None,
    }


@pytest.fixture
async def client(monkeypatch):
    """ASGI 测试 client:patch town.main.bootstrap,避免连真外部服务。"""
    # 必须在 import app 之前 patch,所以这里 from import
    from town import main as town_main

    stub_ctx = _make_stub_ctx()

    async def fake_bootstrap():
        return stub_ctx

    # 1) patch bootstrap
    monkeypatch.setattr(town_main, "bootstrap", fake_bootstrap)
    # 2) patch main.bootstrap(因为 startup 闭包从模块级 import 拿引用)
    import town.bootstrap as town_bootstrap
    monkeypatch.setattr(town_bootstrap, "bootstrap", fake_bootstrap)

    # 用 lifespan-style startup:httpx AsyncClient + ASGITransport 触发 startup 事件
    transport = ASGITransport(app=town_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # 手动触发 startup(ASGITransport 默认不发 lifespan)
        # 直接调 app.router.startup,等价 on_event("startup")
        for handler in town_main.app.router.on_startup:
            await handler()
        try:
            yield c
        finally:
            for handler in town_main.app.router.on_shutdown:
                await handler()
