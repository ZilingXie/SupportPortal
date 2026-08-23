const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");

// The console is served by the production api stack, so every API call goes
// through the existing /production/api/ nginx location — the same base the
// /production console uses.
const API_BASE = "/production";
const ACCESS_TOKEN_KEY = "supportportal_automation_test_access_token";
const ACCOUNT_KEY = "supportportal_automation_test_account";
const TICKETS_REFRESH_MS = 60_000;
const RUNS_REFRESH_MS = 15_000;
const RUN_STATUS_LABELS = {
  queued: "queued",
  running: "running",
  waiting_approval: "waiting for approval",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "interrupted",
};
const DEFAULT_FETCH_TIMEOUT_MS = 25_000;

const CATEGORY_LABELS = {
  fraud_account: "Fraud Account",
  enablement: "Enablement",
  account_suspension: "Account Suspension",
};

let accessToken = "";
let currentAccount = null;
let refreshTimerId = null;
let runsTimerId = null;

const state = {
  authChecking: true,
  authError: "",
  templates: [],
  mail: null,
  tickets: [],
  ticketsLoading: false,
  selectedCategory: "",
  form: { subject: "", body: "" },
  sending: false,
  scenarios: [],
  runs: [],
  runsLoading: false,
};

function readStorage(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function writeStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function removeStorage(key) {
  localStorage.removeItem(key);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function toast(message, tone = "info") {
  const node = document.createElement("div");
  node.className = `at-toast${tone === "error" ? " is-error" : tone === "success" ? " is-success" : ""}`;
  node.textContent = message;
  toastRoot.appendChild(node);
  setTimeout(() => node.remove(), 4800);
}

async function readResponsePayload(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function responseErrorMessage(payload, fallback) {
  return String(payload?.detail || payload?.error || fallback || "Request failed.");
}

async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_FETCH_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${API_BASE}${url}`, {
      ...options,
      cache: "no-store",
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("Request timed out. The production api stack may be busy.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
  const payload = await readResponsePayload(response);
  if ([401, 403].includes(Number(response?.status))) {
    clearAuth();
    state.authError = "Your admin session has expired. Sign in again.";
    render();
    throw new Error(state.authError);
  }
  return { response, payload };
}

function clearAuth() {
  accessToken = "";
  currentAccount = null;
  removeStorage(ACCESS_TOKEN_KEY);
  removeStorage(ACCOUNT_KEY);
  if (refreshTimerId) {
    clearInterval(refreshTimerId);
    refreshTimerId = null;
  }
  if (runsTimerId) {
    clearInterval(runsTimerId);
    runsTimerId = null;
  }
}

/* -- Auth ----------------------------------------------------------------- */

async function workspaceMe(token) {
  const response = await fetch(`${API_BASE}/api/workspace/me`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
  const payload = await readResponsePayload(response);
  if (!response.ok) {
    throw new Error(responseErrorMessage(payload, "Workspace authentication failed."));
  }
  if (String(payload?.account?.role || "").toLowerCase() !== "admin") {
    throw new Error("Admin role required");
  }
  return payload.account;
}

async function handleLogin(form) {
  const data = new FormData(form);
  const email = String(data.get("email") || "").trim();
  const password = String(data.get("password") || "");
  if (!email || !password) return;
  state.authChecking = true;
  state.authError = "";
  render();
  try {
    const response = await fetch(`${API_BASE}/api/workspace/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Invalid email or password."));
    }
    if (String(payload?.account?.role || "").toLowerCase() !== "admin") {
      throw new Error("Admin role required");
    }
    accessToken = String(payload.access_token || "").trim();
    currentAccount = payload.account;
    if (!accessToken) throw new Error("Workspace login did not return an access token.");
    writeStorage(ACCESS_TOKEN_KEY, accessToken);
    writeStorage(ACCOUNT_KEY, currentAccount);
    await enterConsole();
  } catch (error) {
    clearAuth();
    state.authChecking = false;
    state.authError = error instanceof Error ? error.message : "Workspace login failed.";
    render();
  }
}

