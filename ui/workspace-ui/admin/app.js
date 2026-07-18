const WORKSPACE_ACCESS_TOKEN_KEY = "supportportal_workspace_access_token";
const WORKSPACE_ACCOUNT_KEY = "supportportal_workspace_account";
const WORKSPACE_AUTH_KEY = "supportportal_workspace_selected_engineer";

const root = document.getElementById("workspace-admin-root");

let accessToken = readStorage(WORKSPACE_ACCESS_TOKEN_KEY, "");
let currentAccount = readStorage(WORKSPACE_ACCOUNT_KEY, null);
let adminSection = "overview";
let accounts = [];
let adminTickets = [];
let metrics = null;
let auditEvents = [];
let loading = false;
let loadError = "";

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

async function fetchJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      reason = payload?.detail || reason;
    } catch {
      // Keep the status fallback.
    }
    if (response.status === 401) {
      signOut({ render: false });
    }
    throw new Error(reason);
  }
  return response.json();
}

function isAdminAuthenticated() {
  return Boolean(
    accessToken &&
      currentAccount &&
      String(currentAccount.role || "").toLowerCase() === "admin"
  );
}

function publicEngineerAccounts() {
  return accounts.filter(
    (account) => String(account.role || "").toLowerCase() === "engineer" && account.active !== false
  );
}

function normalizeAdminTicket(ticket) {
  const assignmentStatus = String(ticket?.assignment_status || "pending").trim().toLowerCase();
  return {
    id: String(ticket?.engineer_case_id || ticket?.ticket_id || "").trim(),
    clientTicket: String(ticket?.client_ticket_id || ticket?.client_ticket_ref?.ticket_id || "").trim(),
    title: String(ticket?.title || ticket?.subject || "Untitled Engineer Case").trim(),
    clientStatus: String(
      ticket?.client_status || ticket?.client_ticket_ref?.status || "open"
    ).trim().toLowerCase(),
    assignmentStatus: ["pending", "assigned", "resolved"].includes(assignmentStatus)
      ? assignmentStatus
      : "pending",
    assignedEngineerId: String(ticket?.assigned_engineer_id || "").trim(),
    assignmentVersion: Number(ticket?.assignment_version || 0),
    assignedAt: String(ticket?.assigned_at || "").trim(),
    slaDueAt: String(ticket?.sla_due_at || "").trim(),
    dispatchStatus: String(ticket?.dispatch_status || "pending").trim().toLowerCase(),
    updatedAt: String(ticket?.assignment_updated_at || ticket?.updated_at || "").trim(),
  };
}

