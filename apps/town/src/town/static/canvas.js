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

async function init() {
  const resp = await fetch("/api/agents");
  const agents = await resp.json();
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

  // 画 agent
  for (const [id, loc_name] of Object.entries(agentPositions)) {
    const loc = LOCATIONS[loc_name];
    if (!loc) continue;
    ctx.beginPath();
    ctx.arc(loc.x, loc.y - 40, 12, 0, 2 * Math.PI);
    ctx.fillStyle = agentColors[id];
    ctx.fill();
    ctx.strokeStyle = "#000";
    ctx.stroke();
    ctx.fillStyle = "#000";
    ctx.font = "11px sans-serif";
    ctx.fillText(agentNames[id], loc.x, loc.y - 50);
  }
}

function addEvent(text) {
  const div = document.createElement("div");
  div.className = "event";
  div.textContent = new Date().toLocaleTimeString() + " - " + text;
  eventsEl.prepend(div);
  if (eventsEl.children.length > 30) eventsEl.removeChild(eventsEl.lastChild);
}

function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
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
      default:
        // 忽略未知 topic,不刷屏
        break;
    }
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
}

setInterval(() => {
  clockEl.textContent = "现在: " + new Date().toLocaleString("zh-CN");
}, 1000);

init();
