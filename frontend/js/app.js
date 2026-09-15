let currentConversationId = null;
const $ = (selector) => document.querySelector(selector);
const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
};

async function start() {
  const auth = await api("/api/auth/me");
  if (!auth.user) {
    window.location.href = "/login.html";
    return;
  }
  $("#user-name").textContent = auth.user.name;
  $("#user-level").textContent =
    `${auth.user.level} · ${auth.user.level_label}`;
  $("#user-avatar").textContent = auth.user.name[0].toUpperCase();
  const status = await api("/api/status");
  $(".status-dot").textContent =
    status.ai_provider === "deepseek"
      ? `DeepSeek · ${status.model}`
      : "Demo mode · add DeepSeek key";
  await loadConversations();
  bindEvents();
}
async function loadConversations() {
  const data = await api("/api/conversations");
  const list = $("#conversation-list");
  list.innerHTML = data.conversations.length
    ? data.conversations
        .map(
          (item) =>
            `<button class="conversation-item ${item.id === currentConversationId ? "active" : ""}" data-id="${item.id}">${escapeHtml(item.title)}</button>`,
        )
        .join("")
    : '<span class="empty-state">No sessions yet.</span>';
  list
    .querySelectorAll("[data-id]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        selectConversation(button.dataset.id),
      ),
    );
  if (!currentConversationId && data.conversations[0]) {
    await selectConversation(data.conversations[0].id);
  } else if (!currentConversationId) {
    await createConversation();
  }
}
async function createConversation() {
  const data = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ topic: "Everyday English" }),
  });
  currentConversationId = data.conversation.id;
  $("#conversation-title").textContent = "A fresh conversation";
  $("#message-list").innerHTML = welcomeMarkup();
  await loadConversations();
}
async function selectConversation(id) {
  currentConversationId = id;
  const data = await api(`/api/conversations/${id}/messages`);
  const list = $("#message-list");
  list.innerHTML = data.messages.length
    ? data.messages.map(messageMarkup).join("")
    : welcomeMarkup();
  const conversation = (await api("/api/conversations")).conversations.find(
    (item) => item.id === id,
  );
  if (conversation) $("#conversation-title").textContent = conversation.title;
  document
    .querySelectorAll(".conversation-item")
    .forEach((item) => item.classList.toggle("active", item.dataset.id === id));
}
async function sendMessage(event) {
  event.preventDefault();
  const input = $("#message-input");
  const content = input.value.trim();
  if (!content) return;
  if (!currentConversationId) {
    await createConversation();
  }
  appendMessage({ role: "user", content });
  input.value = "";
  updateCount();
  setBusy(true);
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: currentConversationId,
        message: content,
      }),
    });
    appendMessage(data.message);
    await loadConversations();
  } catch (err) {
    appendMessage({
      role: "assistant",
      content: "I could not save that message. Please try again.",
    });
  } finally {
    setBusy(false);
  }
}
async function showReview() {
  if (!currentConversationId) return;
  $("#review-modal").classList.remove("hidden");
  $("#review-content").textContent = "Reading this session...";
  try {
    const data = await api("/api/analysis", {
      method: "POST",
      body: JSON.stringify({ conversation_id: currentConversationId }),
    });
    $("#review-content").innerHTML = formatReview(data.analysis);
  } catch (err) {
    $("#review-content").textContent = err.message;
  }
}
async function loadStats() {
  const data = await api("/api/statistics");
  $("#stat-level").textContent = data.level;
  $("#stat-words").textContent = data.total_words;
  $("#stat-turns").textContent = data.total_user_turns;
  $("#level-progress").style.width = `${Math.min(100, data.total_words / 10)}%`;
  const labels = {
    grammar: "Grammar",
    vocabulary: "Vocabulary",
    fluency: "Fluency",
    naturalness: "Naturalness",
    communication: "Communication",
    sentence_structure: "Sentence structure",
  };
  $("#skill-list").innerHTML = Object.entries(data.skills)
    .map(
      ([key, score]) =>
        `<div class="skill-row"><span>${labels[key]}</span><div class="skill-bar"><i style="width:${score}%"></i></div><strong>${score}</strong></div>`,
    )
    .join("");
  $("#pattern-list").innerHTML = data.patterns.length
    ? data.patterns
        .map(
          (item) =>
            `<div class="pattern"><strong>${escapeHtml(item.category.replaceAll("_", " "))}</strong><p>${escapeHtml(item.example)} → ${escapeHtml(item.correction)}</p></div>`,
        )
        .join("")
    : '<p class="empty-state">Your patterns will appear after you practice.</p>';
}
function bindEvents() {
  $("#new-session").addEventListener("click", createConversation);
  $("#chat-form").addEventListener("submit", sendMessage);
  $("#message-input").addEventListener("input", updateCount);
  $("#message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  $("#review-button").addEventListener("click", showReview);
  $("#close-modal").addEventListener("click", () =>
    $("#review-modal").classList.add("hidden"),
  );
  $("#refresh-stats").addEventListener("click", loadStats);
  $("#logout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    window.location.href = "/login.html";
  });
  document.querySelectorAll("[data-view]").forEach((button) =>
    button.addEventListener("click", async () => {
      const stats = button.dataset.view === "stats";
      $("#chat-view").classList.toggle("active-view", !stats);
      $("#stats-view").classList.toggle("active-view", stats);
      document
        .querySelectorAll("[data-view]")
        .forEach((item) => item.classList.toggle("active", item === button));
      $("#page-title").textContent = stats
        ? "Your progress, at a glance."
        : `Good morning, ${$("#user-name").textContent}.`;
      if (stats) await loadStats();
    }),
  );
}
function appendMessage(message) {
  const list = $("#message-list");
  const welcome = list.querySelector(".welcome-card");
  if (welcome) welcome.remove();
  list.insertAdjacentHTML("beforeend", messageMarkup(message));
  list.scrollTop = list.scrollHeight;
}
function messageMarkup(message) {
  return `<div class="message ${message.role}"><div class="message-avatar">${message.role === "assistant" ? "a" : $("#user-avatar").textContent}</div><div class="message-bubble">${escapeHtml(message.content)}</div></div>`;
}
function welcomeMarkup() {
  return `<div class="welcome-card"><div class="welcome-orbit">a</div><div><span class="eyebrow">YOUR ENGLISH, YOUR PACE</span><h2>What’s on your mind today?</h2><p>Tell me about your day, a plan, or something you’re curious about. There’s no perfect answer here.</p></div></div>`;
}
function formatReview(text) {
  return escapeHtml(text)
    .replace(/^# (.*)$/gm, "<h1>$1</h1>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>");
}
function escapeHtml(value) {
  return String(value).replace(
    /[&<>'"]/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        char
      ],
  );
}
function updateCount() {
  $("#char-count").textContent = `${$("#message-input").value.length} / 500`;
}
function setBusy(busy) {
  $("#message-input").disabled = busy;
  $(".send-button").disabled = busy;
}
start().catch(() => {
  window.location.href = "/login.html";
});