function formatDateTime(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function statusPill(status) {
  const label = String(status || "unknown").replaceAll("_", " ");
  const tone = status === "assigned" || status === "available"
    ? "is-active"
    : status === "resolved"
    ? "is-available"
    : "is-offline";
  return `<span class="admin-work-status ${tone}">${escapeHtml(label)}</span>`;
}

function renderLogin() {
  root.innerHTML = `
    <section class="login-view">
      <aside class="login-intro">
        <p class="eyebrow">SupportPortal</p>
        <h1>Workspace Admin</h1>
        <p>Manage accounts, engineer availability, system assignment, SLA, and audit history.</p>
        <a class="btn btn-ghost" href="/workspace">Engineer Workspace</a>
      </aside>
      <section class="login-card panel-card">
        <div class="login-card-head">
          <p class="eyebrow">Admin account</p>
          <h2>Sign in</h2>
        </div>
        <form class="login-form" data-admin-login-form>
          <label class="field">
            <span>Account ID</span>
            <input name="account_id" autocomplete="username" required maxlength="128" />
          </label>
          <label class="field">
            <span>Password</span>
            <input name="password" type="password" autocomplete="current-password" required maxlength="512" />
          </label>
          <p class="login-error" data-login-error role="alert"></p>
          <button class="btn btn-primary" type="submit">Sign in</button>
        </form>
      </section>
    </section>
  `;
}

function renderAdminShell(content) {
  const navItems = [
    ["overview", "dashboard", "Operations Overview"],
    ["engineers", "groups", "Engineer Management"],
    ["active-tickets", "confirmation_number", "Active Engineer Cases"],
    ["resolved-tickets", "task_alt", "Resolved Engineer Cases"],
    ["audit", "history", "Audit"],
  ];
  return `
    <section class="admin-shell">
      <header class="admin-topbar">
        <div class="admin-topbar-brand">
          <span class="material-symbols-outlined" aria-hidden="true">admin_panel_settings</span>
          <div><strong>Workspace Admin</strong><span>System dispatch control</span></div>
        </div>
        <div class="admin-topbar-actions">
          <a class="admin-topbar-btn" href="/workspace" title="Engineer Workspace">
            <span class="material-symbols-outlined" aria-hidden="true">engineering</span>
          </a>
          <button class="admin-topbar-btn" type="button" data-action="refresh" title="Refresh">
            <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
          </button>
          <button class="admin-topbar-btn" type="button" data-action="sign-out" title="Sign out">
            <span class="material-symbols-outlined" aria-hidden="true">logout</span>
          </button>
          <span class="admin-topbar-avatar">${escapeHtml(
            String(currentAccount?.display_name || currentAccount?.account_id || "A").slice(0, 2).toUpperCase()
          )}</span>
        </div>
      </header>
      <div class="admin-body">
        <aside class="admin-sidebar">
          <nav class="admin-sidebar-nav" aria-label="Admin sections"><ul>
            ${navItems
              .map(
                ([id, icon, label]) => `
                  <li><a href="#${id}" data-section="${id}" class="${adminSection === id ? "is-active" : ""}">
                    <span class="material-symbols-outlined" aria-hidden="true">${icon}</span>
                    <span>${escapeHtml(label)}</span>
                  </a></li>`
              )
              .join("")}
          </ul></nav>
        </aside>
        <main class="admin-main">
          ${loadError ? `<p class="login-error">${escapeHtml(loadError)}</p>` : ""}
          ${content}
        </main>
      </div>
    </section>
  `;
}

function renderMetricCard(label, value, detail, icon) {
  return `
    <article class="admin-metric-card">
      <div class="admin-metric-card-top"><span class="admin-metric-label">${escapeHtml(label)}</span><span class="material-symbols-outlined">${icon}</span></div>
      <strong class="admin-metric-value">${escapeHtml(String(value ?? 0))}</strong>
      <span class="admin-metric-sub">${escapeHtml(detail)}</span>
    </article>
  `;
}

function renderOverview() {
  const caseMetrics = metrics?.engineer_cases || {};
  const engineerMetrics = metrics?.engineers || {};
  const clientMetrics = metrics?.client_tickets || {};
  const billingMetrics = metrics?.billing || {};
  return `
    <header class="admin-main-header">
      <div><h1>Operations Overview</h1><p>Current Engineer Case queue, availability, and SLA health.</p></div>
      <div class="admin-topbar-actions">
        <button class="btn btn-ghost" type="button" data-action="dispatch">Dispatch pending</button>
        <button class="btn btn-primary" type="button" data-action="reassign-due">Reassign overdue</button>
      </div>
    </header>
    <section class="admin-metric-grid">
      ${renderMetricCard("Pending", caseMetrics.pending, "Waiting for an available engineer", "pending_actions")}
      ${renderMetricCard("Assigned", caseMetrics.assigned, "SLA currently running", "assignment_ind")}
      ${renderMetricCard("SLA overdue", caseMetrics.sla_overdue, "Requires automatic reassignment", "timer_off")}
      ${renderMetricCard("Available engineers", engineerMetrics.available, `${engineerMetrics.total || 0} total engineers`, "groups")}
      ${renderMetricCard("Client Tickets", clientMetrics.total, `${clientMetrics.not_automated || 0} not automated`, "support_agent")}
      ${renderMetricCard("Rollout created", caseMetrics.rollout_created, "Engineer Cases from account rollout", "call_split")}
      ${renderMetricCard("SLA reassignments", caseMetrics.sla_reassigned, `${caseMetrics.availability_reassigned || 0} availability reassignments`, "move_up")}
      ${renderMetricCard("Email failures", billingMetrics.internal_email_failed, `${billingMetrics.automation || 0} billing automation tickets`, "mark_email_unread")}
    </section>
    <section class="admin-bottom-grid">
      <article class="admin-bottom-card">
        <header class="admin-bottom-card-header"><h3>Pending Engineer Cases</h3></header>
        <div class="admin-bottom-card-body">${renderCompactCases("pending")}</div>
      </article>
      <article class="admin-bottom-card">
        <header class="admin-bottom-card-header"><h3>Availability</h3></header>
        <div class="admin-bottom-card-body">${renderCompactEngineers()}</div>
      </article>
    </section>
  `;
}

function renderCompactCases(status) {
  const cases = adminTickets.filter((ticket) => ticket.assignmentStatus === status).slice(0, 8);
  if (!cases.length) return `<p class="admin-card-detail">No ${escapeHtml(status)} Engineer Cases.</p>`;
  return cases
    .map(
      (ticket) => `<div class="admin-triage-item"><div class="admin-triage-item-top"><strong class="admin-triage-id">${escapeHtml(
        ticket.id
      )}</strong>${statusPill(ticket.assignmentStatus)}</div><p class="admin-triage-summary">${escapeHtml(ticket.title)}</p></div>`
    )
    .join("");
}

function renderCompactEngineers() {
  const engineers = publicEngineerAccounts();
  if (!engineers.length) return `<p class="admin-card-detail">No engineer accounts.</p>`;
  return engineers
    .map(
      (engineer) => `<div class="admin-triage-item"><div class="admin-triage-item-top"><strong>${escapeHtml(
        engineer.display_name
      )}</strong>${statusPill(engineer.availability)}</div><p class="admin-triage-summary">${escapeHtml(
        engineer.availability_reason || "No availability reason"
      )}</p></div>`
    )
    .join("");
}

function renderAdminEngineerManagement() {
  const engineers = publicEngineerAccounts();
  return `
    <header class="admin-main-header"><div><h1>Engineer Management</h1><p>Create accounts and control dispatch availability.</p></div></header>
    <section class="admin-bottom-grid">
      <article class="admin-bottom-card">
        <header class="admin-bottom-card-header"><h3>Create account</h3></header>
        <div class="admin-bottom-card-body">
          <form class="login-form" data-create-account-form>
            <label class="field"><span>Account ID</span><input name="account_id" required maxlength="128" /></label>
            <label class="field"><span>Display name</span><input name="display_name" required maxlength="160" /></label>
            <label class="field"><span>Role</span><select name="role"><option value="engineer">Engineer</option><option value="admin">Admin</option></select></label>
            <label class="field"><span>Temporary password</span><input name="password" type="password" required minlength="10" maxlength="512" /></label>
            <p class="login-error" data-account-error role="alert"></p>
            <button class="btn btn-primary" type="submit">Create account</button>
          </form>
        </div>
      </article>
      <article class="admin-bottom-card">
        <header class="admin-bottom-card-header"><h3>Engineer availability</h3></header>
        <div class="admin-bottom-card-body">
          ${
            engineers.length
              ? engineers
                  .map(
                    (engineer) => `
                      <form class="admin-triage-item" data-availability-form data-engineer-id="${escapeHtml(engineer.account_id)}">
                        <div class="admin-triage-item-top"><strong>${escapeHtml(engineer.display_name)}</strong>${statusPill(
                          engineer.availability
                        )}</div>
                        <label class="field"><span>Availability</span><select name="availability">
                          <option value="available" ${engineer.availability === "available" ? "selected" : ""}>Available</option>
                          <option value="unavailable" ${engineer.availability !== "available" ? "selected" : ""}>Unavailable</option>
                        </select></label>
                        <label class="field"><span>Reason (optional)</span><input name="reason" maxlength="500" value="${escapeHtml(
                          engineer.availability_reason || ""
                        )}" /></label>
                        <button class="btn btn-ghost" type="submit">Update availability</button>
                      </form>`
                  )
                  .join("")
              : `<p class="admin-card-detail">Create an engineer account to begin dispatch.</p>`
          }
        </div>
      </article>
    </section>
  `;
}

