const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");

const AUTH_KEY = "helpdesk_auth_user";
const TICKETS_KEY = "helpdesk_tickets";
const COUNTER_KEY = "helpdesk_ticket_counter";
const MAX_RECENT = 5;

const DEMO_USERS = [{ id: "user-1", name: "Admin", email: "admin", password: "admin" }];

const STATUS_CONFIG = {
  new: { label: "New", className: "status-new" },
  waiting_for_support: { label: "Waiting for Support", className: "status-waiting_for_support" },
  waiting_for_agent: { label: "Waiting for Customer", className: "status-waiting_for_agent" },
  resolved: { label: "Resolved", className: "status-resolved" },
};

const FEATURES = [
  { icon: "SVR", label: "Server Troubleshooting", desc: "Linux/Windows server diagnostics" },
  { icon: "SEC", label: "Security Response", desc: "Vulnerability fixes & intrusion detection" },
  { icon: "DB", label: "Database Operations", desc: "MySQL/PG/Redis support" },
  { icon: "CLD", label: "Cloud Services", desc: "AWS/Azure/GCP issue handling" },
];

const state = {
  user: getCurrentUser(),
  view: "login",
  activeTicketId: null,
  statusFilter: "all",
  isSending: false,
  loginError: "",
  isSubmittingLogin: false,
  inputDraft: "",
  editingMessageId: null,
  pendingAbortController: null,
  pendingUserMessageId: null,
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sanitizeUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return null;
  }
  const markdownMatch = raw.match(/\((https?:\/\/[^)\s]+)\)/i);
  const candidate = markdownMatch ? markdownMatch[1] : raw;
  const trimmed = candidate.replace(/[)\],.;]+$/g, "");

  const withProtocol = /^[a-z]+:\/\//i.test(trimmed)
    ? trimmed
    : /^[\w.-]+\.[a-z]{2,}(\/.*)?$/i.test(trimmed)
    ? `https://${trimmed}`
    : trimmed;

  try {
    const parsed = new URL(withProtocol);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.toString();
    }
  } catch {
    return null;
  }
  return null;
}

function formatMultilineText(value) {
  return escapeHtml(value).replaceAll("\n", "<br>");
}

function normalizeCitationItem(item) {
  const sourcePathRaw = String(item?.sourcePath ?? item?.source_path ?? "").trim();
  const sourceUrl =
    sanitizeUrl(item?.sourceUrl ?? item?.source_url ?? item?.url) ||
    sanitizeUrl(sourcePathRaw);
  return {
    heading: String(item?.heading ?? item?.title ?? "").trim(),
    sourcePath: sourcePathRaw,
    sourceUrl,
  };
}

function normalizeCitations(payload) {
  if (Array.isArray(payload?.citations) && payload.citations.length > 0) {
    return payload.citations
      .map((item) => normalizeCitationItem(item))
      .filter((item) => item.sourceUrl || item.heading || item.sourcePath);
  }

  if (Array.isArray(payload?.sources) && payload.sources.length > 0) {
    return payload.sources
      .map((source) => normalizeCitationItem({ source_url: source }))
      .filter((item) => item.sourceUrl);
  }

  return [];
}

function renderCitationsHtml(citations) {
  if (!Array.isArray(citations) || citations.length === 0) {
    return "";
  }
  const items = citations
    .map((citation, index) => {
      const heading = citation.heading ? escapeHtml(citation.heading) : "Reference";
      const sourcePath = citation.sourcePath ? ` (${escapeHtml(citation.sourcePath)})` : "";
      if (citation.sourceUrl) {
        return `<li><a class="citation-link" href="${escapeHtml(citation.sourceUrl)}" target="_blank" rel="noopener noreferrer">${heading}</a>${sourcePath}</li>`;
      }
      return `<li>${heading}${sourcePath}</li>`;
    })
    .join("");
  return `
    <div class="citations">
      <div class="citation-title">Related Documentation</div>
      <ol class="citation-list">${items}</ol>
    </div>
  `;
}

