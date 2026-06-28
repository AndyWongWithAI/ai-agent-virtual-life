#!/usr/bin/env bash
# 手动验证 V1-V5 验收脚本
# 用法: bash scripts/verify_v1_to_v5.sh
# 前提:docker compose 已起(infra/docker-compose.yml:Redis 6380、Postgres 5433)
# 不会真启动 web server(由用户手动起),只列操作步骤

set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "=== AI 智能体虚拟小镇 — 手动 V1-V5 验收 ==="
echo ""
echo "端口配置(本机):"
echo "  Postgres: localhost:5433"
echo "  Redis:    localhost:6380"
echo "  Web:      localhost:8000"
echo ""

# V0: 前置检查
echo "--- V0 前置检查 ---"
echo "[1] 启动 docker compose (Postgres 5433 + Redis 6380):"
echo "    docker compose -f $ROOT/infra/docker-compose.yml up -d"
echo ""
echo "[2] 复制 .env 模板(把 MINIMAX_API_KEY 改成你申请到的 MiniMax key,不要用 ANTHROPIC):"
echo "    cp $ROOT/.env.example $ROOT/apps/town/.env"
echo "    # 编辑 apps/town/.env,确保 MINIMAX_API_KEY=<你的真实 key>"
echo ""

# V1
echo "--- V1 验收:5 个 agent 可见 ---"
echo "[3] 启动 town(另一个终端):"
echo "    cd $ROOT/apps/town"
echo "    uvicorn town.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "[4] 验证 API(另一个终端):"
echo "    curl -s http://localhost:8000/api/agents | jq"
echo "    # 期望:5 个对象,id ∈ {lisi, wangwu, zhangwei, liuna, chenlei}"
echo "    #      name ∈ {李四, 王五, 张伟, 刘娜, 陈雷}"
echo ""

# V3
echo "--- V3 验收:浏览器看到 agent 移动 + 偶尔对话气泡 ---"
echo "[5] 浏览器打开 http://localhost:8000"
echo "    期望:5 个彩色圆点在 5 个地点(客厅/厨房/公园/李四家/王五家)"
echo "    每 60s(白天)agent 移动一次,偶尔看到 💬 对话气泡(同位置两人)"
echo ""

# V4
echo "--- V4 验收:1 小时后 agent 还在做事 ---"
echo "[6] 等 1 小时,刷新浏览器,agent 还在做新的事(decide 输出随时间变化)"
echo "    可查 event_store:psql -h localhost -p 5433 -U town -d town -c 'SELECT count(*) FROM events;'"
echo ""

# V5
echo "--- V5 验收:指令面板('李四去买菜' → 李四去厨房) ---"
echo "[7] 指令面板输入 '李四去买菜' 并提交"
echo "    后续 tick 中:李四的圆点应移动到厨房(下一 60s 周期)"
echo "    可用 psql 查: SELECT agent_id, content FROM events WHERE agent_id='lisi' ORDER BY ts DESC LIMIT 5;"
echo ""

# V2
echo "--- V2 验收:点开李四看面板(人设/最近事件) ---"
echo "[8] 在地图上点李四的圆点"
echo "    期望:弹出面板,显示李四人设 + 最近 N 条 events"
echo "    MVP 阶段可用浏览器 alert 弹窗替代 panel.js"
echo ""

echo "=== 验收完成 ==="
echo "通过/失败打勾: V1[ ] V2[ ] V3[ ] V4[ ] V5[ ]"