function renderAdminTicketBoard(section = adminSection) {
  const targetStatus = section === "resolved-tickets" ? "resolved" : null;
  const cases = adminTickets.filter((ticket) =>
    targetStatus ? ticket.assignmentStatus === targetStatus : ticket.assignmentStatus !== "resolved"
  );
  const engineers = publicEngineerAccounts().filter((account) => account.availability === "available");
  return `
    <header class="admin-main-header"><div><h1>${targetStatus ? "Resolved" : "Active"} Engineer Cases</h1><p>Client Ticket status and Engineer Case assignment status are shown separately.</p></div></header>
    <section class="admin-pool-panel panel-card">
      <table class="admin-work-table">
        <thead><tr><th>Engineer Case</th><th>Client Ticket</th><th>Client status</th><th>Assignment</th><th>Assignee / SLA</th><th>Admin adjustment</th></tr></thead>
        <tbody>
          ${
            cases.length
              ? cases
                  .map(
                    (ticket) => `
                      <tr>
                        <td><strong>${escapeHtml(ticket.id)}</strong><span class="admin-work-ticket">${escapeHtml(ticket.title)}</span></td>
                        <td>${escapeHtml(ticket.clientTicket || "-")}</td>
                        <td>${statusPill(ticket.clientStatus)}</td>
                        <td>${statusPill(ticket.assignmentStatus)}</td>
                        <td>${escapeHtml(ticket.assignedEngineerId || "-")}<span class="admin-work-ticket">SLA ${escapeHtml(
                          formatDateTime(ticket.slaDueAt)
                        )}</span></td>
                        <td>
                          ${
                            ticket.assignmentStatus === "resolved"
                              ? "-"
                              : `<form class="admin-assignment-form" data-assignment-form data-case-id="${escapeHtml(
                                  ticket.id
                                )}" data-version="${ticket.assignmentVersion}">
                                  <select name="engineer_id"><option value="">Pending</option>${engineers
                                    .map(
                                      (engineer) => `<option value="${escapeHtml(engineer.account_id)}" ${
                                        engineer.account_id === ticket.assignedEngineerId ? "selected" : ""
                                      }>${escapeHtml(engineer.display_name)}</option>`
                                    )
                                    .join("")}</select>
                                  <input name="reason" value="admin_adjustment" required maxlength="500" aria-label="Assignment reason" />
                                  <button class="btn btn-ghost" type="submit">Save</button>
                                </form>`
                          }
                        </td>
                      </tr>`
                  )
                  .join("")
              : `<tr><td colspan="6">No Engineer Cases in this view.</td></tr>`
          }
        </tbody>
      </table>
    </section>
  `;
}

