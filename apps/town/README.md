# AI 智能体虚拟小镇(town)

> LLM 驱动的虚拟小镇,5 智能体 24/7 在跑,The Sims 游戏化沙盒型。
> 生产地址: **https://life.intelab.cn**

## 📍 快速访问

| 视角 | 地址 | 说明 |
|------|------|------|
| **用户** | https://life.intelab.cn | 5 智能体在跑;点击圆点看状态条;底部发指令;右下角看反思摘要 |
| **运维健康** | https://life.intelab.cn/health | `{status, agents, ts}` JSON,deploy 用 |
| **运维指标** | https://life.intelab.cn/metrics | Prometheus text format,9 个业务指标 |
| **监控大盘** | http://8.163.80.32:3000 | Grafana,首登 admin/admin |
| **Prometheus** | http://8.163.80.32:9090 | 抓取 town /metrics 已在配 |
| **备份** | ssh root@8.163.80.32 "ls /backup/town/" | 每日 03:00 cron,7 天保留 |

## 🎯 功能一览

- **V1** 5 智能体在 5 地点(李四家/王五家/客厅/厨房/公园)实时移动
- **V2** 点击 agent 弹状态面板:4 维状态(饱/累/孤独/快乐)+ 近期记忆摘要 + 近期 10 事件
- **V3** 2+ agent 同空间 + 30% 概率门 → 触发 LLM 实时生成中文对话
- **V4** 持久化(LTM 反思 + STM 事件)落 Postgres,town 重启不丢记忆
- **V5** 指令面板:导演下指令(例 "李四去买菜")→ 下个 tick agent 自动响应
- **V6** 6h gate 触发反思,LLM 把 6h 事件压缩为摘要,后续 prompt 注入摘要

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────┐
│ town (FastAPI + uvicorn :8001  →  nginx → life.intelab.cn) │
├─────────────────────────────────────────────────────────────┤
│ main.py:run_tick() — 60s 一次(白天)/ 300s 一次(夜间)      │
│   ├─ world.tick_decay()          # 4 维状态时间衰减        │
│   ├─ 5× agent.decide(snap, cmd)  # LLM 决策 + P3 fallback  │
│   ├─ world.apply_action(agent)   # 4 维状态动作变化         │
│   ├─ bus.publish AGENT_DECISION  # 推 WS / 写 STM / metrics │
│   ├─ dialogue trigger → LLM 生成对话(30% 概率 + 5 地点)   │
│   └─ reflector.maybe_reflect()   # 6h gate → LTM + publish │
└─────────────────────────────────────────────────────────────┘
       ↓                                       ↓
   Postgres (events / summaries)         Redis (STM / last_reflect)
```

**L1 平台层 4 个**(最大复用价值):
- `llm-client` — LLM 统一入口(限速/重试/截断容错/日预算)
- `event-bus` — Redis pub/sub + 本地 handler
- `agent-runtime` — 感知→决策→行动循环
- `memory-reflection` — STM/LTM + 6h 反思

**L2 能力层 4 个**(业务能力):
- `virtual-world-engine` — 空间/邻接/4 维状态
- `agent-behavior-orchestrator` — tick 调度
- `dialogue-generator` — 同空间社交触发
- `event-memory-system` — Postgres append-only

**L3 应用层**(本项目):
- `apps/town` — FastAPI server(本仓库)
- 5 智能体初始人设 — `bootstrap.py` 加载
- 2D 顶视图前端 — `static/canvas.js` + `index.html`

## 🔌 API 速查

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 2D 顶视图前端 |
| `/health` | GET | 健康检查(deploy 用) |
| `/metrics` | GET | Prometheus 抓取 |
| `/api/agents` | GET | 5 agent 当前状态(id/name/location) |
| `/api/agents/{id}/status` | GET | 单 agent 状态条 + 反思摘要 + 近期事件 |
| `/api/agents/{id}/commands` | GET | 查指令队列(不消费) |
| `/api/command` | POST | 导演下指令(下个 tick 注入 prompt) |
| `/ws` | WS | 实时推送 AGENT_DECISION / DIALOGUE_* / MEMORY_REFLECT |

**指标 9 个**(Prometheus):
- `town_tick_total` / `town_decisions_total{action}`
- `town_decide_fail_total{agent_id}` / `town_llm_json_repaired_total`
- `town_reflects_total{agent_id}` / `town_reflect_fails_total`
- `town_dialogues_total` / `town_dialogue_fails_total`
- `town_ws_clients` / `town_command_queue_size{agent_id}`

## 🚀 部署流程

1. 改代码 → `git push origin master`
2. GH Actions CI 自动跑(`pytest -m "not production"` 跳过 e2e,105 单测)
3. CI 绿后,Deploy workflow 自动拉 master 到 #1
4. 滚 docker compose(`apps/town` 镜像 build + restart)
5. healthcheck 通过 → 部署完成

**关键约束**(CLAUDE.md):
- **不 SSH 手动 rebuild**(会污染 #1 working tree,后续 GHA 拉 master 冲突)
- town /metrics 端点 5 分钟内应能看到新指标
- town 重启后 LTM/STM 不丢(Postgres + Redis 持久化)

## 🔍 故障排查

### Q1:看不到 agent 移动?

1. 检查 WS:`wscat -c wss://life.intelab.cn/ws`(应返回 101 Switching Protocols)
2. 看 nginx:`ssh root@124.71.219.208 "tail /var/log/nginx/access.log" | grep /ws`
3. 看 town 容器日志:`docker logs infra-app-1 --tail 50`