function renderMessageBody(message) {
  const base = `<div>${formatMultilineText(message.content || "")}</div>`;
  if (message.role !== "assistant") {
    return base;
  }
  return `${base}${renderCitationsHtml(message.citations || [])}`;
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`.trim();
  node.textContent = message;
  toastRoot.appendChild(node);
  setTimeout(() => {
    node.remove();
  }, 2600);
}

function getCurrentUser() {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function login(email, password) {
  const normalizedEmail = String(email || "").trim().toLowerCase();
  const normalizedPassword = String(password || "").trim();
  if (normalizedEmail === "admin" && normalizedPassword === "admin") {
    const fallback = DEMO_USERS[0];
    const userData = { id: fallback.id, name: fallback.name, email: fallback.email };
    localStorage.setItem(AUTH_KEY, JSON.stringify(userData));
    return userData;
  }
  const match = DEMO_USERS.find(
    (user) => user.email.toLowerCase() === normalizedEmail && user.password === normalizedPassword
  );
  if (!match) {
    return null;
  }
  const userData = { id: match.id, name: match.name, email: match.email };
  localStorage.setItem(AUTH_KEY, JSON.stringify(userData));
  return userData;
}

function logout() {
  localStorage.removeItem(AUTH_KEY);
  state.user = null;
}

function getCounter() {
  const raw = localStorage.getItem(COUNTER_KEY);
  return raw ? Number(raw) : 0;
}

function setCounter(value) {
  localStorage.setItem(COUNTER_KEY, String(value));
}

function getAllTickets() {
  try {
    const raw = localStorage.getItem(TICKETS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveAllTickets(tickets) {
  localStorage.setItem(TICKETS_KEY, JSON.stringify(tickets));
}

function mapBackendRoleToClientRole(role) {
  const normalized = String(role || "").toLowerCase();
  if (normalized === "customer") {
    return "user";
  }
  return "assistant";
}

function mapBackendStatusToClientStatus(ticket) {
  const status = String(ticket?.status || "open").toLowerCase();
  if (status === "resolved") {
    return "resolved";
  }
  if (status === "waiting_for_engineer") {
    return "waiting_for_support";
  }

  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  const latest = messages.length > 0 ? messages[messages.length - 1] : null;
  const latestRole = String(latest?.role || "").toLowerCase();
  if (latestRole === "customer") {
    return "waiting_for_support";
  }
  return "waiting_for_agent";
}

function normalizeBackendTicket(ticket) {
  const ticketId = String(ticket?.ticket_id || "").trim();
  if (!ticketId) {
    return null;
  }
  const createdAt = String(ticket?.created_at || new Date().toISOString());
  const updatedAt = String(ticket?.updated_at || createdAt);
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];

  return {
    id: ticketId,
    title: String(ticket?.subject || "New Session"),
    status: mapBackendStatusToClientStatus(ticket),
    createdAt,
    updatedAt,
    userId: String(ticket?.customer_id || ""),
    messages: messages.map((message, index) => ({
      id:
        String(message?.id || "").trim() ||
        `${ticketId}-m-${String(message?.created_at || "")}-${index}`,
      role: mapBackendRoleToClientRole(message?.role),
      content: String(message?.content || ""),
      createdAt: String(message?.created_at || updatedAt),
      citations: normalizeCitations({
        citations: Array.isArray(message?.citations) ? message.citations : [],
        sources: Array.isArray(message?.sources) ? message.sources : [],
      }),
    })),
  };
}

async function syncTicketsFromBackend(options = {}) {
  const { silent = false } = options;
  if (!state.user?.id) {
    return;
  }
  try {
    const response = await fetch("/api/engineer/tickets?status=all");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    const incoming = Array.isArray(payload?.tickets) ? payload.tickets : [];
    const mapped = incoming
      .filter((ticket) => String(ticket?.customer_id || "") === state.user.id)
      .map(normalizeBackendTicket)
      .filter(Boolean);

    const allLocal = getAllTickets();
    const otherUsersLocal = allLocal.filter((ticket) => ticket.userId !== state.user.id);
    saveAllTickets([...otherUsersLocal, ...mapped]);
  } catch (error) {
    if (!silent) {
      toast(`Failed to sync sessions from backend: ${error.message}`, "error");
    }
  }
}

function getTicketsByUser(userId) {
  return getAllTickets()
    .filter((ticket) => ticket.userId === userId)
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

function getTicketById(ticketId) {
  return getAllTickets().find((ticket) => ticket.id === ticketId) || null;
}

function createTicket(userId) {
  const next = getCounter() + 1;
  setCounter(next);
  const now = new Date().toISOString();
  const ticket = {
    id: `TK-${String(next).padStart(3, "0")}`,
    title: "New Session",
    status: "new",
    createdAt: now,
    updatedAt: now,
    userId,
    messages: [],
  };
  const all = getAllTickets();
  all.push(ticket);
  saveAllTickets(all);
  return ticket;
}

function updateTicketStatus(ticketId, status) {
  const all = getAllTickets();
  const idx = all.findIndex((ticket) => ticket.id === ticketId);
  if (idx < 0) {
    return;
  }
  all[idx].status = status;
  all[idx].updatedAt = new Date().toISOString();
  saveAllTickets(all);
}

function updateTicketTitle(ticketId, title) {
  const all = getAllTickets();
  const idx = all.findIndex((ticket) => ticket.id === ticketId);
  if (idx < 0) {
    return;
  }
  all[idx].title = title;
  all[idx].updatedAt = new Date().toISOString();
  saveAllTickets(all);
}

function saveTicketMessages(ticketId, messages) {
  const all = getAllTickets();
  const idx = all.findIndex((ticket) => ticket.id === ticketId);
  if (idx < 0) {
    return;
  }
  all[idx].messages = messages;
  all[idx].updatedAt = new Date().toISOString();
  saveAllTickets(all);
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusBadge(status) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.new;
  return `<span class="status-badge ${config.className}">${config.label}</span>`;
}

function parseRoute() {
  const hash = window.location.hash || "#/chat";
  const path = hash.replace(/^#/, "");
  if (!state.user) {
    state.view = "login";
    return;
  }
  if (path.startsWith("/tickets")) {
    state.view = "tickets";
    state.activeTicketId = null;
    return;
  }
  if (path.startsWith("/chat/")) {
    state.view = "chat-ticket";
    state.activeTicketId = path.split("/")[2] || null;
    return;
  }
  state.view = "chat-home";
  state.activeTicketId = null;
}

function navigate(path) {
  const target = `#${path}`;
  if (window.location.hash === target) {
    parseRoute();
    render();
    return;
  }
  window.location.hash = target;
}