function renderAudit() {
  return `
    <header class="admin-main-header"><div><h1>Audit</h1><p>Account and availability administration events.</p></div></header>
    <section class="admin-pool-panel panel-card">
      <table class="admin-work-table"><thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Target</th><th>Reason</th></tr></thead><tbody>
        ${
          auditEvents.length
            ? auditEvents
                .map(
                  (event) => `<tr><td>${escapeHtml(formatDateTime(event.created_at))}</td><td>${escapeHtml(
                    event.event_type
                  )}</td><td>${escapeHtml(event.actor_id)}</td><td>${escapeHtml(event.target_id || "-")}</td><td>${escapeHtml(
                    event.payload?.reason || event.payload?.availability || "-"
                  )}</td></tr>`
                )
                .join("")
            : `<tr><td colspan="5">No audit events.</td></tr>`
        }
      </tbody></table>
    </section>
  `;
}

function renderAdmin() {
  if (!isAdminAuthenticated()) {
    renderLogin();
    return;
  }
  if (loading) {
    root.innerHTML = renderAdminShell(`<p class="admin-card-detail">Loading Workspace Admin...</p>`);
    return;
  }
  const content = adminSection === "engineers"
    ? renderAdminEngineerManagement()
    : adminSection === "active-tickets" || adminSection === "resolved-tickets"
    ? renderAdminTicketBoard(adminSection)
    : adminSection === "audit"
    ? renderAudit()
    : renderOverview();
  root.innerHTML = renderAdminShell(content);
}

