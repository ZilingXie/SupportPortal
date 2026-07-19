const WORKSPACE_ACCESS_TOKEN_KEY = "supportportal_workspace_access_token";
const WORKSPACE_ACCOUNT_KEY = "supportportal_workspace_account";
const WORKSPACE_AUTH_KEY = "supportportal_workspace_selected_engineer";

const root = document.getElementById("workspace-admin-root");

let accessToken = readStorage(WORKSPACE_ACCESS_TOKEN_KEY, "");
let currentAccount = readStorage(WORKSPACE_ACCOUNT_KEY, null);
let adminSection = sectionFromHash();
let accounts = [];
let adminTickets = [];
let metrics = null;
let auditEvents = [];
let scheduleData = { timezone: "Asia/Shanghai", engineers: [] };
let selectedEngineerId = "";
let invitationResult = null;
let loading = false;
let loadError = "";

function sectionFromHash() {
  const section = String(globalThis.location?.hash || window.location?.hash || "").replace(/^#/, "");
  return ["overview", "engineers", "new-account", "active-tickets", "resolved-tickets", "audit"].includes(section)
    ? section
    : "overview";
}

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

function scheduleEngineers() {
  return Array.isArray(scheduleData?.engineers) ? scheduleData.engineers : [];
}

function assignableEngineerAccounts() {
  return scheduleEngineers().filter(
    (engineer) => engineer.is_on_schedule_now && engineer.availability === "available"
  );
}

function engineerInitials(engineer) {
  return String(engineer?.display_name || engineer?.account_id || "E").slice(0, 2).toUpperCase();
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
    <section class="admin-login-page">
      <header class="admin-login-header">
        <div class="admin-login-brand" aria-label="Admin">
          <span class="admin-login-brand-icon material-symbols-outlined" aria-hidden="true">ac_unit</span>
          <strong>Admin</strong>
        </div>
      </header>
      <main class="admin-login-main">
        <div class="admin-login-content">
          <header class="admin-login-heading">
            <h1>Welcome Back</h1>
            <p>An administrative workspace for managing engineer access, assignments, availability, and SLA health.</p>
          </header>
          <section class="admin-login-card" aria-label="Admin sign in">
            <form class="admin-login-form" data-admin-login-form>
              <label class="admin-login-field">
                <span>Account ID</span>
                <span class="admin-login-input-wrap">
                  <span class="material-symbols-outlined" aria-hidden="true">person</span>
                  <input name="account_id" autocomplete="username" placeholder="admin name" required maxlength="128" />
                </span>
              </label>
              <label class="admin-login-field">
                <span>Password</span>
                <span class="admin-login-input-wrap">
                  <span class="material-symbols-outlined" aria-hidden="true">lock</span>
                  <input name="password" type="password" autocomplete="current-password" placeholder="Password" required maxlength="512" />
                </span>
              </label>
              <p class="login-error admin-login-error" data-login-error role="alert"></p>
              <button class="btn btn-primary admin-login-submit" type="submit">
                <span>Sign In</span>
                <span class="material-symbols-outlined" aria-hidden="true">login</span>
              </button>
            </form>
          </section>
          <div class="admin-login-orbit" aria-hidden="true">
            <span class="material-symbols-outlined">data_usage</span>
          </div>
        </div>
      </main>
      <footer class="admin-login-footer">
        <strong>&copy; 2026 SupportPortal. Secure Admin Workspace.</strong>
        <nav aria-label="Admin resources">
          <span>Security Policy</span>
          <a href="https://status.agora.io/" target="_blank" rel="noopener noreferrer">System Status</a>
          <span>Help Desk</span>
        </nav>
      </footer>
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
  const activeNavSection = adminSection === "new-account" ? "engineers" : adminSection;
  const accountName = String(currentAccount?.display_name || currentAccount?.account_id || "Admin");
  return `
    <section class="admin-shell">
      <aside class="admin-sidebar">
        <a class="admin-rail-brand" href="#overview" data-section="overview" aria-label="Admin overview">
          <span class="admin-rail-brand-icon material-symbols-outlined" aria-hidden="true">admin_panel_settings</span>
          <span class="admin-rail-copy"><strong>Admin</strong><small>Dispatch control</small></span>
        </a>
        <div class="admin-sidebar-body">
          <nav class="admin-sidebar-nav" aria-label="Admin sections"><ul>
            ${navItems
              .map(
                ([id, icon, label]) => `
                  <li><a href="#${id}" data-section="${id}" class="${activeNavSection === id ? "is-active" : ""}" title="${escapeHtml(label)}">
                    <span class="material-symbols-outlined" aria-hidden="true">${icon}</span>
                    <span class="admin-rail-label">${escapeHtml(label)}</span>
                  </a></li>`
              )
              .join("")}
          </ul></nav>
        </div>
        <footer class="admin-rail-footer">
          <div class="admin-user-chip" title="${escapeHtml(accountName)}">
            <span class="admin-user-avatar">${escapeHtml(engineerInitials(currentAccount))}</span>
            <span class="admin-user-meta"><strong>${escapeHtml(accountName)}</strong><small>Administrator</small></span>
          </div>
          <button class="admin-logout-btn" type="button" data-action="sign-out" title="Sign out" aria-label="Sign out">
            <span class="material-symbols-outlined" aria-hidden="true">logout</span>
          </button>
        </footer>
      </aside>
      <main class="admin-main">
        ${loadError ? `<p class="login-error">${escapeHtml(loadError)}</p>` : ""}
        ${content}
      </main>
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
      ${renderMetricCard("Dispatch eligible", engineerMetrics.dispatch_eligible, `${engineerMetrics.on_schedule || 0} on schedule`, "groups")}
      ${renderMetricCard("Client Tickets", clientMetrics.total, `${clientMetrics.not_automated || 0} not automated`, "support_agent")}
      ${renderMetricCard("Rollout created", caseMetrics.rollout_created, "Engineer Cases from account rollout", "call_split")}
      ${renderMetricCard("SLA reassignments", caseMetrics.sla_reassigned, `${caseMetrics.availability_reassigned || 0} availability · ${caseMetrics.schedule_reassigned || 0} schedule`, "move_up")}
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
  const engineers = scheduleEngineers();
  const onSchedule = engineers.filter((engineer) => engineer.is_on_schedule_now);
  const selected = engineers.find((engineer) => engineer.account_id === selectedEngineerId);
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return `
    <header class="admin-main-header admin-management-header">
      <div><h1>Engineer Management</h1><p>Manage weekly coverage and dispatch eligibility.</p></div>
      <a class="btn btn-primary" href="#new-account" data-section="new-account">
        <span class="material-symbols-outlined" aria-hidden="true">person_add</span><span>New Account</span>
      </a>
    </header>
    <section class="admin-roster-section" aria-labelledby="on-schedule-title">
      <header class="admin-section-heading">
        <div><p class="admin-eyebrow">CURRENT COVERAGE</p><h2 id="on-schedule-title">On Schedule Now</h2></div>
        <span class="admin-count">${onSchedule.length}</span>
      </header>
      <div class="admin-roster-list">
        ${onSchedule.length ? onSchedule.map((engineer) => `
          <article class="admin-roster-person">
            <span class="admin-user-avatar">${escapeHtml(engineerInitials(engineer))}</span>
            <span class="admin-roster-copy"><strong>${escapeHtml(engineer.display_name)}</strong><small>${escapeHtml(engineer.account_id)}</small></span>
            ${statusPill(engineer.availability)}
            <button class="admin-icon-btn" type="button" data-action="edit-schedule" data-engineer-id="${escapeHtml(engineer.account_id)}" title="Modify shifts" aria-label="Modify ${escapeHtml(engineer.display_name)} shifts">
              <span class="material-symbols-outlined" aria-hidden="true">edit_calendar</span>
            </button>
          </article>`).join("") : `<p class="admin-empty-state">No engineers are scheduled for the current time.</p>`}
      </div>
    </section>
    <section class="admin-weekly-section" aria-labelledby="weekly-schedule-title">
      <header class="admin-section-heading">
        <div><p class="admin-eyebrow">${escapeHtml(scheduleData.timezone || "Asia/Shanghai")}</p><h2 id="weekly-schedule-title">Weekly Schedule</h2></div>
      </header>
      <div class="admin-roster-table-wrap">
        <table class="admin-roster-table">
          <thead><tr><th>Engineer</th>${days.map((day) => `<th>${day}</th>`).join("")}<th><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>
            ${engineers.length ? engineers.map((engineer) => {
              const shifts = new Map((engineer.shifts || []).map((shift) => [Number(shift.weekday), shift]));
              return `<tr>
                <td><strong>${escapeHtml(engineer.display_name)}</strong><small>${engineer.is_on_schedule_now ? "On schedule" : "Off schedule"} · ${escapeHtml(engineer.availability)}</small></td>
                ${days.map((_, weekday) => {
                  const shift = shifts.get(weekday);
                  return `<td>${shift ? `<span class="admin-shift-chip">${escapeHtml(shift.start)}–${escapeHtml(shift.end)}</span>` : `<span class="admin-day-off">Off</span>`}</td>`;
                }).join("")}
                <td><button class="admin-icon-btn" type="button" data-action="edit-schedule" data-engineer-id="${escapeHtml(engineer.account_id)}" title="Modify shifts" aria-label="Modify ${escapeHtml(engineer.display_name)} shifts"><span class="material-symbols-outlined" aria-hidden="true">edit</span></button></td>
              </tr>`;
            }).join("") : `<tr><td colspan="9" class="admin-empty-state">Invite an engineer to create the first schedule.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
    ${selected ? renderScheduleEditor(selected, days) : ""}
  `;
}

function renderScheduleEditor(engineer, days) {
  const shifts = new Map((engineer.shifts || []).map((shift) => [Number(shift.weekday), shift]));
  return `
    <div class="admin-editor-backdrop" data-action="close-schedule-editor"></div>
    <aside class="admin-schedule-editor" aria-label="Modify shifts" role="dialog" aria-modal="true">
      <header><div><p class="admin-eyebrow">MODIFY SHIFTS</p><h2>${escapeHtml(engineer.display_name)}</h2><small>${escapeHtml(scheduleData.timezone)}</small></div>
        <button class="admin-icon-btn" type="button" data-action="close-schedule-editor" title="Close" aria-label="Close schedule editor"><span class="material-symbols-outlined" aria-hidden="true">close</span></button>
      </header>
      <form data-schedule-form data-engineer-id="${escapeHtml(engineer.account_id)}">
        <div class="admin-shift-fields">
          ${days.map((day, weekday) => {
            const shift = shifts.get(weekday);
            return `<fieldset class="admin-shift-row">
              <label class="admin-shift-toggle"><input type="checkbox" name="day_${weekday}" ${shift ? "checked" : ""} /><span>${day}</span></label>
              <label><span>Start</span><input type="time" name="start_${weekday}" value="${escapeHtml(shift?.start || "09:00")}" /></label>
              <label><span>End</span><input type="time" name="end_${weekday}" value="${escapeHtml(shift?.end || "18:00")}" /></label>
            </fieldset>`;
          }).join("")}
        </div>
        <div class="admin-availability-fields">
          <label class="field"><span>Availability</span><select name="availability"><option value="available" ${engineer.availability === "available" ? "selected" : ""}>Available</option><option value="unavailable" ${engineer.availability !== "available" ? "selected" : ""}>Unavailable</option></select></label>
          <label class="field"><span>Reason</span><input name="reason" maxlength="500" value="${escapeHtml(engineer.availability_reason || "")}" /></label>
        </div>
        <p class="login-error" data-schedule-error role="alert"></p>
        <footer><button class="btn btn-ghost" type="button" data-action="close-schedule-editor">Cancel</button><button class="btn btn-primary" type="submit">Save Schedule</button></footer>
      </form>
    </aside>`;
}

function renderAdminNewAccount() {
  return `
    <section class="admin-invite-page">
      <a class="admin-back-link" href="#engineers" data-section="engineers"><span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>Engineer Management</a>
      <header class="admin-main-header"><div><p class="admin-eyebrow">ACCOUNT ACCESS</p><h1>Invite a workspace member</h1><p>Send a secure, single-use setup link by email.</p></div></header>
      <div class="admin-invite-layout">
        <form class="admin-invite-form" data-invitation-form>
          <label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" required maxlength="320" placeholder="name@company.com" /></label>
          <label class="field"><span>Role</span><select name="role"><option value="engineer">Engineer</option><option value="admin">Admin</option></select></label>
          <p class="login-error" data-invitation-error role="alert"></p>
          <button class="btn btn-primary" type="submit"><span class="material-symbols-outlined" aria-hidden="true">send</span><span>Send Invitation Email</span></button>
        </form>
        ${invitationResult ? `<aside class="admin-invite-success" role="status"><span class="material-symbols-outlined" aria-hidden="true">mark_email_read</span><div><strong>Invitation sent</strong><p>${escapeHtml(invitationResult.email)} can use the setup link until ${escapeHtml(formatDateTime(invitationResult.expires_at))}.</p></div></aside>` : ""}
      </div>
    </section>`;
}

function renderAdminTicketBoard(section = adminSection) {
  const targetStatus = section === "resolved-tickets" ? "resolved" : null;
  const cases = adminTickets.filter((ticket) =>
    targetStatus ? ticket.assignmentStatus === targetStatus : ticket.assignmentStatus !== "resolved"
  );
  const engineers = assignableEngineerAccounts();
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
    : adminSection === "new-account"
    ? renderAdminNewAccount()
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
    const [accountPayload, casePayload, metricPayload, auditPayload, schedulePayload] = await Promise.all([
      fetchJson("/api/workspace/admin/accounts"),
      fetchJson("/api/workspace/cases?assignment_status=all"),
      fetchJson("/api/workspace/admin/metrics"),
      fetchJson("/api/workspace/admin/audit?limit=200"),
      fetchJson("/api/workspace/admin/engineer-schedules"),
    ]);
    accounts = Array.isArray(accountPayload.accounts) ? accountPayload.accounts : [];
    adminTickets = Array.isArray(casePayload.cases) ? casePayload.cases.map(normalizeAdminTicket) : [];
    metrics = metricPayload || null;
    auditEvents = Array.isArray(auditPayload.events) ? auditPayload.events : [];
    scheduleData = schedulePayload || { timezone: "Asia/Shanghai", engineers: [] };
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
  scheduleData = { timezone: "Asia/Shanghai", engineers: [] };
  selectedEngineerId = "";
  invitationResult = null;
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

async function handleInvitation(form) {
  const data = new FormData(form);
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  form.querySelector("[data-invitation-error]").textContent = "";
  try {
    const payload = await fetchJson("/api/workspace/admin/invitations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: String(data.get("email") || "").trim(),
        role: String(data.get("role") || "engineer"),
      }),
    });
    invitationResult = payload.invitation;
    renderAdmin();
  } catch (error) {
    form.querySelector("[data-invitation-error]").textContent = error.message;
    submit.disabled = false;
  }
}

