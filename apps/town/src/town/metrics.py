"""town Prometheus 指标 — Phase 7 运维第 1 步

设计原则(CLAUDE.md 一致性 + 定位稳定性):
- 单文件、单职责:只定义 town 业务指标
- 模块级 Counter/Gauge,无状态
- 在 main.py 的关键节点 inc/set,不耦合具体业务
- 暴露给 /metrics endpoint(Prometheus 抓取)

**模块 reload 安全**:
prometheus_client 默认 registry 严格禁止同名 metric 重复注册。
pytest 在每个测试 fixture 可能 reload town.main → metrics 重新执行顶层
Counter() 调用 → 第二次 register 抛 ValueError。
解决:_get_or_create 包装 Counter/Gauge,先看是否已注册,有就复用,没有就建。
"""
from prometheus_client import REGISTRY, Counter, Gauge


def _get_or_create_counter(name: str, doc: str, labels: list[str] | None = None):
    """安全获取或创建 Counter。

    prometheus_client 默认禁止同名 metric;pytest reload 或同进程多
    实例会触发 ValueError。绕过:用 REGISTRY._names_to_collectors
    检查已注册,直接复用 collector(用其内部 dict 拿到的就是
    prometheus_client 内部同一个对象)。
    """
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    if labels is not None:
        return Counter(name, doc, labels)
    return Counter(name, doc)


def _get_or_create_gauge(name: str, doc: str, labels: list[str] | None = None):
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    if labels is not None:
        return Gauge(name, doc, labels)
    return Gauge(name, doc)


# === Counter(只增) ===
town_tick_total = _get_or_create_counter(
    "town_tick_total",
    "town tick_loop 跑过的 tick 总数",
)

town_decisions_total = _get_or_create_counter(
    "town_decisions_total",
    "agent 决策总数,按 action 名分桶",
    ["action"],
)

town_decide_fail_total = _get_or_create_counter(
    "town_decide_fail_total",
    "agent.decide 失败(P3 fallback 触发),按 agent_id 分桶",
    ["agent_id"],
)

town_reflects_total = _get_or_create_counter(
    "town_reflects_total",
    "Reflector 反思触发成功次数,按 agent_id 分桶",
    ["agent_id"],
)

town_reflect_fails_total = _get_or_create_counter(
    "town_reflect_fails_total",
    "Reflector.maybe_reflect 异常次数",
)

town_dialogues_total = _get_or_create_counter(
    "town_dialogues_total",
    "对话触发次数(LLM 生成 1 条或以上消息)",
)

town_dialogue_fails_total = _get_or_create_counter(
    "town_dialogue_fails_total",
    "dialogue LLM generate 失败次数",
)

town_llm_json_repaired_total = _get_or_create_counter(
    "town_llm_json_repaired_total",
    "llm_client 截断 JSON 自动修复成功次数",
)

# === Gauge(可增可减,反映当前状态) ===
town_ws_clients = _get_or_create_gauge(
    "town_ws_clients",
    "当前活跃 WS 客户端连接数",
)

town_command_queue_size = _get_or_create_gauge(
    "town_command_queue_size",
    "各 agent 当前 pending 指令队列长度",
    ["agent_id"],
)
