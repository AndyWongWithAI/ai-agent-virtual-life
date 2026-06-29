# AI 智能体虚拟空间生活

[![CI](https://github.com/AndyWongWithAI/ai-agent-virtual-life/actions/workflows/ci.yml/badge.svg)](https://github.com/AndyWongWithAI/ai-agent-virtual-life/actions/workflows/ci.yml)

LLM 驱动的虚拟小镇,智能体 24/7 在跑,观察者通过 2D 顶视图围观。

5 个性格各异的 agent(李四/王五/张伟/刘娜/陈雷)住在一个 5 地点小镇里
(客厅 / 厨房 / 公园 / 李四家 / 王五家),按真实时钟作息、彼此偶遇就聊天。
观察者打开浏览器就能围观他们 24/7 的「生活」。

---

## 架构

按 CLAUDE.md 三大原则分层(高内聚低耦合 / 复用 / 资产):

- **L1 平台层**(可复用资产,跨项目可移植)
  - `llm-client`:OpenAI 兼容 LLM 调用 + 限速 + 重试 + 降级 + 成本跟踪
  - `memory-reflection`:短期/长期记忆 + 反思摘要
  - `event-bus`:Redis 发布-订阅总线
  - `agent-runtime`:感知-决策-行动循环 + 作息锁
- **L2 能力层**(业务能力,本项目域)
  - `virtual-world-engine`:5 地点世界 + 邻接关系 + 真实时钟
  - `agent-behavior-orchestrator`:tick 调度器(白天 60s / 夜间 300s)
  - `dialogue-generator`:对话触发判断 + LLM 对话生成
  - `event-memory-system`:Postgres append-only 事件表 + dialogue 表
- **L3 应用层**(本项目专用,不可复用)
  - `apps/town`:FastAPI 装配 + 主页 + WebSocket
  - `apps/town/src/town/static/`:HTML + CSS + 2D 顶视图 canvas

每层只能引用下一层,不允许循环依赖。

---

## 部署

### 前置

| 工具 | 版本 | 备注 |
|------|------|------|
| Python | ≥ 3.12 |  |
| uv | 最新版 | 替代 pip/venv,管理整个 workspace |
| Docker + Docker Compose | 最新 | 起 Postgres + Redis |
| MiniMax M3 API Key | — | 从 `~/.claude/secrets.json` 的 `minimax_coding_plan.api_key` 拿 |

> **注意**:本项目 LLM **不是** Anthropic,统一走 MiniMax M3(OpenAI 兼容接口)。

### 步骤

```bash
# 1. 启动基础设施(Postgres 5433 + Redis 6380,见下「端口说明」)
docker compose -f infra/docker-compose.yml up -d

# 2. 复制 env 模板
cp apps/town/.env.example apps/town/.env
# 编辑 apps/town/.env,把 MINIMAX_API_KEY 填成你的真实 key

# 3. uv 同步所有 workspace 包(8 个 L1/L2 + apps/town)
uv sync

# 4. 启动 town(另一个终端保持运行)
cd apps/town
uv run uvicorn town.main:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000

### 端口说明

为了避免和本机其他项目冲突,docker compose 用了非默认端口:

| 服务 | 容器内端口 | Host 端口 | 原因 |
|------|----------|----------|------|
| Postgres | 5432 | **5433** | 本机 5432 已被 babybus 项目占用 |
| Redis | 6379 | **6380** | 防止与其他项目冲突 |

容器内端口保持标准值,只改 host 端口映射。

### 环境变量

完整 `.env` 模板见 `apps/town/.env.example`:

```bash
# === MiniMax M3 LLM 配置 ===
MINIMAX_API_KEY=sk-cp-your-key-here   # 必填
MINIMAX_BASE_URL=https://api.minimax.chat/v1   # OpenAI 兼容端点
MINIMAX_MODEL=MiniMax-M3              # 模型名
LLM_DAILY_BUDGET_CNY=20               # 日预算(单位 CNY)

# === 基础设施(端口 5433 / 6380) ===
REDIS_URL=redis://localhost:6380/0
DATABASE_URL=postgresql+asyncpg://town:town_dev_pwd@localhost:5433/town
```

> **不要把 `.env` 提交到 git**(已在 .gitignore 中)。

### 关闭 / 重置

```bash
# 停止 web server:Ctrl+C
# 停止基础设施
docker compose -f infra/docker-compose.yml down

# 重置数据库(清空 events / dialogues,人物设还在 personas.yaml)
docker compose -f infra/docker-compose.yml down -v
docker compose -f infra/docker-compose.yml up -d
```

---

## MVP 验收对照

按 brief 中 5 条验收标准 (V1-V5),验证方法全部指向 `scripts/verify_v1_to_v5.sh`:

| # | 验收标准 | 验证方法 | 自动化 |
|---|---------|---------|--------|
| V1 | 5 智能体在动 | `curl -s http://localhost:8000/api/agents \| jq` 看到 5 个对象(id ∈ lisi/wangwu/zhangwei/liuna/chenlei);浏览器 Canvas 上 5 个彩色圆点 | E2E `tests/test_e2e_v1_list_agents.py` 覆盖 API;UI 需手动 |
| V2 | 点开看状态/记忆 | 浏览器点李四的圆点 → 弹窗显示人设 + 最近 N 条 events(注:plan 中 panel.js 暂用 alert 占位,完整面板在 Plan 2 补) | 手动(panel UI 暂未实现) |
| V3 | 围观 LLM 对话 | 浏览器看 Canvas 上偶发的 💬 对话气泡(同位置两人);可在 psql 查 `dialogues` 表 | E2E 覆盖 bootstrap + dialogue trigger |
| V4 | 跨会话记得 | 关 1h 再开,agent 还在做事(decide 输出随时间变化);`psql` 查 `SELECT count(*) FROM events;` 应持续增长 | 手动(自动化跨长时测试) |
| V5 | 发指令响应 | 指令面板输入「李四去买菜」并提交,下一 tick(60s 周期)李四的圆点应移动到厨房 | 手动(指令面板 UI + endpoint 在 Plan 2 补) |

**一键手动验收**:

```bash
bash scripts/verify_v1_to_v5.sh
# 按提示逐步操作,最后会输出 V1[ ] V2[ ] V3[ ] V4[ ] V5[ ] 打勾
```

> **已知 GAP(plan 范围内未实现,需要 Plan 2 补)**:
> - 完整 `panel.js`(V2 点开面板)
> - 跨长时自动化测试(V4)
> - 指令面板 UI + 后端 endpoint(V5)
>
> 短期可用 alert 占位,跑通主流程。

---

## 开发

```bash
# 跑所有单测(各 L1/L2 package 自带 tests/)
uv run pytest

# 跑 town E2E
cd apps/town && uv run pytest tests/

# 改 frontend
# apps/town/src/town/static/{index.html, style.css, canvas.js}
# 改完直接刷新浏览器,FastAPI 直接 serve 静态文件
```

### 目录结构

```
ai-agent-virtual-life/
├── apps/
│   └── town/              # L3 FastAPI 应用
│       ├── src/town/
│       │   ├── main.py            # FastAPI 入口 + tick loop
│       │   ├── bootstrap.py       # 装配
│       │   ├── personas.yaml      # 5 智能体人设
│       │   └── static/            # 2D 前端
│       └── tests/                 # E2E
├── packages/             # L1/L2 8 个 workspace 包
│   ├── llm-client/
│   ├── memory-reflection/
│   ├── event-bus/
│   ├── agent-runtime/
│   ├── virtual-world-engine/
│   ├── agent-behavior-orchestrator/
│   ├── dialogue-generator/
│   └── event-memory-system/
├── infra/
│   └── docker-compose.yml   # Postgres 5433 + Redis 6380
├── scripts/
│   └── verify_v1_to_v5.sh   # MVP 手动验收脚本
├── pyproject.toml           # uv workspace 根
└── README.md                # 本文件
```

---

## 文档

- 设计:`~/.claude/specs/designs/2026-06-29-ai-agent-virtual-life-design.md`
- 计划:`~/.claude/specs/plans/2026-06-29-ai-agent-virtual-life.md`
- 架构平台 REQ:`9d88acbf-6990-43e8-9163-5c5ea9ece30d`