async function enterConsole() {
  state.authChecking = false;
  state.authError = "";
  render();
  await Promise.all([loadTemplates(), loadTickets(), loadScenarios()]);
  if (refreshTimerId) clearInterval(refreshTimerId);
  refreshTimerId = setInterval(() => {
    if (accessToken) loadTickets({ silent: true });
  }, TICKETS_REFRESH_MS);
  if (runsTimerId) clearInterval(runsTimerId);
  runsTimerId = setInterval(() => {
    if (accessToken) loadScenarios({ silent: true });
  }, RUNS_REFRESH_MS);
}

async function boot() {
  accessToken = String(readStorage(ACCESS_TOKEN_KEY, "") || "");
  if (!accessToken) {
    state.authChecking = false;
    render();
    return;
  }
  try {
    currentAccount = await workspaceMe(accessToken);
    await enterConsole();
  } catch (error) {
    clearAuth();
    state.authChecking = false;
    state.authError = error instanceof Error ? error.message : "Workspace authentication failed.";
    render();
  }
}

/* -- Data ----------------------------------------------------------------- */

async function loadTemplates() {
  try {
    const { response, payload } = await apiFetch("/api/automation-test/templates");
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Failed to load templates."));
    }
    state.templates = Array.isArray(payload?.categories) ? payload.categories : [];
    state.mail = payload?.mail || null;
  } catch (error) {
    if (state.authError) return;
    toast(error instanceof Error ? error.message : "Failed to load templates.", "error");
  }
  render();
}

async function loadTickets({ silent = false } = {}) {
  if (!silent) {
    state.ticketsLoading = true;
    render();
  }
  try {
    const { response, payload } = await apiFetch("/api/automation-test/tickets?limit=100");
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Failed to load test tickets."));
    }
    state.tickets = Array.isArray(payload?.tickets) ? payload.tickets : [];
  } catch (error) {
    if (!silent && !state.authError) {
      toast(error instanceof Error ? error.message : "Failed to load test tickets.", "error");
    }
  } finally {
    state.ticketsLoading = false;
  }
  if (!silent) render();
}

async function loadScenarios({ silent = false } = {}) {
  if (!silent) {
    state.runsLoading = true;
    render();
  }
  try {
    const { response, payload } = await apiFetch("/api/automation-test/scenarios");
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Failed to load scenario runs."));
    }
    state.scenarios = Array.isArray(payload?.scenarios) ? payload.scenarios : [];
    state.runs = Array.isArray(payload?.runs) ? payload.runs : [];
  } catch (error) {
    if (!silent && !state.authError) {
      toast(error instanceof Error ? error.message : "Failed to load scenario runs.", "error");
    }
  } finally {
    state.runsLoading = false;
  }
  if (!silent) render();
}

function activeRun() {
  return state.runs.find((run) => ["queued", "running", "waiting_approval"].includes(run.status)) || null;
}

async function startScenario(scenarioId) {
  if (!scenarioId) return;
  const active = activeRun();
  if (active) {
    toast(`Another run is already active (${active.scenario_id}, ${active.status}).`, "error");
    return;
  }
  const scenario = state.scenarios.find((item) => item.id === scenarioId);
  const confirmed = window.confirm(
    `Run scenario ${scenarioId} (${scenario ? scenario.label : ""})? This sends real emails and creates a real Zendesk ticket with real automation side effects. Continue?`
  );
  if (!confirmed) return;
  try {
    const { response, payload } = await apiFetch(
      `/api/automation-test/scenarios/${encodeURIComponent(scenarioId)}/runs`,
      { method: "POST" }
    );
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Failed to start the scenario run."));
    }
    toast(`Scenario ${scenarioId} started. Progress appears below; keep this page open.`, "success");
    await loadScenarios();
  } catch (error) {
    if (!state.authError) {
      toast(error instanceof Error ? error.message : "Failed to start the scenario run.", "error");
    }
  }
}