async function handleScheduleUpdate(form) {
  const data = new FormData(form);
  const engineerId = form.dataset.engineerId;
  const shifts = [];
  for (let weekday = 0; weekday < 7; weekday += 1) {
    if (!data.get(`day_${weekday}`)) continue;
    shifts.push({
      weekday,
      start: String(data.get(`start_${weekday}`) || ""),
      end: String(data.get(`end_${weekday}`) || ""),
    });
  }
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  form.querySelector("[data-schedule-error]").textContent = "";
  try {
    await fetchJson(`/api/workspace/admin/engineers/${encodeURIComponent(engineerId)}/availability`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        availability: String(data.get("availability") || "unavailable"),
        reason: String(data.get("reason") || "").trim() || null,
      }),
    });
    await fetchJson(`/api/workspace/admin/engineers/${encodeURIComponent(engineerId)}/schedule`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shifts }),
    });
    selectedEngineerId = "";
    await loadAdminData();
  } catch (error) {
    form.querySelector("[data-schedule-error]").textContent = error.message;
    submit.disabled = false;
  }
}

root.addEventListener("click", (event) => {
  const sectionLink = event.target.closest("[data-section]");
  if (sectionLink) {
    event.preventDefault();
    adminSection = sectionLink.dataset.section;
    selectedEngineerId = "";
    if (adminSection !== "new-account") invitationResult = null;
    if (globalThis.location) globalThis.location.hash = adminSection;
    renderAdmin();
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "sign-out") {
    signOut();
  } else if (action === "edit-schedule") {
    selectedEngineerId = event.target.closest("[data-engineer-id]")?.dataset.engineerId || "";
    renderAdmin();
  } else if (action === "close-schedule-editor") {
    selectedEngineerId = "";
    renderAdmin();
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
  if (form.matches("[data-invitation-form]")) {
    handleInvitation(form);
    return;
  }
  if (form.matches("[data-schedule-form]")) {
    handleScheduleUpdate(form);
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

window.addEventListener?.("hashchange", () => {
  adminSection = sectionFromHash();
  selectedEngineerId = "";
  renderAdmin();
});

renderAdmin();
if (isAdminAuthenticated()) {
  loadAdminData();
}
