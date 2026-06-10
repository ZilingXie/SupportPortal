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
  title: "",
  question: "",
  customerEmail: "",
  source: "account-ui",
  isSubmitting: false,
  result: null,
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
    automated: "Automated",
    needs_more_info: "Needs more info",
    not_automated: "Not automated",
  };
  return labels[status] || "Ready";
}

function resultTone(status) {
  if (status === "automated") return "success";
  if (status === "needs_more_info") return "warning";
  if (status === "not_automated") return "neutral";
  return "danger";
}

function showToast(message) {
  if (!toastRoot) return;
  toastRoot.innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;
  window.setTimeout(() => {
    toastRoot.innerHTML = "";
  }, 3200);
}

function resultMarkup() {
  if (state.error) {
    return `
      <div class="result-card danger">
        <div class="result-title">
          <span>Submission failed</span>
          <span class="status-pill">Error</span>
        </div>
        <p class="result-copy">${escapeHtml(state.error)}</p>
      </div>
    `;
  }

  if (!state.result) {
    return `
      <div class="result-card neutral">
        <div class="result-title">
          <span>Route preview</span>
          <span class="status-pill">Waiting</span>
        </div>
        <p class="result-copy">Submit a title and customer question to create a ticket, route it, and run the billing process when eligible.</p>
      </div>
    `;
  }

  const result = state.result;
  const missingFields = Array.isArray(result.missing_fields) ? result.missing_fields : [];
  return `
    <div class="result-card ${resultTone(result.status)}">
      <div class="result-title">
        <span>${escapeHtml(statusLabel(result.status))}</span>
        <span class="status-pill">${escapeHtml(result.route || "manual review")}</span>
      </div>
      <div class="meta-grid">
        <div class="meta-row">
          <span class="meta-label">Ticket ID</span>
          <span class="meta-value">${escapeHtml(result.ticket_id || "")}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Internal email</span>
          <span class="meta-value">${escapeHtml(result.internal_email_send_status || "not_applicable")}</span>
        </div>
        ${
          result.internal_email_send_reason
            ? `<div class="meta-row"><span class="meta-label">Email reason</span><span class="meta-value">${escapeHtml(result.internal_email_send_reason)}</span></div>`
            : ""
        }
      </div>
    </div>
    ${
      missingFields.length
        ? `<div class="result-card warning"><div class="result-title"><span>Missing fields</span><span class="status-pill">${missingFields.length}</span></div><ul class="missing-list">${missingFields
            .map((field) => `<li>${escapeHtml(field)}</li>`)
            .join("")}</ul></div>`
        : ""
    }
    ${
      result.customer_reply
        ? `<div class="result-card success"><div class="result-title"><span>Customer reply</span></div><p class="result-copy">${renderMarkdownMessage(result.customer_reply)}</p></div>`
        : ""
    }
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
        <p class="side-copy">Create a customer ticket from an account-side request, route it, and let the billing workflow handle detailed invoice or account suspension cases.</p>
        <div class="history-stack">
          <button class="history-item" type="button">
            <strong>Same ticket identity</strong>
            <span>Account-created tickets appear in the existing dashboard and engineer views.</span>
          </button>
          <button class="history-item" type="button">
            <strong>Billing whitelist only</strong>
            <span>Only detailed invoice and account suspension enter automation.</span>
          </button>
          <button class="history-item" type="button">
            <strong>Process first</strong>
            <span>Create ticket, route, validate fields, then reply or send internal email.</span>
          </button>
        </div>
      </aside>
      <section class="workbench">
        <div class="workbench-header">
          <div>
            <span class="pill"><span class="material-symbols-outlined">route</span>HTTP or manual</span>
            <h2>Create and route an account ticket</h2>
          </div>
          <button class="ghost-button" type="button" data-action="reset">Reset</button>
        </div>
        <div class="intake-grid">
          <form class="panel form-stack" data-account-form>
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
          <section class="panel result-panel">
            ${resultMarkup()}
          </section>
        </div>
      </section>
    </main>
  `;
  bind();
}

function readForm(form) {
  const formData = new FormData(form);
  state.title = String(formData.get("title") || "").trim();
  state.customerEmail = String(formData.get("customerEmail") || "").trim();
  state.question = String(formData.get("question") || "").trim();
}

async function submitAccountIntake(event) {
  event.preventDefault();
  const form = event.currentTarget;
  readForm(form);
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
    state.result = payload;
    showToast(payload.ticket_id ? `Ticket ${payload.ticket_id} created` : "Ticket created");
  } catch (error) {
    state.error = error instanceof Error ? error.message : "Account intake failed.";
  } finally {
    state.isSubmitting = false;
    render();
  }
}

function resetForm() {
  state.title = "";
  state.question = "";
  state.customerEmail = "";
  state.result = null;
  state.error = "";
  state.composerToolbarState = buildDefaultComposerToolbarState();
  composerRuntime = null;
  render();
}

function bind() {
  const form = document.querySelector("[data-account-form]");
  if (form) {
    form.addEventListener("submit", submitAccountIntake);
  }
  const resetButton = document.querySelector('[data-action="reset"]');
  if (resetButton) {
    resetButton.addEventListener("click", resetForm);
  }
  applySharedComposerToolbarStateToButtons(document, state.composerToolbarState);
}

renderSharedComposerFormattingToolbarButtons(state.composerToolbarState);
serializeRichComposerHtmlToMarkdown("");
void composerRuntime;
render();