async function cancelRun(runId) {
  if (!window.confirm("Cancel this scenario run? Steps already sent cannot be undone.")) return;
  try {
    const { response, payload } = await apiFetch(
      `/api/automation-test/scenarios/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" }
    );
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Failed to cancel the run."));
    }
    await loadScenarios();
  } catch (error) {
    if (!state.authError) {
      toast(error instanceof Error ? error.message : "Failed to cancel the run.", "error");
    }
  }
}

function selectCategory(categoryId) {
  const template = state.templates.find((item) => item.id === categoryId);
  state.selectedCategory = categoryId;
  state.form = {
    subject: template ? String(template.subject || "") : "",
    body: template ? String(template.body || "") : "",
  };
  render();
  const formCard = document.querySelector("[data-at-form-card]");
  if (formCard) formCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function createTicket() {
  if (state.sending || !state.selectedCategory) return;
  const recipient = String(state.mail?.recipient || "");
  const confirmed = window.confirm(
    `This sends a real email to ${recipient || "the Zendesk support address"} and creates a real Zendesk ticket with real automation side effects (public reply, internal handoff email, Slack). Continue?`
  );
  if (!confirmed) return;
  state.sending = true;
  render();
  try {
    const { response, payload } = await apiFetch("/api/automation-test/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: state.selectedCategory,
        subject: state.form.subject,
        body: state.form.body,
      }),
    });
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Failed to create the test ticket."));
    }
    toast(
      payload?.ticket?.send_status === "sent"
        ? `Test email sent for ${CATEGORY_LABELS[state.selectedCategory] || state.selectedCategory}. Use Refresh to link the Zendesk ticket.`
        : `Test email failed: ${payload?.ticket?.send_error || "unknown error"}`,
      payload?.ticket?.send_status === "sent" ? "success" : "error"
    );
    await loadTickets();
  } catch (error) {
    if (!state.authError) {
      toast(error instanceof Error ? error.message : "Failed to create the test ticket.", "error");
    }
  } finally {
    state.sending = false;
    render();
  }
}

async function refreshTicket(ticketId) {
  try {
    const { response, payload } = await apiFetch(
      `/api/automation-test/tickets/${encodeURIComponent(ticketId)}/refresh`,
      { method: "POST" }
    );
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Failed to refresh the test ticket."));
    }
    if (payload?.ticket) {
      const index = state.tickets.findIndex((item) => Number(item.id) === Number(ticketId));
      if (index >= 0) state.tickets[index] = payload.ticket;
    }
    render();
  } catch (error) {
    if (!state.authError) {
      toast(error instanceof Error ? error.message : "Failed to refresh the test ticket.", "error");
    }
  }
}

/* -- Rendering ------------------------------------------------------------ */

function render() {
  if (state.authChecking) {
    appRoot.innerHTML = '<div class="at-empty">Loading…</div>';
    return;
  }
  if (!accessToken || !currentAccount) {
    appRoot.innerHTML = renderLogin();
    const form = document.querySelector("[data-at-login-form]");
    form?.addEventListener("submit", (event) => {
      event.preventDefault();
      handleLogin(form);
    });
    return;
  }
  appRoot.innerHTML = renderConsole();
  bindConsoleEvents();
}

function renderLogin() {
  return `
    <section class="at-login-page">
      <header class="at-login-header">
        <div class="at-login-brand" aria-label="Automation Test">
          <span class="material-symbols-outlined" aria-hidden="true">science</span>
          <strong>Automation Test</strong>
        </div>
      </header>
      <main class="at-login-main">
        <div class="at-login-content">
          <header class="at-login-heading">
            <h1>Welcome Back</h1>
            <p>Sign in to create and track Zendesk regression test tickets against the production pipeline.</p>
          </header>
          <section class="at-login-card" aria-label="Automation Test sign in">
            <form class="at-login-form" data-at-login-form>
              <label class="at-login-field">
                <span>Email</span>
                <span class="at-login-input-wrap">
                  <span class="material-symbols-outlined" aria-hidden="true">person</span>
                  <input name="email" autocomplete="username" placeholder="name@company.com" required maxlength="320" />
                </span>
              </label>
              <label class="at-login-field">
                <span>Password</span>
                <span class="at-login-input-wrap">
                  <span class="material-symbols-outlined" aria-hidden="true">lock</span>
                  <input name="password" type="password" autocomplete="current-password" placeholder="••••••••" required maxlength="200" />
                </span>
              </label>
              ${state.authError ? `<p class="at-login-error">${escapeHtml(state.authError)}</p>` : ""}
              <button class="at-login-submit" type="submit">Sign In</button>
            </form>
          </section>
        </div>
      </main>
    </section>
  `;
}

function renderConsole() {
  const mailConfigured = Boolean(state.mail?.configured);
  return `
    <div class="at-shell">
      <header class="at-topbar">
        <div class="at-topbar-brand">
          <span class="material-symbols-outlined" aria-hidden="true">science</span>
          <strong>Automation Test</strong>
        </div>
        <div class="at-topbar-account">
          <span>Signed in as <strong>${escapeHtml(currentAccount?.email || currentAccount?.account_id || "")}</strong></span>
          <button class="at-link-button" type="button" data-at-logout>Sign out</button>
        </div>
      </header>
      <main class="at-main">
        ${renderApprovalBanner()}
        ${renderMailNotice(mailConfigured)}
        <section>
          <h2 class="at-section-title">1. Pick a regression category</h2>
          <p class="at-section-sub">Each card loads an editable ticket template that reliably routes to that automation.</p>
          <div class="at-category-grid" style="margin-top: 12px;">
            ${state.templates.map(renderCategoryCard).join("")}
          </div>
        </section>
        ${state.selectedCategory ? renderFormCard(mailConfigured) : ""}
        ${renderTicketsCard()}
        ${renderScenariosCard()}
      </main>
    </div>
  `;
}

function renderMailNotice(mailConfigured) {
  const recipient = escapeHtml(state.mail?.recipient || "");
  const sender = escapeHtml(state.mail?.sender || "");
  const tag = escapeHtml(state.mail?.subject_tag || "");
  if (!mailConfigured) {
    const missing = (state.mail?.missing_config_keys || []).map(escapeHtml).join(", ");
    return `
      <div class="at-banner is-danger">
        <span class="material-symbols-outlined" aria-hidden="true">report</span>
        <span>Test mailbox is not configured on the server. Set <strong>${missing || "AUTOMATION_TEST_MAIL_*"}</strong> in the stack .env and restart the api_production service before creating tickets.</span>
      </div>
    `;
  }
  return `
    <div class="at-banner">
      <span class="material-symbols-outlined" aria-hidden="true">warning</span>
      <span>Sending creates a <strong>real Zendesk ticket</strong> and triggers real production automation: public replies, internal handoff emails and Slack notifications. From <strong>${sender}</strong> to <strong>${recipient}</strong>, subject tag <strong>${tag}</strong>. Clean up test tickets afterwards.</span>
    </div>
  `;
}

function renderCategoryCard(template) {
  const active = state.selectedCategory === template.id ? " is-active" : "";
  return `
    <button type="button" class="at-category-card${active}" data-at-category="${escapeHtml(template.id)}">
      <strong>${escapeHtml(template.label || template.id)}</strong>
      <span class="at-category-desc">${escapeHtml(template.description || "")}</span>
    </button>
  `;
}

function renderFormCard(mailConfigured) {
  const template = state.templates.find((item) => item.id === state.selectedCategory);
  const expected = (template?.expected || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  return `
    <section class="at-form-card" data-at-form-card>
      <div>
        <h2 class="at-section-title">2. Review and edit the ticket</h2>
        <p class="at-section-sub">Keep the "${escapeHtml(state.mail?.subject_tag || "")}" subject tag so test tickets stay identifiable in Zendesk.</p>
      </div>
      <label class="at-field">
        <span>Subject</span>
        <input data-at-subject maxlength="300" value="${escapeHtml(state.form.subject)}" />
      </label>
      <label class="at-field">
        <span>Body</span>
        <textarea data-at-body maxlength="40000">${escapeHtml(state.form.body)}</textarea>
      </label>
      <div>
        <strong style="font-size: 13px;">Expected outcomes</strong>
        <ul class="at-expected" style="margin-top: 8px;">${expected}</ul>
      </div>
      <div class="at-form-actions">
        <button class="at-button" type="button" data-at-create ${mailConfigured && !state.sending ? "" : "disabled"}>
          ${state.sending ? "Sending…" : "Create test ticket"}
        </button>
        <button class="at-button is-secondary" type="button" data-at-reset>Reset to template</button>
      </div>
    </section>
  `;
}

function renderTicketsCard() {
  return `
    <section class="at-table-card">
      <div class="at-table-head">
        <div>
          <h2 class="at-section-title">3. Tracked test tickets</h2>
          <p class="at-section-sub">Refresh links each sent email to its Zendesk ticket and pulls the live pipeline state.</p>
        </div>
        <button class="at-button is-secondary" type="button" data-at-refresh-all ${state.ticketsLoading ? "disabled" : ""}>
          ${state.ticketsLoading ? "Loading…" : "Refresh list"}
        </button>
      </div>
      ${state.tickets.length ? renderTicketsTable() : `<div class="at-empty">No test tickets yet. Create one above.</div>`}
    </section>
  `;
}

function renderApprovalBanner() {
  const waiting = state.runs.find((run) => run.status === "waiting_approval");
  if (!waiting) return "";
  const hint = waiting.approval_hint || {};
  const url = hint.zendesk_ticket_url
    ? `<a href="${escapeHtml(hint.zendesk_ticket_url)}" target="_blank" rel="noopener">${escapeHtml(hint.zendesk_ticket_url)}</a>`
    : "";
  return `
    <div class="at-banner is-approval" data-at-approval-banner>
      <span class="material-symbols-outlined" aria-hidden="true">approval</span>
      <span>
        <strong>MANUAL APPROVAL REQUIRED</strong> (scenario ${escapeHtml(waiting.scenario_id)}) —
        from YOUR mailbox reply to the internal email whose subject starts with
        <strong>${escapeHtml(hint.internal_email_subject_prefix || "[Enablement Request]")}</strong>
        with a sentence such as “${escapeHtml(hint.suggested_reply || "")}”.
        Ticket: ${url}
      </span>
    </div>
  `;
}

function renderScenariosCard() {
  const active = activeRun();
  const scenarioCards = state.scenarios
    .map(
      (scenario) => `
        <div class="at-scenario-card">
          <strong>${escapeHtml(scenario.id)} · ${escapeHtml(scenario.label)}</strong>
          <span class="at-category-desc">${escapeHtml(scenario.description || "")}</span>
          <button class="at-button" type="button" data-at-run-scenario="${escapeHtml(scenario.id)}" ${active ? "disabled" : ""}>
            ${active ? "A run is active" : "Run scenario"}
          </button>
        </div>
      `
    )
    .join("");
  const runs = state.runs.length
    ? state.runs.map(renderRunRow).join("")
    : `<div class="at-empty">No scenario runs yet. Pick a scenario above (E1 recommended for the first run).</div>`;
  return `
    <section class="at-table-card">
      <div class="at-table-head">
        <div>
          <h2 class="at-section-title">4. Scenario runs (automated)</h2>
          <p class="at-section-sub">Each run plays a full multi-turn conversation against production and reports a PASS/FAIL matrix. Enablement runs pause for your internal approval.</p>
        </div>
        <button class="at-button is-secondary" type="button" data-at-refresh-runs ${state.runsLoading ? "disabled" : ""}>
          ${state.runsLoading ? "Loading…" : "Refresh runs"}
        </button>
      </div>
      <div class="at-scenario-grid">${scenarioCards}</div>
      <div class="at-runs-list">${runs}</div>
    </section>
  `;
}

function runStatusChip(run) {
  const label = RUN_STATUS_LABELS[run.status] || run.status;
  const tone =
    run.status === "completed"
      ? " is-good"
      : ["failed", "cancelled", "interrupted"].includes(run.status)
        ? " is-bad"
        : run.status === "waiting_approval"
          ? " is-warn"
          : "";
  return `<span class="chip${tone}">${escapeHtml(label)}</span>`;
}

function renderRunRow(run) {
  const created = String(run.created_at || "").replace("T", " ").slice(0, 19);
  const ticket = run.zendesk_ticket_url
    ? `<a href="${escapeHtml(run.zendesk_ticket_url)}" target="_blank" rel="noopener">#${escapeHtml(run.zendesk_ticket_id || "?")}</a>`
    : "—";
  const active = ["queued", "running", "waiting_approval"].includes(run.status);
  const steps = (run.steps || [])
    .map(
      (step) =>
        `<li>${step.status === "PASS" ? "✓" : "✗"} ${escapeHtml(step.step)}${step.detail ? ` — <span class="at-muted">${escapeHtml(step.detail)}</span>` : ""}</li>`
    )
    .join("");
  return `
    <div class="at-run-row">
      <div class="at-chip-row">
        <strong>${escapeHtml(run.scenario_id)}</strong>
        ${runStatusChip(run)}
        <span class="at-muted">${escapeHtml(created)}</span>
        <span>Zendesk ${ticket}</span>
        ${active ? `<button class="at-link-button" type="button" data-at-cancel-run="${escapeHtml(run.run_id)}">Cancel</button>` : ""}
      </div>
      ${run.current_step ? `<div class="at-muted">current: ${escapeHtml(run.current_step)}</div>` : ""}
      ${run.error ? `<div class="at-run-error">${escapeHtml(run.error)}</div>` : ""}
      ${steps ? `<details class="at-run-steps"><summary>steps (${(run.steps || []).length})</summary><ul>${steps}</ul></details>` : ""}
    </div>
  `;
}

function sendChip(ticket) {
  if (ticket.send_status === "sent") return `<span class="chip is-good">email sent</span>`;
  return `<span class="chip is-bad" title="${escapeHtml(ticket.send_error || "")}">email failed</span>`;
}

function linkCell(ticket) {
  if (ticket.link_status === "linked") {
    const id = escapeHtml(ticket.zendesk_ticket_id || "?");
    const url = ticket.zendesk_ticket_url
      ? `<a href="${escapeHtml(ticket.zendesk_ticket_url)}" target="_blank" rel="noopener">#${id}</a>`
      : `#${id}`;
    return `<strong>${url}</strong>`;
  }
  if (ticket.link_status === "not_found") {
    return `<span class="chip is-warn">not linked yet</span>`;
  }
  return `<span class="chip">pending</span>`;
}

function snapshotChips(ticket) {
  const snapshot = ticket.linked_case_snapshot || {};
  const chips = [];
  const executionAction = String(snapshot.execution_action || "");
  if (executionAction) {
    chips.push(
      executionAction === ticket.category
        ? `<span class="chip is-good">route: ${escapeHtml(executionAction)}</span>`
        : `<span class="chip is-bad">route: ${escapeHtml(executionAction)}</span>`
    );
  }
  const automationStatus = String(snapshot.automation_status || "");
  if (automationStatus) chips.push(`<span class="chip">automation: ${escapeHtml(automationStatus)}</span>`);
  const internalEmail = String(snapshot.internal_email_send_status || "");
  if (internalEmail) {
    chips.push(
      `<span class="chip${internalEmail === "sent" ? " is-good" : internalEmail === "failed" ? " is-bad" : ""}">internal email: ${escapeHtml(internalEmail)}</span>`
    );
  }
  const replyJob = snapshot.reply_job ? String(snapshot.reply_job.status || "") : "";
  if (replyJob) {
    chips.push(
      `<span class="chip${replyJob === "published" ? " is-good" : replyJob === "failed" || replyJob === "manual_attention" ? " is-bad" : ""}">reply: ${escapeHtml(replyJob)}</span>`
    );
  }
  const zendeskStatus = String(snapshot.zendesk_ticket_status || "");
  if (zendeskStatus) chips.push(`<span class="chip">zendesk: ${escapeHtml(zendeskStatus)}</span>`);
  return chips.join("");
}

function renderTicketsTable() {
  const rows = state.tickets
    .map((ticket) => {
      const created = String(ticket.created_at || "").replace("T", " ").slice(0, 19);
      return `
        <tr>
          <td class="at-muted">#${escapeHtml(ticket.id)}</td>
          <td>${escapeHtml(created)}</td>
          <td>${escapeHtml(CATEGORY_LABELS[ticket.category] || ticket.category)}</td>
          <td>${escapeHtml(ticket.subject)}</td>
          <td>${sendChip(ticket)}</td>
          <td>${linkCell(ticket)}</td>
          <td>
            <div class="at-chip-row">
              ${snapshotChips(ticket) || '<span class="at-muted">—</span>'}
            </div>
            ${ticket.send_error ? `<div class="at-muted" style="margin-top: 6px;">${escapeHtml(ticket.send_error)}</div>` : ""}
          </td>
          <td>
            <button class="at-link-button" type="button" data-at-refresh-row="${escapeHtml(ticket.id)}">Refresh</button>
          </td>
        </tr>
      `;
    })
    .join("");
  return `
    <div class="at-table-wrap">
      <table class="at-table">
        <thead>
          <tr>
            <th>ID</th><th>Created</th><th>Category</th><th>Subject</th>
            <th>Send</th><th>Zendesk</th><th>Pipeline</th><th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function bindConsoleEvents() {
  document.querySelector("[data-at-logout]")?.addEventListener("click", () => {
    clearAuth();
    state.selectedCategory = "";
    state.form = { subject: "", body: "" };
    render();
  });
  document.querySelectorAll("[data-at-category]").forEach((node) => {
    node.addEventListener("click", () => selectCategory(node.getAttribute("data-at-category")));
  });
  const subjectInput = document.querySelector("[data-at-subject]");
  const bodyInput = document.querySelector("[data-at-body]");
  subjectInput?.addEventListener("input", () => {
    state.form.subject = subjectInput.value;
  });
  bodyInput?.addEventListener("input", () => {
    state.form.body = bodyInput.value;
  });
  document.querySelector("[data-at-create]")?.addEventListener("click", createTicket);
  document.querySelector("[data-at-reset]")?.addEventListener("click", () => {
    selectCategory(state.selectedCategory);
  });
  document.querySelector("[data-at-refresh-all]")?.addEventListener("click", () => {
    loadTickets();
  });
  document.querySelectorAll("[data-at-refresh-row]").forEach((node) => {
    node.addEventListener("click", () => refreshTicket(node.getAttribute("data-at-refresh-row")));
  });
  document.querySelectorAll("[data-at-run-scenario]").forEach((node) => {
    node.addEventListener("click", () => startScenario(node.getAttribute("data-at-run-scenario")));
  });
  document.querySelectorAll("[data-at-cancel-run]").forEach((node) => {
    node.addEventListener("click", () => cancelRun(node.getAttribute("data-at-cancel-run")));
  });
  document.querySelector("[data-at-refresh-runs]")?.addEventListener("click", () => {
    loadScenarios();
  });
}

boot();
