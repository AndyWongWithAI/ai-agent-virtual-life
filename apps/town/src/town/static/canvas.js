// apps/town/src/town/static/canvas.js
const LOCATIONS = {
  "李四家":  { x: 100, y: 100, color: "#FFD700" },
  "王五家":  { x: 300, y: 100, color: "#87CEEB" },
  "客厅":   { x: 200, y: 250, color: "#98FB98" },
  "厨房":   { x: 350, y: 300, color: "#FFA07A" },
  "公园":   { x: 600, y: 350, color: "#90EE90" },
};
const AGENT_COLORS = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#FFA07A", "#C39BD3"];

const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const clockEl = document.getElementById("clock");
const eventsEl = document.getElementById("events");

let agentPositions = {};  // agent_id -> location
let agentNames = {};  // agent_id -> name
let agentColors = {};  // agent_id -> color
let memorySummaries = [];  // 最近 5 条 {ts, agent_id, text}
let agentRenderPositions = {};  // V2:点击检测用 {agent_id: {x, y, radius}}

async function init() {
  let agents;
  try {
    const resp = await fetch("/api/agents");
    if (!resp.ok) throw new Error(`/api/agents ${resp.status}`);
    agents = await resp.json();
  } catch (e) {
    addEvent("⚠️ /api/agents 拉取失败:" + e.message + "(地图无 agent 可显示)");
    console.error("[init] /api/agents failed:", e);
    draw();
    return;
  }
  agents.forEach((a, i) => {
    agentPositions[a.id] = a.location;
    agentNames[a.id] = a.name;
    agentColors[a.id] = AGENT_COLORS[i % AGENT_COLORS.length];
  });
  draw();
  // 任务 #125/#126(Bug4/5):启动时拉历史,避免刷新后从 0 开始
  await Promise.all([loadHistoricalEvents(), loadHistoricalMemories()]);
  connectWS();
  await initCommandPanel();
}

// 任务 #125(Bug4):init 拉 /api/events,append 到 events div(asc 顺序,新事件 prepend 到顶部对齐)
async function loadHistoricalEvents() {
  try {
    const resp = await fetch("/api/events?limit=30");
    if (!resp.ok) return;
    const events = await resp.json();
    for (const ev of events) {
      const name = agentNames[ev.agent_id] || ev.agent_id;
      const div = document.createElement("div");
      div.className = "event";
      const t = new Date(ev.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      div.textContent = `${t} - ${name}: [${ev.kind}] ${ev.content}`;
      // asc: 最早在上,新事件 prepend 后仍在上方,保持时序顺序
      eventsEl.appendChild(div);
    }
    while (eventsEl.children.length > 30) eventsEl.removeChild(eventsEl.firstChild);
  } catch (e) {
    console.warn("[init] /api/events failed:", e);
  }
}

// 任务 #126(Bug5):init 拉 /api/memory-summaries,填 memorySummary 然后渲染面板
async function loadHistoricalMemories() {
  try {
    const resp = await fetch("/api/memory-summaries?limit=5");
    if (!resp.ok) return;
    const items = await resp.json();
    // 后端返 desc,前端按 desc 存进头部
    for (const m of items.reverse()) {
      memorySummaries.unshift({
        ts: m.ts,
        agent_id: m.agent_id,
        text: m.text,
      });
    }
    if (memorySummaries.length > 5) memorySummaries.length = 5;
    drawMemoryPanel();
  } catch (e) {
    console.warn("[init] /api/memory-summaries failed:", e);
  }
}

// V5:指令面板 — 填充 agent 下拉 + 绑定事件
async function initCommandPanel() {
  const sel = document.getElementById("command-agent");
  if (!sel) return;
  for (const id in agentNames) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = agentNames[id];
    sel.appendChild(opt);
  }
  document.getElementById("command-send").addEventListener("click", sendCommand);
  document.getElementById("command-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendCommand();
  });
}

