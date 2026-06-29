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
  connectWS();
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

  // 画 agent(V2:同时记录点击坐标)
  for (const [id, loc_name] of Object.entries(agentPositions)) {
    const loc = LOCATIONS[loc_name];
    if (!loc) continue;
    const x = loc.x;
    const y = loc.y - 40;
    const radius = 12;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, 2 * Math.PI);
    ctx.fillStyle = agentColors[id];
    ctx.fill();
    ctx.strokeStyle = "#000";
    ctx.stroke();
    ctx.fillStyle = "#000";
    ctx.font = "11px sans-serif";
    ctx.fillText(agentNames[id], x, y - 18);
    // 记录渲染坐标(给点击用)
    agentRenderPositions[id] = { x, y, radius };
  }
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
