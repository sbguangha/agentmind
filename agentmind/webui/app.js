/* AgentMind WebUI client — WebSocket chat with live streaming & tool cards */

const els = {
  chat: document.getElementById("chat"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  mic: document.getElementById("mic"),
  sessions: document.getElementById("session-list"),
  newChat: document.getElementById("new-chat"),
  status: document.getElementById("status"),
  statusText: document.getElementById("status-text"),
  sessionName: document.getElementById("current-session"),
  meta: document.getElementById("meta"),
  accessSelect: document.getElementById("access-select"),
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

// optional web token (server gate): ?token=xxx in the URL
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const authSuffix = TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : "";
function api(path) { return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}`; }

// bump this when the WebUI logic changes; helps diagnose stale-cache issues
const APP_VERSION = "v5-voice-recall";
console.log("[AgentMind] app loaded:", APP_VERSION);

// remember the current session across page refreshes so a reload reopens the
// same conversation instead of piling up empty "新对话" sessions
const SESSION_KEY = "agentmind.currentSession";

let ws = null;
let sessionId = localStorage.getItem(SESSION_KEY) || null;
let sessions = [];
let currentAssistantEl = null;
let thinkingEl = null;
let toolCards = [];
let approvalQueue = [];
let currentApproval = null;
let needsHistoryRender = true;

/* ---------------- WebSocket ---------------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws${authSuffix}`);
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
      sessionId = payload.session_id || null;
      if (sessionId) localStorage.setItem(SESSION_KEY, sessionId);
      else localStorage.removeItem(SESSION_KEY);
      sessions = payload.sessions;
      els.meta.textContent = `model: ${payload.model}`;
      renderSessions();
      // re-render history only on first load / explicit session switch; a
      // refreshSessions() welcome must NOT wipe ephemeral UI (voice bubbles)
      if (needsHistoryRender) {
        renderHistory();
        needsHistoryRender = false;
      }
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
    case "attachment":
      if (payload.mime && payload.mime.startsWith("audio/")) {
        addVoiceBubble(payload.label || "", payload.mime, payload.url || "");
        syncMessageIds(); // the bubble is persisted server-side before this event
      }
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
      syncMessageIds();
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
  el.dataset.ts = Date.now() / 1000; // refined by syncMessageIds()
  attachRecall(el);
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

/* ---------------- Voice bubble (WeChat style) ---------------- */
let currentVoice = null;

function addVoiceBubble(label, mime, url, role = "assistant") {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = `
    <span class="role">${role === "user" ? "你 · 语音" : "AgentMind · 语音"}</span>
    <div class="voice-bubble" data-src="${url}${TOKEN ? (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : ""}" title="${escapeHtml(label)}">
      <span class="vb-play">▶</span>
      <span class="vb-wave"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>
      <span class="vb-duration">0:00</span>
    </div>`;
  attachRecall(el);
  els.chat.appendChild(el);
  const bubble = el.querySelector(".voice-bubble");
  bubble.onclick = () => toggleVoice(bubble);
  scrollToBottom();
  console.log("[AgentMind] voice bubble added:", url);
  return bubble;
}

function toggleVoice(bubble) {
  if (currentVoice && currentVoice._bubble === bubble && !currentVoice.paused) {
    currentVoice.pause();
    setVoicePlaying(bubble, false);
    return;
  }
  if (currentVoice) {
    currentVoice.pause();
    setVoicePlaying(currentVoice._bubble, false);
  }
  const audio = new Audio(bubble.dataset.src);
  audio._bubble = bubble;
  currentVoice = audio;
  setVoicePlaying(bubble, true);
  audio.onloadedmetadata = () => {
    bubble.querySelector(".vb-duration").textContent = fmtDuration(audio.duration);
  };
  audio.onended = () => setVoicePlaying(bubble, false);
  audio.onerror = () => { setVoicePlaying(bubble, false); bubble.querySelector(".vb-duration").textContent = "播放失败"; };
  audio.play().catch(() => setVoicePlaying(bubble, false));
}

function setVoicePlaying(bubble, playing) {
  bubble.classList.toggle("playing", playing);
  bubble.querySelector(".vb-play").textContent = playing ? "❚❚" : "▶";
}

function fmtDuration(secs) {
  if (!isFinite(secs) || secs < 0) return "0:00";
  const s = Math.round(secs);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* ---------------- Recall (撤回, 3-minute window) ---------------- */
const RECALL_WINDOW = 180; // seconds — must match the server

function attachRecall(msgEl) {
  const btn = document.createElement("span");
  btn.className = "recall-btn hidden";
  btn.textContent = "撤回";
  btn.onclick = (e) => { e.stopPropagation(); recallMessage(msgEl); };
  msgEl.appendChild(btn);
}

// show the recall button only for messages still inside the window
els.chat.addEventListener("mouseover", (e) => {
  const msg = e.target.closest(".msg");
  if (!msg) return;
  const btn = msg.querySelector(".recall-btn");
  if (!btn) return;
  const age = Date.now() / 1000 - (parseFloat(msg.dataset.ts) || 0);
  btn.classList.toggle("hidden", !msg.dataset.mid || age > RECALL_WINDOW);
});

async function recallMessage(msgEl) {
  const mid = msgEl.dataset.mid;
  if (!mid || !sessionId) return;
  if (!confirm("撤回这条消息？")) return;
  const res = await fetch(api(`/api/sessions/${sessionId}/messages/${mid}`), { method: "DELETE" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.error || "撤回失败");
    return;
  }
  await renderHistory();
  refreshSessions();
}

// live-rendered messages lack server ids; fetch history and assign them in
// order so recall works without a page refresh
async function syncMessageIds() {
  if (!sessionId) return;
  try {
    const res = await fetch(api(`/api/sessions/${sessionId}/messages`));
    const { messages } = await res.json();
    const domMsgs = [...els.chat.querySelectorAll(".msg")];
    if (domMsgs.length !== messages.length) return; // next render will fix it
    domMsgs.forEach((el, i) => {
      el.dataset.mid = messages[i].id || "";
      el.dataset.ts = messages[i].timestamp || 0;
    });
  } catch (e) { /* ignore */ }
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
    li.className = s.id === sessionId ? "active" : "";
    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = `${s.title} (${s.message_count})`;
    const del = document.createElement("span");
    del.className = "session-del";
    del.textContent = "✕";
    del.title = "删除会话";
    del.onclick = (e) => { e.stopPropagation(); deleteSession(s.id); };
    li.appendChild(title);
    li.appendChild(del);
    li.onclick = () => switchSession(s.id);
    els.sessions.appendChild(li);
  }
  const cur = sessions.find((s) => s.id === sessionId);
  els.sessionName.textContent = cur ? cur.title : "新对话";
  els.accessSelect.value = cur && cur.access_mode ? cur.access_mode : "restricted";
}

async function deleteSession(id) {
  if (!confirm("确定删除这个会话？此操作不可恢复。")) return;
  await fetch(api(`/api/sessions/${id}`), { method: "DELETE" });
  if (sessionId === id) {
    sessionId = null;
    localStorage.removeItem(SESSION_KEY);
    els.chat.innerHTML = "";
    currentAssistantEl = null;
    needsHistoryRender = false; // chat already cleared; the welcome must not re-render
  }
  // refresh the sidebar from the server's authoritative list
  ws.send(JSON.stringify({ type: "hello", session_id: sessionId }));
}

function switchSession(id) {
  sessionId = id;
  localStorage.setItem(SESSION_KEY, id);
  needsHistoryRender = false; // we render right here; the welcome must not wipe it again
  ws.send(JSON.stringify({ type: "hello", session_id: id }));
  renderSessions();
  renderHistory();
}

async function renderHistory() {
  console.log("[AgentMind] renderHistory()", sessionId, "needsHistoryRender=", needsHistoryRender);
  els.chat.innerHTML = "";
  currentAssistantEl = null;
  toolCards = [];
  if (!sessionId) return;
    try {
      const res = await fetch(api(`/api/sessions/${sessionId}/messages`));
      const { messages } = await res.json();
    for (const m of messages) {
      let el = null;
      if (m.attachment) {
        const bubble = addVoiceBubble(
          m.attachment.text || "", "audio/mpeg", m.attachment.url,
          m.role === "user" ? "user" : "assistant",
        );
        el = bubble.closest(".msg");
      } else if (m.role === "user") {
        el = document.createElement("div");
        el.className = "msg user";
        el.innerHTML = `<span class="role">你</span><div class="bubble"></div>`;
        el.querySelector(".bubble").textContent = m.content;
        attachRecall(el);
        els.chat.appendChild(el);
      } else if (m.role === "assistant") {
        el = document.createElement("div");
        el.className = "msg assistant";
        el.innerHTML = `<span class="role">AgentMind</span><div class="bubble"></div>`;
        el.querySelector(".bubble").textContent = m.content;
        attachRecall(el);
        els.chat.appendChild(el);
      }
      if (el) {
        el.dataset.mid = m.id || "";
        el.dataset.ts = m.timestamp || 0;
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
  el.dataset.ts = Date.now() / 1000; // id assigned by syncMessageIds() on "done"
  attachRecall(el);
  els.chat.appendChild(el);

  els.input.value = "";
  autoResize();
  ws.send(JSON.stringify({ type: "chat", text }));
  scrollToBottom();
}

/* ---------------- Voice recording ---------------- */
let recorder = null;
let recChunks = [];
let recStream = null;

els.mic.onclick = async () => {
  if (recorder) { recorder.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    alert("当前浏览器不支持录音");
    return;
  }
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    alert("无法访问麦克风，请检查浏览器权限");
    return;
  }
  recChunks = [];
  recorder = new MediaRecorder(recStream);
  const mime = recorder.mimeType || "audio/webm";
  recorder.ondataavailable = (e) => { if (e.data.size) recChunks.push(e.data); };
  recorder.onstop = async () => {
    recStream.getTracks().forEach((t) => t.stop());
    recStream = null;
    const blob = new Blob(recChunks, { type: mime });
    recorder = null;
    els.mic.classList.remove("recording");
    await sendVoice(blob, mime);
  };
  recorder.start();
  els.mic.classList.add("recording");
};

async function sendVoice(blob, mime) {
  try {
    if (!sessionId) await createSession();
    if (!sessionId) return;
    const data = await blobToBase64(blob);
    const res = await fetch(api(`/api/sessions/${sessionId}/voice`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mime, data }),
    });
    if (!res.ok) { alert("语音发送失败"); return; }
    const msg = await res.json();
    const bubble = addVoiceBubble("语音消息", mime, msg.url, "user");
    const msgEl = bubble.closest(".msg");
    msgEl.dataset.mid = msg.id;
    msgEl.dataset.ts = msg.timestamp;
    refreshSessions();
  } catch (e) {
    alert("语音发送失败");
  }
}

// create a real session on demand (voice sent into a fresh "新对话")
async function createSession() {
  const res = await fetch(api("/api/sessions"), { method: "POST" });
  const { id } = await res.json();
  sessionId = id;
  localStorage.setItem(SESSION_KEY, id);
  needsHistoryRender = false; // chat is empty; the welcome must not re-render
  ws.send(JSON.stringify({ type: "hello", session_id: id })); // bind ws + refresh sidebar
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 160) + "px";
}

/* ---------------- Memory drawer ---------------- */
async function openMemory() {
  els.memoryDrawer.classList.remove("hidden");
  try {
    const res = await fetch(api("/api/memory"));
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
  localStorage.removeItem(SESSION_KEY);
  needsHistoryRender = false; // chat already cleared below; welcome must not re-render
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
  await fetch(api("/api/memory"), { method: "DELETE" });
  openMemory();
};

/* ---------------- Access mode (workspace permission) ---------------- */
els.accessSelect.onchange = async (e) => {
  if (!sessionId) return;
  const mode = e.target.value;
  await fetch(api(`/api/sessions/${sessionId}/access`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_mode: mode }),
  });
  const cur = sessions.find((s) => s.id === sessionId);
  if (cur) cur.access_mode = mode;
};

connect();