### Q2:town 容器是 f9236ba 但代码应更新到 7979e09?

按 `feedback-docker-ps-uptime-during-restart` 3 步诊断:
```bash
ssh root@124.71.219.208 "docker inspect infra-app-1 --format '{{.State.StartedAt}}'"
# StartedAt < push 时间 + 5min = 已重启
# StartedAt 早于 push 时间 = GHA 部署失败,去 GH Actions UI re-run
curl https://life.intelab.cn/api/agents/lisi/status
# 看 status_keys 字段是否存在
```

### Q3:CI 报 1 fail 看着像 town bug?

按 `feedback-ci-override-ini-deselects-marker`:
- CI 用 `pytest -m "not production"`(已修)
- production e2e 需要访问公网,只能本地 + nightly cron 跑
- 不要再加 `--override-ini="addopts="`(会清空 marker 过滤)

### Q4:town 日志去哪看?

- **直接看生产**(权威源):`ssh root@124.71.219.208 "docker logs infra-app-1 --tail 200 -f"`
- **本机归档**:#3 `/var/log/town/town.log`(由 `docker-logs-puller.sh` 每 10s 增量拉过来,已运行)
- **Grafana 日志**:`http://8.163.80.32:3000/explore`,Loki 数据源,job="syslog"(本机系统日志) — **town 日志因 promtail 2.9 file glob tailer bug 未直接接入**(详见熔断决策)

**为什么不直连 Loki**:曾试 promtail 2.9 拉 #1 docker logs(走 docker remote API / ssh / file_sd_configs 各种方案),发现:
- 暴露 #1 docker 2375 端口 → 安全风险(任意人可 root 容器)
- ssh + docker logs 拉到 #3 → promtail 2.9 对 pre-existing file 不开 tailer
- 走 town main.py 加 syslog handler → 需 town 重启 + GHA 部署

**务实方案**:**ssh 直接看 #1 容器日志**(SSOT),Loki 留作本机 syslog 监控。town 日志归档通过每日 03:00 backup cron → `/backup/town/town-YYYYMMDD-HHMMSS.tar.gz`(已含 town.log 等关键状态)。

### Q5:4 维状态条一直不变?

按 `feedback-memory-cache-vs-persistent-state`:
- 真实计算是 #113 实现的(commit 4bfd047)
- 旧版本 town 会卡在硬编码 70/40/30/60
- town 启动后第一次 tick 才会调 `world.tick_decay()` 变化

## 📦 备份

每日 03:00 cron 跑 `/opt/services/monitoring/backup-town.sh` → `#3 /backup/town/`:
- 5 agent status JSON(包含 4 维 + 近期事件 + 反思)
- town /metrics 快照
- town git commit
- GH actions 公共 API 拉 town 当前 commit

恢复演练:
```bash
cd /backup/town
tar xzf town-20260629-201702.tar.gz
# 看 status_lisi.json 验数据
```

## 🛠️ 开发工作流

```bash
# 本地单测(快,~9s)
uv run pytest -m "not production" -q

# Production e2e(慢,14s,需访问 life.intelab.cn)
uv run pytest -m "production" -q

# 启动本地 town(需要 .env + docker postgres+redis)
cd apps/town
uv run uvicorn town.main:app --reload --port 8000

# 看 town 指标
curl http://127.0.0.1:8000/metrics | head -20
```

## 📚 相关资源

- **设计文档**:`~/.claude/specs/designs/2026-06-29-ai-agent-virtual-life-design.md`
- **arch 平台 REQ**:`9d88acbf-6990-43e8-9163-5c5ea9ece30d` (阶段 1 complete)
- **arch 平台 REQ**:`378ef1e0-c59a-410c-8d1b-dd9eb66a8ca2` (阶段 2 导演控制台 draft)
- **8 Component 资产**:`https://arch.intelab.cn`(搜 llm-client / agent-runtime / memory-reflection / event-bus / virtual-world-engine / agent-behavior-orchestrator / dialogue-generator / event-memory-system)
- **CLAUDE.md**:`~/.claude/CLAUDE.md`
- **SDLC SOP**:`~/.claude/specs/sdlc/SOP.md`
- **memory 沉淀**(本项目关键):
  - `feedback-docker-ps-uptime-during-restart.md` — 部署诊断 3 步
  - `feedback-ci-override-ini-deselects-marker.md` — CI 加 -m 显式过滤
  - `feedback-memory-cache-vs-persistent-state.md` — 内存缓存 + 吞异常 = 状态卡死
  - `feedback-nginx-http2-websocket-upgrade.md` — nginx + WS 升级
  - `feedback-edit-replace-all-indent-bug.md` — Edit 替换陷阱

## 🤝 反馈渠道

- **Bug 报告**:arch 平台 → 8 Component 资产 → 任意一个 → `create_feedback`
- **设计疑问**:arch 平台 → `search_components` 查 → 评论区
- **新需求**:arch 平台 → `create_requirement`(type=`new_feature`,引用现有 REQ id)
