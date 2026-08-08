/* AgentMind WebUI client — WebSocket chat with live streaming & tool cards */

const els = {
  chat: document.getElementById("chat"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  sessions: document.getElementById("session-list"),
  newChat: document.getElementById("new-chat"),
  status: document.getElementById("status"),
  statusText: document.getElementById("status-text"),
  sessionName: document.getElementById("current-session"),
  meta: document.getElementById("meta"),
  memoryBtn: document.getElementById("memory-btn"),
  memoryDrawer: document.getElementById("memory-drawer"),
  memoryBody: document.getElementById("memory-body"),
  memoryClose: document.getElementById("memory-close"),
  memoryClear: document.getElementById("memory-clear"),
  approvalModal: document.getElementById("approval-modal"),
  approvalTool: document.getElementById("approval-tool"),
  approvalPending: document.getElementById("approval-pending"),
  approvalAllow: document.getElementById("approval-allow"),
  approvalDeny: document.getElementById("approval-deny"),
};

let ws = null;
let sessionId = null;
let sessions = [];
let currentAssistantEl = null;
let thinkingEl = null;
let toolCards = [];
let approvalQueue = [];
let currentApproval = null;

/* ---------------- WebSocket ---------------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  setStatus("connecting");

  ws.onopen = () => {
    setStatus("connected");
    ws.send(JSON.stringify({ type: "hello", session_id: sessionId }));
  };

  ws.onmessage = (evt) => {
    const { event, payload } = JSON.parse(evt.data);
    handleEvent(event, payload);
  };

  ws.onclose = () => { setStatus("disconnected"); setTimeout(connect, 2000); };
  ws.onerror = () => ws.close();
}

function setStatus(state) {
  els.status.className = "status " + state;
  els.statusText.textContent =
    state === "connected" ? "已连接" :
    state === "connecting" ? "连接中..." : "已断开，重连中...";
}

/* ---------------- Event handling ---------------- */
function handleEvent(event, payload) {
  switch (event) {
    case "welcome":
      sessionId = payload.session_id;
      sessions = payload.sessions;
      els.meta.textContent = `model: ${payload.model}`;
      renderSessions();
      renderHistory();
      break;
    case "thinking_start":
      hideThinking();
      thinkingEl = addThinking();
      break;
    case "thinking_end":
      hideThinking();
      break;
    case "delta":
      ensureAssistant().textContent += payload.text;
      scrollToBottom();
      break;
    case "tool_start":
      toolCards.push(addToolCard(payload.name, payload.arguments, "运行中..."));
      break;
    case "tool_end":
      const card = toolCards.pop();
      if (card) card.finish(payload.output);
      hideThinking();
      break;
    case "subagent_start":
      addSubagentCard(payload.task);
      break;
    case "subagent_end":
      finalizeSubagent(payload.result, payload.success);
      break;
    case "approval_request":
      approvalQueue.push(payload);
      showNextApproval();
      break;
    case "approval_result":
      if (currentApproval && currentApproval.approval_id === payload.approval_id) {
        currentApproval = null;
        els.approvalModal.classList.add("hidden");
        showNextApproval();
      }
      break;
    case "done":
      finalizeAssistant(payload.answer);
      refreshSessions();
      break;
    case "error":
      hideThinking();
      ensureAssistant();
      currentAssistantEl.innerHTML =
        `<span class="error-text">⚠ ${escapeHtml(payload.message)}</span>`;
      break;
  }
}

/* ---------------- DOM helpers ---------------- */
function addThinking() {
  const el = document.createElement("div");
  el.className = "thinking";
  el.innerHTML = `<span class="spinner"></span><span>思考中...</span>`;
  els.chat.appendChild(el);
  scrollToBottom();
  return el;
}
function hideThinking() { if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; } }

