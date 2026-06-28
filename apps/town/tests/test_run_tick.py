"""C3 fix 测试:run_tick 在 3+ 人同位置时触发所有 C(n,2) 对话

用 stub ctx 注入 FakeBus + 可控 trigger + 空 dialogue_gen,断言:
- 3 个 agent 同位置 → 3 个 dialogue
- 5 个 agent 同位置 → 10 个 dialogue
"""
import asyncio
import pytest
from collections import defaultdict

from virtual_world_engine import World, DEFAULT_LOCATIONS


class FakeBus:
    def __init__(self):
        self.handlers: dict[str, list] = {}
        self.redis = None
        self.published: list[tuple[str, dict]] = []

    def subscribe(self, topic, handler):
        t = topic.value if hasattr(topic, "value") else topic
        self.handlers.setdefault(t, []).append(handler)

    async def publish(self, topic, payload):
        t = topic.value if hasattr(topic, "value") else topic
        self.published.append((t, payload))
        for h in self.handlers.get(t, []):
            r = h(payload)
            if asyncio.iscoroutine(r):
                await r

    async def run_forever(self):
        await asyncio.sleep(0)


def _make_ctx(num_agents: int, world: World, trigger_value: bool = True):
    """构造一个 stub ctx,让 5 个(或 num_agents 个) agent 都在同一位置。"""
    from event_bus import Topic

    bus = FakeBus()
    agents = {}
    personas = []
    for i in range(num_agents):
        aid = f"a{i}"
        agents[aid] = type(
            "A", (), {"name": aid, "persona": "test", "decide": _fake_decide}
        )()
        personas.append({"id": aid, "name": aid, "start_location": "客厅"})
        world.place(aid, "客厅")

    last_actions: dict[str, str] = {aid: "idle" for aid in agents}

    class _StubStore:
        def __init__(self):
            self.dialogues: list[tuple[str, list[tuple[str, str]]]] = []
            self.did_counter = 0

        async def append(self, **kw):
            return 1

        async def create_dialogue(self, location):
            self.did_counter += 1
            return self.did_counter

        async def add_dialogue_message(self, did, speaker, content):
            self.dialogues.append(("msg", (did, speaker, content)))

        async def aclose(self):
            pass

    store = _StubStore()

    class _StubGen:
        async def generate(self, **kw):
            return [(kw["a_name"], "hi"), (kw["b_name"], "yo")]

    class _StubTrigger:
        def should_start(self, **kw):
            return trigger_value

    return {
        "personas": personas,
        "agents": agents,
        "world": world,
        "bus": bus,
        "event_store": store,
        "trigger": _StubTrigger(),
        "dialogue_gen": _StubGen(),
    }, store


async def _fake_decide(self, snap):
    from agent_runtime.actions import Action

    return Action(name="idle", target=None)


@pytest.mark.asyncio
async def test_run_tick_triggers_all_pairs_for_three_occupants():
    """C3:3 人同位置 → 触发 3 个 dialogue(C(3,2)=3)"""
    from town.main import run_tick

    world = World()
    ctx, store = _make_ctx(num_agents=3, world=world, trigger_value=True)
    # 注入到 main 模块
    import town.main as town_main

    town_main.ctx = ctx
    try:
        await run_tick()
        # 3 dialogue starts (DIALOGUE_START published)
        dialogue_starts = [p for p in ctx["bus"].published if p[0] == "dialogue.start"]
        assert len(dialogue_starts) == 3, f"expected 3 dialogue.start, got {len(dialogue_starts)}"
    finally:
        town_main.ctx = None


@pytest.mark.asyncio
async def test_run_tick_triggers_all_pairs_for_five_occupants():
    """C3:5 人同位置 → 触发 10 个 dialogue(C(5,2)=10)"""
    from town.main import run_tick

    world = World()
    ctx, store = _make_ctx(num_agents=5, world=world, trigger_value=True)
    import town.main as town_main

    town_main.ctx = ctx
    try:
        await run_tick()
        dialogue_starts = [p for p in ctx["bus"].published if p[0] == "dialogue.start"]
        assert len(dialogue_starts) == 10, f"expected 10 dialogue.start, got {len(dialogue_starts)}"
    finally:
        town_main.ctx = None


@pytest.mark.asyncio
async def test_run_tick_no_dialogue_when_trigger_false():
    """trigger.should_start=False → 0 dialogue(组合遍历不影响,只是 trigger gate)"""
    from town.main import run_tick

    world = World()
    ctx, store = _make_ctx(num_agents=4, world=world, trigger_value=False)
    import town.main as town_main

    town_main.ctx = ctx
    try:
        await run_tick()
        dialogue_starts = [p for p in ctx["bus"].published if p[0] == "dialogue.start"]
        assert len(dialogue_starts) == 0
    finally:
        town_main.ctx = None