// V5:发送指令 — POST /api/command,XSS 防护用 textContent
async function sendCommand() {
  const sel = document.getElementById("command-agent");
  const input = document.getElementById("command-input");
  const status = document.getElementById("command-status");
  const agentId = sel.value;
  const cmd = input.value.trim();
  if (!agentId) { status.textContent = "请先选 agent"; return; }
  if (!cmd) { status.textContent = "指令不能空"; return; }
  status.textContent = "发送中…";
  try {
    const resp = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: agentId, command: cmd }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    status.textContent = `✅ 已排队 (队列长度 ${data.queue_len})`;
    input.value = "";
    addEvent(`📩 给 ${agentNames[agentId]} 下指令:"${cmd}" (位置 ${data.queue_len})`);
  } catch (err) {
    status.textContent = "❌ " + err.message;
    addEvent("❌ 指令发送失败:" + err.message);
  }
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  agentRenderPositions = {};  // V2:每次重绘清空

  // 画地点
  for (const [name, loc] of Object.entries(LOCATIONS)) {
    ctx.fillStyle = loc.color;
    ctx.fillRect(loc.x - 40, loc.y - 25, 80, 50);
    ctx.strokeStyle = "#333";
    ctx.strokeRect(loc.x - 40, loc.y - 25, 80, 50);
    ctx.fillStyle = "#000";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(name, loc.x, loc.y + 5);
  }

  // 任务 #122(Bug1):按 location 分组,同地点 agent 错位排开 ——
  // 之前所有同地点 agent 都画在 (loc.x, loc.y-40),完全重叠,点击只能命中最顶层。
  // 现在按 location 分组后计算 (dx, dy) 偏移;≤3 单行,4-5 两行。
  const byLoc = {};
  for (const id of Object.keys(agentPositions)) {
    const loc_name = agentPositions[id];
    const loc = LOCATIONS[loc_name];
    if (!loc) continue;
    if (!byLoc[loc_name]) byLoc[loc_name] = [];
    byLoc[loc_name].push(id);
  }
  // 同一 location 内按 id 字典序排序,保证布局稳定(刷新/WS 重绘结果一致)
  for (const loc_name in byLoc) byLoc[loc_name].sort();

  for (const [loc_name, ids] of Object.entries(byLoc)) {
    const loc = LOCATIONS[loc_name];
    if (!loc) continue;
    const offsets = computeAgentOffsets(ids.length);
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i];
      const x = loc.x + offsets[i].dx;
      const y = (loc.y - 40) + offsets[i].dy;
      const radius = 12;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
      ctx.fillStyle = agentColors[id];
      ctx.fill();
      ctx.strokeStyle = "#000";
      ctx.stroke();
      ctx.fillStyle = "#000";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(agentNames[id], x, y - 18);
      agentRenderPositions[id] = { x, y, radius };
    }
  }
}

// 任务 #122:同地点 N 个 agent 的偏移(dx, dy)。
// 1→中心;2-3→水平单行;4-5→上下两行(上排 2/3,下排 2)。最多 5 个。
function computeAgentOffsets(N) {
  if (N <= 0) return [];
  if (N === 1) return [{ dx: 0, dy: 0 }];
  if (N === 2) return [{ dx: -22, dy: 0 }, { dx: 22, dy: 0 }];
  if (N === 3) return [{ dx: -44, dy: 0 }, { dx: 0, dy: 0 }, { dx: 44, dy: 0 }];
  if (N === 4) return [
    { dx: -22, dy: -28 }, { dx: 22, dy: -28 },
    { dx: -22, dy: 0 },  { dx: 22, dy: 0 },
  ];
  // N === 5
  return [
    { dx: -44, dy: -28 }, { dx: 0, dy: -28 }, { dx: 44, dy: -28 },
    { dx: -22, dy: 0 },  { dx: 22, dy: 0 },
  ];
}

function addEvent(text) {
  const div = document.createElement("div");
  div.className = "event";
  div.textContent = new Date().toLocaleTimeString() + " - " + text;
  eventsEl.prepend(div);
  if (eventsEl.children.length > 30) eventsEl.removeChild(eventsEl.lastChild);
}

function drawMemoryPanel() {
  const listEl = document.getElementById("memory-list");
  if (!listEl) return;
  listEl.innerHTML = "";
  for (const m of memorySummaries) {
    const div = document.createElement("div");
    div.className = "memory-item";
    const ts = new Date(m.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    div.innerHTML =
      `<span class="ts">[${ts}]</span>` +
      `<span class="agent">${agentNames[m.agent_id] || m.agent_id}:</span>` +
      `<div class="text"></div>`;
    // 用 textContent 防 XSS(虽然后端是 LLM 输出,但仍要防)
    div.querySelector(".text").textContent = m.text;
    listEl.appendChild(div);
  }
}

function connectWS() {
  // HTTPS 下浏览器自动用 wss://,HTTP 下用 ws://
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${scheme}//${location.host}/ws`);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    switch (msg.topic) {
      case "agent.decision": {
        const { agent_id, action } = msg;
        if (action && action.name === "go_to" && LOCATIONS[action.target]) {
          agentPositions[agent_id] = action.target;
        }
        addEvent(
          `${agentNames[agent_id] || agent_id}: ${action ? action.name : "?"} -> ${action ? action.target || "-" : ""}`
        );
        draw();
        break;
      }
      case "dialogue.start": {
        addEvent(
          `🗨️ 对话开始 @ ${msg.location || "?"}(参与者 ${(msg.participants || []).map((id) => agentNames[id] || id).join(" & ")})`
        );
        break;
      }
      case "dialogue.message": {
        addEvent(
          `💬 ${agentNames[msg.agent_id] || msg.agent_id}: ${msg.content || ""}`
        );
        break;
      }
      case "memory.reflect": {
        memorySummaries.unshift({
          ts: msg.period_end || new Date().toISOString(),
          agent_id: msg.agent_id,
          text: msg.text || "(空摘要)",
        });
        if (memorySummaries.length > 5) memorySummaries.length = 5;
        drawMemoryPanel();
        break;
      }
      default:
        // 忽略未知 topic,不刷屏
        break;
    }
  };
  ws.onerror = (e) => {
    addEvent("❌ WebSocket 连接失败,1.5s 后重试…");
    console.error("[ws] error", e);
  };
  ws.onclose = () => setTimeout(connectWS, 1500);
}