function renderLogin() {
  appRoot.innerHTML = `
    <div class="page-auth">
      <div class="auth-wrap">
        <div class="brand-head">
          <div class="brand-icon">IT</div>
          <div>
            <h1>IT HelpDesk</h1>
            <p>IT Operations Support Portal</p>
          </div>
        </div>
        <section class="panel">
          <div class="panel-header">
            <h2 class="panel-title">Sign In</h2>
            <p class="panel-desc">Enter your credentials to access the system</p>
          </div>
          <div class="panel-body">
            <form id="login-form" class="stack">
              ${
                state.loginError
                  ? `<div class="error-box">${escapeHtml(state.loginError)}</div>`
                  : ""
              }
              <div class="field">
                <label for="username">Username</label>
                <input class="input" id="username" name="username" type="text" placeholder="admin" required />
              </div>
              <div class="field">
                <label for="password">Password</label>
                <input class="input" id="password" name="password" type="password" placeholder="admin" required />
              </div>
              <button class="btn btn-primary w-full" type="submit" ${
                state.isSubmittingLogin ? "disabled" : ""
              }>
                ${state.isSubmittingLogin ? "Signing in..." : "Sign In"}
              </button>
            </form>
          </div>
        </section>
        <section class="demo-box">
          <div><strong>Demo Account</strong></div>
          <div>Username: admin / Password: admin</div>
        </section>
      </div>
    </div>
  `;

  const form = document.getElementById("login-form");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = document.getElementById("username")?.value?.trim() || "";
    const password = document.getElementById("password")?.value || "";
    state.loginError = "";
    const result = login(username, password);
    if (!result) {
      state.isSubmittingLogin = false;
      state.loginError = "Invalid username or password. Please try again.";
      render();
      return;
    }
    state.user = result;
    state.isSubmittingLogin = false;
    await syncTicketsFromBackend({ silent: true });
    navigate("/chat");
  });
}

function renderSidebar() {
  const tickets = getTicketsByUser(state.user.id);
  const recent = tickets.slice(0, MAX_RECENT);
  return `
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-brand">
          <div class="sidebar-brand-icon">IT</div>
          <div class="sidebar-brand-title">
            <span class="line-1">IT HelpDesk</span>
            <span class="line-2">Support Portal</span>
          </div>
        </div>
        <button class="btn btn-primary" data-action="new-session">+ New Session</button>
      </div>
      <div class="sidebar-content">
        <div class="sidebar-label">RECENT SESSIONS</div>
        ${
          recent.length === 0
            ? `<p class="session-empty">No sessions yet. Click above to create one.</p>`
            : recent
                .map(
                  (ticket) => `
              <button class="session-btn ${
                state.activeTicketId === ticket.id ? "active" : ""
              }" data-action="open-ticket" data-ticket-id="${ticket.id}">
                <div class="session-top">
                  <span class="session-id">${ticket.id}</span>
                  ${statusBadge(ticket.status)}
                </div>
                <div class="session-title">${escapeHtml(ticket.title)}</div>
              </button>
            `
                )
                .join("")
        }
      </div>
      <div class="sidebar-footer">
        <button class="btn btn-ghost" data-action="go-tickets">View All Sessions</button>
        <div class="user-row">
          <div class="user-meta">
            <span class="user-name">${escapeHtml(state.user.name)}</span>
            <span class="user-email">${escapeHtml(state.user.email)}</span>
          </div>
          <button class="btn btn-ghost btn-icon" data-action="logout">X</button>
        </div>
      </div>
    </aside>
  `;
}