async function loadAdminData() {
  if (!isAdminAuthenticated()) return;
  loading = true;
  loadError = "";
  renderAdmin();
  try {
    const [accountPayload, casePayload, metricPayload, auditPayload] = await Promise.all([
      fetchJson("/api/workspace/admin/accounts"),
      fetchJson("/api/workspace/cases?assignment_status=all"),
      fetchJson("/api/workspace/admin/metrics"),
      fetchJson("/api/workspace/admin/audit?limit=200"),
    ]);
    accounts = Array.isArray(accountPayload.accounts) ? accountPayload.accounts : [];
    adminTickets = Array.isArray(casePayload.cases) ? casePayload.cases.map(normalizeAdminTicket) : [];
    metrics = metricPayload || null;
    auditEvents = Array.isArray(auditPayload.events) ? auditPayload.events : [];
  } catch (error) {
    loadError = error.message;
  } finally {
    loading = false;
    renderAdmin();
  }
}

function signOut(options = {}) {
  removeStorage(WORKSPACE_ACCESS_TOKEN_KEY);
  removeStorage(WORKSPACE_ACCOUNT_KEY);
  removeStorage(WORKSPACE_AUTH_KEY);
  accessToken = "";
  currentAccount = null;
  accounts = [];
  adminTickets = [];
  metrics = null;
  auditEvents = [];
  if (options.render !== false) renderAdmin();
}

async function handleAdminLogin(form) {
  const data = new FormData(form);
  const payload = await fetchJson("/api/workspace/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      account_id: String(data.get("account_id") || "").trim(),
      password: String(data.get("password") || ""),
    }),
  });
  if (String(payload?.account?.role || "").toLowerCase() !== "admin") {
    throw new Error("Admin role required");
  }
  accessToken = payload.access_token;
  currentAccount = payload.account;
  writeStorage(WORKSPACE_ACCESS_TOKEN_KEY, accessToken);
  writeStorage(WORKSPACE_ACCOUNT_KEY, currentAccount);
  writeStorage(WORKSPACE_AUTH_KEY, currentAccount.account_id);
  await loadAdminData();
}

root.addEventListener("click", (event) => {
  const sectionLink = event.target.closest("[data-section]");
  if (sectionLink) {
    event.preventDefault();
    adminSection = sectionLink.dataset.section;
    renderAdmin();
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "sign-out") {
    signOut();
  } else if (action === "refresh") {
    loadAdminData();
  } else if (action === "dispatch") {
    fetchJson("/api/workspace/admin/dispatch", { method: "POST" }).then(loadAdminData).catch((error) => {
      loadError = error.message;
      renderAdmin();
    });
  } else if (action === "reassign-due") {
    fetchJson("/api/workspace/admin/reassign-due", { method: "POST" }).then(loadAdminData).catch((error) => {
      loadError = error.message;
      renderAdmin();
    });
  }
});

root.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.matches("[data-admin-login-form]")) {
    handleAdminLogin(form).catch((error) => {
      form.querySelector("[data-login-error]").textContent = error.message;
    });
    return;
  }
  if (form.matches("[data-create-account-form]")) {
    const data = new FormData(form);
    fetchJson("/api/workspace/admin/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(data.entries())),
    })
      .then(loadAdminData)
      .catch((error) => {
        form.querySelector("[data-account-error]").textContent = error.message;
      });
    return;
  }
  if (form.matches("[data-availability-form]")) {
    const data = new FormData(form);
    const engineerId = form.dataset.engineerId;
    fetchJson(`/api/workspace/admin/engineers/${encodeURIComponent(engineerId)}/availability`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(data.entries())),
    }).then(loadAdminData).catch((error) => {
      loadError = error.message;
      renderAdmin();
    });
    return;
  }
  if (form.matches("[data-assignment-form]")) {
    const data = new FormData(form);
    fetchJson(`/api/workspace/admin/cases/${encodeURIComponent(form.dataset.caseId)}/assignment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        engineer_id: String(data.get("engineer_id") || "").trim() || null,
        expected_version: Number(form.dataset.version || 0),
        reason: String(data.get("reason") || "admin_adjustment").trim(),
      }),
    }).then(loadAdminData).catch((error) => {
      loadError = error.message;
      renderAdmin();
    });
  }
});

renderAdmin();
if (isAdminAuthenticated()) {
  loadAdminData();
}