function ensureAssistant() {
  if (!currentAssistantEl) {
    currentAssistantEl = document.createElement("div");
    currentAssistantEl.className = "msg assistant";
    currentAssistantEl.innerHTML = `<span class="role">AgentMind</span><div class="bubble"></div>`;
    els.chat.appendChild(currentAssistantEl);
    scrollToBottom();
  }
  return currentAssistantEl.querySelector(".bubble");
}
function finalizeAssistant(answer) {
  if (currentAssistantEl) { currentAssistantEl.remove(); }
  currentAssistantEl = null;
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<span class="role">AgentMind</span><div class="bubble"></div>`;
  el.querySelector(".bubble").textContent = answer;
  els.chat.appendChild(el);
  scrollToBottom();
}

function addToolCard(name, args, status) {
  const el = document.createElement("div");
  el.className = "tool-card open";
  el.innerHTML = `
    <div class="tool-head">
      <span class="caret">▶</span>
      <span class="tool-name">${escapeHtml(name)}</span>
      <span class="tool-args">${escapeHtml(args || "")}</span>
      <span class="tool-status">${escapeHtml(status)}</span>
    </div>
    <div class="tool-body"><div class="tool-output"></div></div>`;
  el.querySelector(".tool-head").onclick = () => el.classList.toggle("open");
  els.chat.appendChild(el);
  scrollToBottom();

  return {
    finish(output) {
      el.querySelector(".tool-output").textContent = output || "(空)";
      el.querySelector(".tool-status").textContent = "完成";
      el.classList.add("done");
      scrollToBottom();
    },
  };
}

function scrollToBottom() { els.chat.scrollTop = els.chat.scrollHeight; }

/* ---------------- Subagent cards ---------------- */
function addSubagentCard(task) {
  const el = document.createElement("div");
  el.className = "subagent-card open";
  el.dataset.placeholder = "";
  el.innerHTML = `
    <div class="subagent-head">
      <span class="caret">▶</span>
      <span class="subagent-label">子代理</span>
      <span class="subagent-task">${escapeHtml(task)}</span>
      <span class="subagent-status">运行中...</span>
    </div>
    <div class="subagent-body"><div class="subagent-result">...</div></div>`;
  el.querySelector(".subagent-head").onclick = () => el.classList.toggle("open");
  els.chat.appendChild(el);
  scrollToBottom();
  return el;
}

function finalizeSubagent(result, success) {
  const cards = [...els.chat.querySelectorAll(".subagent-card")];
  const el = cards[cards.length - 1];
  if (!el) return;
  el.querySelector(".subagent-result").textContent = result || "(空)";
  el.querySelector(".subagent-status").textContent = success ? "完成" : "失败";
  el.classList.add("done");
  scrollToBottom();
}

/* ---------------- Approval ---------------- */
function showNextApproval() {
  if (currentApproval) return;
  const next = approvalQueue.shift();
  if (!next) { els.approvalModal.classList.add("hidden"); return; }
  currentApproval = next;
  els.approvalTool.textContent = `${next.tool}(${next.arguments || "{}"})`;
  els.approvalPending.textContent = approvalQueue.length
    ? `还有 ${approvalQueue.length} 个请求在排队`
    : "";
  els.approvalModal.classList.remove("hidden");
}

function respondApproval(approved) {
  if (!currentApproval) return;
  ws.send(JSON.stringify({ type: "approval", approval_id: currentApproval.approval_id, approved }));
  currentApproval = null;
  els.approvalModal.classList.add("hidden");
  showNextApproval();
}

/* ---------------- Sessions ---------------- */
function renderSessions() {
  els.sessions.innerHTML = "";
  for (const s of sessions) {
    const li = document.createElement("li");
    li.textContent = `${s.title} (${s.message_count})`;
    li.className = s.id === sessionId ? "active" : "";
    li.onclick = () => switchSession(s.id);
    els.sessions.appendChild(li);
  }
  const cur = sessions.find((s) => s.id === sessionId);
  els.sessionName.textContent = cur ? cur.title : "新对话";
}

function switchSession(id) {
  sessionId = id;
  ws.send(JSON.stringify({ type: "hello", session_id: id }));
  renderSessions();
  renderHistory();
}

async function renderHistory() {
  els.chat.innerHTML = "";
  currentAssistantEl = null;
  toolCards = [];
  if (!sessionId) return;
  try {
    const res = await fetch(`/api/sessions/${sessionId}/messages`);
    const { messages } = await res.json();
    for (const m of messages) {
      if (m.role === "user") {
        const el = document.createElement("div");
        el.className = "msg user";
        el.innerHTML = `<span class="role">你</span><div class="bubble"></div>`;
        el.querySelector(".bubble").textContent = m.content;
        els.chat.appendChild(el);
      } else if (m.role === "assistant") {
        const el = document.createElement("div");
        el.className = "msg assistant";
        el.innerHTML = `<span class="role">AgentMind</span><div class="bubble"></div>`;
        el.querySelector(".bubble").textContent = m.content;
        els.chat.appendChild(el);
      }
    }
  } catch (e) { /* ignore */ }
  scrollToBottom();
}

function refreshSessions() { ws.send(JSON.stringify({ type: "hello", session_id: sessionId })); }

/* ---------------- Send ---------------- */
function send() {
  const text = els.input.value.trim();
  if (!text || !ws || ws.readyState !== 1) return;

  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<span class="role">你</span><div class="bubble"></div>`;
  el.querySelector(".bubble").textContent = text;
  els.chat.appendChild(el);

  els.input.value = "";
  autoResize();
  ws.send(JSON.stringify({ type: "chat", text }));
  scrollToBottom();
}