function renderChatHome() {
  return `
    <section class="welcome">
      <div class="welcome-inner">
        <div class="bot-mark">AI</div>
        <h1 class="welcome-title">Welcome to IT Support</h1>
        <p class="welcome-desc">
          Your AI operations assistant, ready to help with server troubleshooting, security incidents,
          performance optimization, and more. Start a new session to begin.
        </p>
        <div class="feature-grid">
          ${FEATURES.map(
            (feature) => `
            <article class="feature-item">
              <div class="feature-icon">${feature.icon}</div>
              <div class="feature-label">${escapeHtml(feature.label)}</div>
              <div class="feature-desc">${escapeHtml(feature.desc)}</div>
            </article>
          `
          ).join("")}
        </div>
        <button class="btn btn-primary" data-action="new-session">+ New Session</button>
      </div>
    </section>
  `;
}

function renderChatTicket() {
  const ticket = getTicketById(state.activeTicketId);
  if (!ticket || ticket.userId !== state.user.id) {
    return `<div class="empty-state">Session not found.</div>`;
  }
  const canCompose = !state.isSending && ticket.status !== "resolved";
  const isEditing = Boolean(state.editingMessageId);

  if (isEditing && !ticket.messages.some((message) => message.id === state.editingMessageId)) {
    state.editingMessageId = null;
    if (!state.isSending) {
      state.inputDraft = "";
    }
  }

  return `
    <section class="chat-root">
      <header class="chat-header">
        <div>
          <div class="chat-ticket-id">${ticket.id} ${statusBadge(ticket.status)}</div>
          <div class="chat-ticket-title">${escapeHtml(ticket.title)}</div>
        </div>
        <div>
          ${
            ticket.status === "resolved"
              ? `<button class="btn btn-outline" data-action="reopen-ticket" data-ticket-id="${ticket.id}">Reopen</button>`
              : `<button class="btn btn-outline" data-action="resolve-ticket" data-ticket-id="${ticket.id}">Resolve</button>`
          }
        </div>
      </header>
      <main class="chat-main">
        <div class="message-list">
          ${
            ticket.messages.length === 0
              ? `
                <div class="empty-chat">
                  <div class="bot-mark">AI</div>
                  <h3>IT Support</h3>
                  <p>Describe your IT issue and I will provide professional technical support.</p>
                </div>
              `
              : ticket.messages
                  .map(
                    (message) => `
                <div class="msg-row ${message.role === "user" ? "user" : ""}">
                  <div class="avatar ${message.role === "user" ? "user" : "assistant"}">
                    ${message.role === "user" ? "U" : "A"}
                  </div>
                  <div class="bubble ${message.role === "user" ? "user" : "assistant"}">${renderMessageBody(
                      message
                    )}</div>
                </div>
              `
                  )
                  .join("")
          }
          ${
            state.isSending
              ? `
            <div class="msg-row">
              <div class="avatar assistant">A</div>
              <div class="bubble assistant"><span class="typing"><span></span><span></span><span></span></span></div>
            </div>
          `
              : ""
          }
        </div>
      </main>
      <footer class="chat-input-wrap">
        ${
          state.isSending
            ? `<div class="composer-note">Generating answer... click stop to interrupt.</div>`
            : isEditing
            ? `<div class="composer-note">Editing your last message. Press Enter to resend, Shift+Enter for newline.</div>`
            : ""
        }
        <form id="chat-input-form" class="chat-input-inner">
          <textarea
            id="chat-input"
            class="textarea"
            rows="1"
            placeholder="Describe your IT issue..."
            ${canCompose ? "" : "disabled"}
          >${escapeHtml(state.inputDraft || "")}</textarea>
          ${
            state.isSending
              ? `<button class="send-btn send-btn-stop" type="button" data-action="stop-generation" title="Stop generation"><span class="stop-glyph" aria-hidden="true"></span></button>`
              : `<button class="send-btn" type="submit" ${canCompose ? "" : "disabled"}>${isEditing ? "Resend" : "Send"}</button>`
          }
        </form>
      </footer>
    </section>
  `;
}