setInterval(() => {
  clockEl.textContent = "现在: " + new Date().toLocaleString("zh-CN");
}, 1000);

// V2:点击 canvas 检测是否命中 agent,命中弹面板;空白区域关闭面板
canvas.addEventListener("click", async (e) => {
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  for (const [id, pos] of Object.entries(agentRenderPositions)) {
    const dx = cx - pos.x;
    const dy = cy - pos.y;
    if (dx * dx + dy * dy <= pos.radius * pos.radius) {
      await showAgentPanel(id);
      return;
    }
  }
  // 没点中 agent → 关闭面板
  hideAgentPanel();
});

async function showAgentPanel(agentId) {
  const panel = document.getElementById("agent-panel");
  if (!panel) return;
  panel.classList.remove("hidden");
  try {
    const resp = await fetch(`/api/agents/${encodeURIComponent(agentId)}/status`);
    if (!resp.ok) throw new Error(`/api/agents/${agentId}/status ${resp.status}`);
    const data = await resp.json();
    renderAgentPanel(data);
  } catch (err) {
    addEvent("⚠️ 拉取 agent 状态失败:" + err.message);
    panel.classList.add("hidden");
  }
}

function hideAgentPanel() {
  const panel = document.getElementById("agent-panel");
  if (panel) panel.classList.add("hidden");
}

document.getElementById("agent-panel-close")?.addEventListener("click", hideAgentPanel);

function renderAgentPanel(data) {
  // name / persona / location — textContent 防 XSS
  document.getElementById("agent-panel-name").textContent = data.name;
  document.getElementById("agent-panel-persona").textContent = data.persona;
  document.getElementById("agent-panel-location").textContent = `📍 ${data.location}`;

  // status_bar 是 dict {饱: 70, 累: 40, ...}
  const statusEl = document.getElementById("agent-panel-status");
  statusEl.innerHTML = "";
  for (const [label, value] of Object.entries(data.status_bar)) {
    const item = document.createElement("div");
    item.className = "status-item";
    item.innerHTML =
      `<div class="label"></div>` +
      `<div class="value"></div>` +
      `<div class="status-bar-bg"><div class="status-bar-fill"></div></div>`;
    item.querySelector(".label").textContent = label;
    item.querySelector(".value").textContent = value;
    const fill = item.querySelector(".status-bar-fill");
    fill.style.width = `${Math.max(0, Math.min(100, value))}%`;
    statusEl.appendChild(item);
  }

  // summaries
  const sumEl = document.getElementById("agent-panel-summaries");
  sumEl.innerHTML = "";
  if (!data.recent_summaries.length) {
    sumEl.textContent = "(暂无反思摘要)";
  } else {
    for (const s of data.recent_summaries) {
      const div = document.createElement("div");
      div.className = "summary-item";
      const ts = new Date(s.ts).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
      div.innerHTML = `<span class="ts">[${ts}]</span><span class="text"></span>`;
      div.querySelector(".text").textContent = s.text;
      sumEl.appendChild(div);
    }
  }

  // events
  const evEl = document.getElementById("agent-panel-events");
  evEl.innerHTML = "";
  if (!data.recent_events.length) {
    evEl.textContent = "(暂无事件)";
  } else {
    for (const ev of data.recent_events) {
      const div = document.createElement("div");
      div.className = "event-item";
      const ts = new Date(ev.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      div.innerHTML = `<span class="ts">[${ts}]</span><span class="text"></span>`;
      div.querySelector(".text").textContent = `[${ev.kind}] ${ev.content}`;
      evEl.appendChild(div);
    }
  }
}

init();