function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 160) + "px";
}

/* ---------------- Memory drawer ---------------- */
async function openMemory() {
  els.memoryDrawer.classList.remove("hidden");
  try {
    const res = await fetch("/api/memory");
    const data = await res.json();
    els.memoryBody.innerHTML = "";
    const head = document.createElement("div");
    head.className = "memory-empty";
    head.style.cssText = "font-size:11px;color:var(--text-dim);margin:0 0 6px;";
    head.textContent = `共 ${data.count} 条记忆 · ${data.semantic_enabled ? "语义检索已启用" : "关键词检索（未配置 embedding 模型）"}`;
    els.memoryBody.appendChild(head);
    if (!data.entries.length) {
      const empty = document.createElement("div");
      empty.className = "memory-empty";
      empty.textContent = "暂无记忆。多聊几句，AgentMind 会自动记住关键信息。";
      els.memoryBody.appendChild(empty);
    }
    for (const e of data.entries) {
      const item = document.createElement("div");
      item.className = "memory-item";
      item.innerHTML = `<div class="m-kind">${escapeHtml(e.kind)} · ${new Date(e.created_at * 1000).toLocaleString()}</div><div class="m-content">${escapeHtml(e.content)}</div>`;
      els.memoryBody.appendChild(item);
    }
  } catch (e) {
    els.memoryBody.innerHTML = `<div class="memory-empty">加载失败</div>`;
  }
}

/* ---------------- helpers ---------------- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- init ---------------- */
els.send.onclick = send;
els.approvalAllow.onclick = () => respondApproval(true);
els.approvalDeny.onclick = () => respondApproval(false);
els.newChat.onclick = () => {
  sessionId = null;
  els.chat.innerHTML = "";
  els.sessionName.textContent = "新对话";
  currentAssistantEl = null;
  ws.send(JSON.stringify({ type: "hello", session_id: null }));
  renderSessions();
};
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
els.input.addEventListener("input", autoResize);
els.memoryBtn.onclick = openMemory;
els.memoryClose.onclick = () => els.memoryDrawer.classList.add("hidden");
els.memoryClear.onclick = async () => {
  if (!confirm("确定清空所有长期记忆？")) return;
  await fetch("/api/memory", { method: "DELETE" });
  openMemory();
};

connect();
