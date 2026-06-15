const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");
const SharedComposer = globalThis.SupportPortalComposer || {};

const renderMarkdownMessage =
  SharedComposer.renderMarkdownMessage ||
  ((value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\n", "<br>"));
const serializeRichComposerHtmlToMarkdown =
  SharedComposer.serializeRichComposerHtmlToMarkdown || ((value) => String(value || ""));
const buildDefaultComposerToolbarState =
  SharedComposer.buildDefaultComposerToolbarState ||
  (() => ({
    bold: false,
    italic: false,
    list: false,
    codeBlock: false,
  }));
const renderSharedComposerFormattingToolbarButtons =
  SharedComposer.renderComposerFormattingToolbarButtons || (() => "");
const applySharedComposerToolbarStateToButtons =
  SharedComposer.applyComposerToolbarStateToButtons || (() => {});

const state = {
  view: "create",
  title: "",
  question: "",
  customerEmail: "",
  source: "manual",
  isSubmitting: false,
  history: [],
  activeItem: null,
  error: "",
  composerToolbarState: buildDefaultComposerToolbarState(),
};

let composerRuntime = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function statusLabel(status) {
  const labels = {
    automation: "Automation",
    automated: "Automated",
    needs_more_info: "Needs more info",
    not_automated: "Not automated",
  };
  return labels[status] || "Not automated";
}

function routeClass(route) {
  if (route === "detailed_invoice") return "route-invoice";
  if (route === "account_suspension") return "route-suspension";
  return "route-other";
}

function safeSourceLink(source) {
  const link = String(source?.Link || "").trim();
  if (!link) return "";
  try {
    const parsed = new URL(link);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return link;
    }
  } catch {}
  return "";
}

function normalizeSource(source) {
  if (source && typeof source === "object" && safeSourceLink(source)) {
    return "api";
  }
  const normalized = String(source || "").trim().toLowerCase().replaceAll("_", "-");
  if (normalized === "api" || normalized === "http" || normalized === "account-http" || normalized === "/account-http") {
    return "api";
  }
  return "manual";
}

function sourceLabel(source) {
  if (normalizeSource(source) === "api") return "API";
  return "Manual";
}

function sourceClass(source) {
  if (normalizeSource(source) === "api") return "source-api";
  return "source-manual";
}

function renderSourceValue(source) {
  if (source && typeof source === "object") {
    const link = safeSourceLink(source);
    if (link) {
      return `<a class="source-link" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">Link</a>`;
    }
  }
  return `<span class="source-badge ${sourceClass(source)}">${escapeHtml(sourceLabel(source))}</span>`;
}

function showToast(message) {
  if (!toastRoot) return;
  toastRoot.innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;
  window.setTimeout(() => {
    toastRoot.innerHTML = "";
  }, 3200);
}

async function fetchTickets() {
  try {
    const response = await fetch("/api/account/billing-tickets?limit=30");
    if (!response.ok) return;
    const data = await response.json();
    state.history = data.tickets || data.billing_tickets || [];
  } catch {
    state.history = [];
  }
}

async function fetchTicketDetail(ticketId) {
  try {
    const response = await fetch(`/api/account/billing-tickets/${ticketId}`);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function openTicket(ticketId) {
  const detail = await fetchTicketDetail(ticketId);
  if (!detail) {
    showToast("Failed to load ticket details.");
    return;
  }
  state.activeItem = detail;
  state.view = "detail";
  render();
}

function openCreateView() {
  state.activeItem = null;
  state.view = "create";
  state.error = "";
  render();
}

async function submitAccountIntake(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  state.title = String(formData.get("title") || "").trim();
  state.question = String(formData.get("question") || "").trim();
  state.customerEmail = String(formData.get("customerEmail") || "").trim();

  if (!state.title || !state.question) {
    state.error = "Title and question are required.";
    render();
    return;
  }

  state.isSubmitting = true;
  state.error = "";
  render();

  try {
    const response = await fetch("/account", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: state.title,
        question: state.question,
        customer_email: state.customerEmail || null,
        source: state.source,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Account intake failed.");
    }
    showToast(payload.ticket_id ? `Ticket ${payload.ticket_id} created` : "Ticket created");
    await fetchTickets();
    if (payload.ticket_id) {
      await openTicket(payload.ticket_id);
    }
    state.title = "";
    state.question = "";
    state.customerEmail = "";
  } catch (err) {
    state.error = err instanceof Error ? err.message : "Account intake failed.";
  } finally {
    state.isSubmitting = false;
    render();
  }
}

function renderHistorySidebar() {
  if (!state.history.length) {
    return `
      <div class="history-empty">
        <span class="material-symbols-outlined">receipt_long</span>
        <p>No tickets yet</p>
      </div>
    `;
  }
  return `
    <div class="history-section-title">Recent tickets</div>
    ${state.history
      .map(
        (item) => {
          const itemId = item.ticket_id || item.billing_ticket_id || "";
          const itemTicketId = item.ticket_id || item.client_ticket_id || "";
          const activeTicketId = state.activeItem ? (state.activeItem.ticket_id || state.activeItem.client_ticket_id || "") : "";
          const activeBillingId = state.activeItem ? (state.activeItem.billing_ticket_id || "") : "";
          const isActive = (activeBillingId && activeBillingId === itemId) || (activeTicketId && activeTicketId === itemTicketId);
          const itemSource = item.source || "";
          const itemStatus = item.status || item.automation_status || "not_automated";
          return `
    <button class="history-item ${isActive ? "history-item--active" : ""}" type="button" data-action="open-ticket" data-id="${escapeHtml(itemId)}">
      <div class="history-item-header">
        <strong>${escapeHtml(item.title || "")}</strong>
        ${renderSourceValue(itemSource)}
      </div>
      <div class="history-item-meta">
        <span class="status-badge status-badge--${escapeHtml(itemStatus)}">${escapeHtml(statusLabel(itemStatus))}</span>
        <span class="history-time">${escapeHtml((item.updated_at || item.created_at || "").slice(0, 16).replace("T", " "))}</span>
      </div>
    </button>
  `;
        }
      )
      .join("")}
  `;
}

function renderCreateForm() {
  return `
    <div class="panel form-stack">
      <div class="form-header">
        <h3>Create a ticket</h3>
        <p class="form-desc">Submit an account-side request to route and process billing automation.</p>
      </div>
      <form data-account-form>
        <label class="field">
          <span class="field-label">Title</span>
          <input class="input" name="title" value="${escapeHtml(state.title)}" placeholder="Detailed invoice request" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">Customer email</span>
          <input class="input" name="customerEmail" value="${escapeHtml(state.customerEmail)}" placeholder="customer@example.com" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">Question</span>
          <textarea class="textarea" name="question" placeholder="Issue date: 6 May 2026&#10;Transaction ID: 1104245232004173824&#10;Amount: USD 705.97">${escapeHtml(state.question)}</textarea>
        </label>
        <div class="actions">
          <button class="primary-button" type="submit" ${state.isSubmitting ? "disabled" : ""}>
            <span class="material-symbols-outlined">send</span>
            ${state.isSubmitting ? "Creating..." : "Create ticket"}
          </button>
        </div>
      </form>
      ${
        state.error
          ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.error)}</div>`
          : ""
      }
    </div>
  `;
}

function renderDetailView() {
  const item = state.activeItem;
  if (!item) return "";

  let missingFields = [];
  if (Array.isArray(item.missing_fields)) missingFields = item.missing_fields;
  else if (typeof item.missing_fields === "string") {
    try { missingFields = JSON.parse(item.missing_fields || "[]"); } catch {}
  }

  let collectedFields = {};
  if (typeof item.collected_fields === "object" && item.collected_fields !== null) {
    collectedFields = item.collected_fields;
  }

  const itemSource = item.source || "";
  const itemStatus = item.status || item.automation_status || "not_automated";
  const ticketId = item.ticket_id || item.client_ticket_id || "";

  return `
    <div class="panel detail-stack">
      <div class="detail-header">
        <h3>${escapeHtml(item.title || "")}</h3>
        <span class="status-chip ${routeClass(item.route)}">${escapeHtml(item.route || "manual review")}</span>
      </div>
      <div class="meta-grid">
        <div class="meta-row">
          <span class="meta-label">Ticket ID</span>
          <span class="meta-value">${escapeHtml(ticketId)}</span>
        </div>

        <div class="meta-row">
          <span class="meta-label">Source</span>
          <span class="meta-value">${renderSourceValue(itemSource)}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Status</span>
          <span class="meta-value status-badge status-badge--${escapeHtml(itemStatus)}">${escapeHtml(statusLabel(itemStatus))}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Email</span>
          <span class="meta-value">${escapeHtml(item.internal_email_send_status || "not_applicable")}</span>
        </div>
        ${
          item.internal_email_send_reason
            ? `<div class="meta-row"><span class="meta-label">Email reason</span><span class="meta-value">${escapeHtml(item.internal_email_send_reason)}</span></div>`
            : ""
        }
        ${
          item.route_reason
            ? `<div class="meta-row"><span class="meta-label">Route reason</span><span class="meta-value">${escapeHtml(item.route_reason)}</span></div>`
            : ""
        }
        ${
          item.created_at
            ? `<div class="meta-row"><span class="meta-label">Created</span><span class="meta-value">${escapeHtml(item.created_at.slice(0, 16).replace("T", " "))}</span></div>`
            : ""
        }
      </div>
      ${
        missingFields.length
          ? `<div class="detail-section warning"><div class="detail-section-title">Missing fields</div><ul class="missing-list">${missingFields
              .map((field) => `<li>${escapeHtml(field)}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      ${
        Object.keys(collectedFields).length
          ? `<div class="detail-section"><div class="detail-section-title">Collected fields</div><ul class="collected-list">${Object.entries(collectedFields)
              .map(([k, v]) => `<li><strong>${escapeHtml(k)}</strong>: ${escapeHtml(String(v))}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      ${
        item.question
          ? `<div class="detail-section"><div class="detail-section-title">Customer question</div><p class="result-copy">${escapeHtml(item.question)}</p></div>`
          : ""
      }
      ${
        item.customer_reply
          ? `<div class="detail-section success"><div class="detail-section-title">Customer reply</div><p class="result-copy">${renderMarkdownMessage(item.customer_reply)}</p></div>`
          : ""
      }
    </div>
  `;
}

function render() {
  appRoot.innerHTML = `
    <main class="account-shell">
      <aside class="side-panel">
        <div class="brand">
          <div class="brand-mark"><span class="material-symbols-outlined">support_agent</span></div>
          <div>
            <div class="eyebrow">Account intake</div>
            <h1>Support Portal</h1>
          </div>
        </div>
        <div class="side-actions">
          <button class="primary-button primary-button--small" type="button" data-action="new-ticket">
            <span class="material-symbols-outlined">add</span>
            New ticket
          </button>
        </div>
        <div class="history-stack" id="history-list">
          ${renderHistorySidebar()}
        </div>
      </aside>
      <section class="workbench">
        <div class="workbench-header">
          <div>
            <span class="pill"><span class="material-symbols-outlined">route</span>HTTP or manual</span>
            <h2>${state.view === "create" ? "Create and route an account ticket" : "Ticket detail"}</h2>
          </div>
          ${state.view === "detail" ? `<button class="ghost-button" type="button" data-action="back-to-create">Back to create</button>` : ""}
        </div>
        <div class="intake-grid">
          ${state.view === "create" ? renderCreateForm() : ""}
          ${state.view === "detail" ? renderDetailView() : ""}
        </div>
      </section>
    </main>
  `;
  bind();
}

function bind() {
  const form = document.querySelector("[data-account-form]");
  if (form) {
    form.addEventListener("submit", submitAccountIntake);
  }
  const historyList = document.getElementById("history-list");
  if (historyList) {
    historyList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action='open-ticket']");
      if (!button) return;
      const id = button.dataset.id;
      if (id) openTicket(id);
    });
  }
  document.querySelectorAll("[data-action='new-ticket']").forEach((el) => {
    el.addEventListener("click", openCreateView);
  });
  document.querySelectorAll("[data-action='back-to-create']").forEach((el) => {
    el.addEventListener("click", openCreateView);
  });
  applySharedComposerToolbarStateToButtons(document, state.composerToolbarState);
}

renderSharedComposerFormattingToolbarButtons(state.composerToolbarState);
serializeRichComposerHtmlToMarkdown("");
void composerRuntime;
(async () => {
  await fetchTickets();
  render();
})();
