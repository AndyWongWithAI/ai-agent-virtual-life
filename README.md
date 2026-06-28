# AI 智能体虚拟空间生活

LLM 驱动的虚拟小镇,智能体 24/7 在跑,观察者通过 2D 顶视图围观。

## 快速开始

```bash
# 1. 启动基础设施
docker compose -f infra/docker-compose.yml up -d

# 2. 复制 env 模板并填 key
cp apps/town/.env.example apps/town/.env
# 编辑 .env,填 MiniMax_API_KEY(从 ~/.claude/secrets.json 的 minimax_coding_plan.api_key 拿)

# 3. uv 同步所有 workspace 包
uv sync

# 4. 启动
cd apps/town && uv run uvicorn town.main:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000

## 文档

- 设计:`~/.claude/specs/designs/2026-06-29-ai-agent-virtual-life-design.md`
- 计划:`~/.claude/specs/plans/2026-06-29-ai-agent-virtual-life.md`
- 架构平台 REQ:`9d88acbf-6990-43e8-9163-5c5ea9ece30d`