function renderTicketsPage() {
  const all = getTicketsByUser(state.user.id);
  const filtered =
    state.statusFilter === "all"
      ? all
      : all.filter((ticket) => ticket.status === state.statusFilter);

  return `
    <section class="tickets-root">
      <header class="tickets-header">
        <div class="tickets-header-left">
          <button class="btn btn-ghost btn-icon" data-action="go-chat">&lt;</button>
          <div class="tickets-title">Session History</div>
        </div>
        <select class="select" id="status-filter">
          <option value="all" ${state.statusFilter === "all" ? "selected" : ""}>All Statuses</option>
          <option value="new" ${state.statusFilter === "new" ? "selected" : ""}>New</option>
          <option value="waiting_for_support" ${
            state.statusFilter === "waiting_for_support" ? "selected" : ""
          }>Waiting for Support</option>
          <option value="waiting_for_agent" ${
            state.statusFilter === "waiting_for_agent" ? "selected" : ""
          }>Waiting for Customer</option>
          <option value="resolved" ${state.statusFilter === "resolved" ? "selected" : ""}>Resolved</option>
        </select>
      </header>
      <div class="tickets-body">
        ${
          filtered.length === 0
            ? `<div class="empty-state">No sessions found.</div>`
            : `
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Session ID</th>
                  <th>Title</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Updated</th>
                  <th style="text-align:right">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${filtered
                  .map(
                    (ticket) => `
                  <tr>
                    <td class="mono">${ticket.id}</td>
                    <td>${escapeHtml(ticket.title)}</td>
                    <td>${statusBadge(ticket.status)}</td>
                    <td class="mono">${formatDate(ticket.createdAt)}</td>
                    <td class="mono">${formatDate(ticket.updatedAt)}</td>
                    <td>
                      <div class="actions">
                        <button class="btn btn-ghost" data-action="open-ticket" data-ticket-id="${ticket.id}">View</button>
                        ${
                          ticket.status === "resolved"
                            ? `<button class="btn btn-outline" data-action="reopen-ticket" data-ticket-id="${ticket.id}">Reopen</button>`
                            : `<button class="btn btn-outline" data-action="resolve-ticket" data-ticket-id="${ticket.id}">Resolve</button>`
                        }
                      </div>
                    </td>
                  </tr>
                `
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        `
        }
      </div>
    </section>
  `;
}

function renderAuthed() {
  appRoot.innerHTML = `
    <div class="app-shell">
      ${renderSidebar()}
      <main class="main">
        ${
          state.view === "tickets"
            ? renderTicketsPage()
            : state.view === "chat-ticket"
            ? renderChatTicket()
            : renderChatHome()
        }
      </main>
    </div>
  `;

  bindAuthedEvents();
}

function generateTitle(message) {
  const words = message.trim().split(/\s+/).slice(0, 6).join(" ");
  return words.length > 0 ? words : "New Session";
}

async function syncBackendTicketAction(ticketId, action) {
  try {
    await fetch(`/api/tickets/${encodeURIComponent(ticketId)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, engineer_id: "CLIENT-UI" }),
    });
  } catch {
    toast("Action synced locally. Backend sync failed.", "error");
  }
}

function stopGeneration() {
  if (!state.isSending || !state.pendingAbortController) {
    return;
  }
  state.pendingAbortController.abort();
}

async function handleSendMessage(text, options = {}) {
  const ticketId = state.activeTicketId;
  const ticket = getTicketById(ticketId);
  if (!ticket || ticket.status === "resolved" || state.isSending) {
    return;
  }
  const editMessageId = options.editMessageId || null;
  const now = new Date().toISOString();
  let userMessageId = editMessageId;
  let messages = [];

  if (editMessageId) {
    messages = ticket.messages.map((message) => {
      if (message.id === editMessageId && message.role === "user") {
        return {
          ...message,
          content: text,
          createdAt: now,
        };
      }
      return message;
    });
    if (!messages.some((message) => message.id === editMessageId && message.role === "user")) {
      return;
    }
  } else {
    const userMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      createdAt: now,
    };
    userMessageId = userMessage.id;
    messages = [...ticket.messages, userMessage];
  }

  saveTicketMessages(ticketId, messages);
  if (ticket.title === "New Session") {
    updateTicketTitle(ticketId, generateTitle(text));
  }
  updateTicketStatus(ticketId, "waiting_for_support");
  state.editingMessageId = null;
  state.inputDraft = "";
  state.isSending = true;
  state.pendingUserMessageId = userMessageId;
  state.pendingAbortController = new AbortController();
  render();

  try {
    const response = await fetch("/api/tickets/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: state.pendingAbortController.signal,
      body: JSON.stringify({
        ticket_id: ticketId,
        customer_id: state.user.id,
        message: text,
      }),
    });
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    const updated = getTicketById(ticketId);
    const answerMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: payload.answer,
      createdAt: new Date().toISOString(),
      citations: normalizeCitations(payload),
    };
    const nextMessages = [...(updated?.messages || messages), answerMessage];
    saveTicketMessages(ticketId, nextMessages);
    updateTicketStatus(ticketId, "waiting_for_agent");
    await syncTicketsFromBackend({ silent: true });
    if (payload.sentiment?.is_alert) {
      toast("Urgent escalation triggered.", "error");
    }
  } catch (error) {
    if (error.name === "AbortError") {
      const updated = getTicketById(ticketId);
      const pendingMessage = updated?.messages?.find(
        (message) => message.id === userMessageId && message.role === "user"
      );
      state.editingMessageId = userMessageId;
      state.inputDraft = pendingMessage?.content || text;
      toast("Generation stopped. Edit your message and resend.");
    } else {
      const updated = getTicketById(ticketId);
      const failMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Request failed: ${error.message}`,
        createdAt: new Date().toISOString(),
      };
      saveTicketMessages(ticketId, [...(updated?.messages || messages), failMessage]);
      toast("Failed to fetch assistant response.", "error");
    }
  } finally {
    state.isSending = false;
    state.pendingAbortController = null;
    state.pendingUserMessageId = null;
    render();
  }
}

function bindAuthedEvents() {
  appRoot.querySelectorAll("[data-action='new-session']").forEach((element) => {
    element.addEventListener("click", () => {
      const ticket = createTicket(state.user.id);
      navigate(`/chat/${ticket.id}`);
    });
  });

  appRoot.querySelectorAll("[data-action='open-ticket']").forEach((element) => {
    element.addEventListener("click", () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      navigate(`/chat/${ticketId}`);
    });
  });

  appRoot.querySelectorAll("[data-action='resolve-ticket']").forEach((element) => {
    element.addEventListener("click", async () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      updateTicketStatus(ticketId, "resolved");
      render();
      await syncBackendTicketAction(ticketId, "resolved");
      await syncTicketsFromBackend({ silent: true });
      render();
      toast("Session marked as resolved");
    });
  });

  appRoot.querySelectorAll("[data-action='reopen-ticket']").forEach((element) => {
    element.addEventListener("click", async () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      updateTicketStatus(ticketId, "waiting_for_support");
      render();
      await syncBackendTicketAction(ticketId, "processing");
      await syncTicketsFromBackend({ silent: true });
      render();
      toast("Session reopened");
    });
  });

  const logoutButton = appRoot.querySelector("[data-action='logout']");
  logoutButton?.addEventListener("click", () => {
    logout();
    navigate("/login");
  });

  const goTickets = appRoot.querySelector("[data-action='go-tickets']");
  goTickets?.addEventListener("click", () => navigate("/tickets"));

  const goChat = appRoot.querySelector("[data-action='go-chat']");
  goChat?.addEventListener("click", () => navigate("/chat"));

  const filter = document.getElementById("status-filter");
  filter?.addEventListener("change", (event) => {
    state.statusFilter = event.target.value;
    render();
  });

  const form = document.getElementById("chat-input-form");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = String(state.inputDraft || "").trim();
    if (!message) {
      return;
    }
    await handleSendMessage(message, {
      editMessageId: state.editingMessageId,
    });
  });

  const input = document.getElementById("chat-input");
  input?.addEventListener("input", () => {
    state.inputDraft = input.value;
  });
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form?.requestSubmit();
    }
  });

  const stopButton = appRoot.querySelector("[data-action='stop-generation']");
  stopButton?.addEventListener("click", () => {
    stopGeneration();
  });
}

function render() {
  parseRoute();
  if (!state.user) {
    renderLogin();
    return;
  }
  renderAuthed();
}

window.addEventListener("hashchange", render);

async function bootstrap() {
  if (state.user) {
    await syncTicketsFromBackend({ silent: true });
  }
  render();
}

bootstrap();
