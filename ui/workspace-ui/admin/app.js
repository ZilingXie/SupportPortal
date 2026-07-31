const WORKSPACE_ACCESS_TOKEN_KEY = "supportportal_admin_workspace_access_token";
const WORKSPACE_ACCOUNT_KEY = "supportportal_admin_workspace_account";
const WORKSPACE_AUTH_KEY = "supportportal_admin_workspace_account_id";
const ADMIN_SECTION_TITLES = {
  overview: "Operations Overview",
  "automated-cases": "Automated Cases",
  "agent-config": "Agent Config",
  "environment-config": "Environment Config",
  engineers: "Engineer Management",
  schedule: "Weekly Schedule",
  "new-account": "Invite a workspace member",
  "pending-assignment": "Pending Assignment",
  assigned: "Assigned",
  resolved: "Resolved",
  audit: "Audit",
};

const root = document.getElementById("workspace-admin-root");

let accessToken = readStorage(WORKSPACE_ACCESS_TOKEN_KEY, "");
let currentAccount = readStorage(WORKSPACE_ACCOUNT_KEY, null);
let adminSection = sectionFromHash();
let selectedAgentPath = agentPathFromHash();
let accounts = [];
let adminTickets = [];
let metrics = null;
let auditEvents = [];
let scheduleData = { timezone: "Asia/Shanghai", engineers: [] };
let automationData = { metrics: {}, cases: [] };
let automationRouteStatus = "automated";
let agentConfigData = null;
let agentConfigLoading = false;
let agentConfigLoadError = "";
let expandedAgentKeys = new Set();
let selectedAgentViews = {};
let selectedAgentPrompts = {};
let selectedPromptVersions = {};
let promptEditorKeys = new Set();
let promptDiffKeys = new Set();
let promptOperationNotice = {};
let promptOperationBusy = false;
let promptDraftValues = {};
let selectedPersonaKey = "";
let comparePersonaVersions = [];
let personaDraftValues = {};
let personaOperationNotice = "";
let personaOperationBusy = false;
let personaCreateOpen = false;
let environmentData = { names: [], items: [] };
let environmentLoadError = "";
let environmentQuery = "";
let selectedEngineerId = "";
let invitationResult = null;
let scheduleNotice = null;
let loading = false;
let loadError = "";

function sectionFromHash() {
  const section = String(globalThis.location?.hash || window.location?.hash || "").replace(/^#/, "");
  if (section === "route-strategy") return "agent-config";
  const rootSection = section.split("/")[0];
  return ["overview", "automated-cases", "agent-config", "environment-config", "engineers", "schedule", "new-account", "pending-assignment", "assigned", "resolved", "audit"].includes(rootSection)
    ? rootSection
    : "overview";
}

function agentPathFromHash() {
  const hash = String(globalThis.location?.hash || window.location?.hash || "").replace(/^#/, "");
  if (hash === "route-strategy") return ["route-agent"];
  const [section, ...path] = hash.split("/").filter(Boolean);
  return section === "agent-config" ? path : [];
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

function scheduleEngineers() {
  return Array.isArray(scheduleData?.engineers) ? scheduleData.engineers : [];
}

function engineerInitials(engineer) {
  return String(engineer?.display_name || engineer?.account_id || "E").slice(0, 2).toUpperCase();
}

function normalizeAdminTicket(ticket) {
  const assignmentStatus = String(ticket?.assignment_status || "pending").trim().toLowerCase();
  return {
    id: String(ticket?.engineer_case_id || ticket?.ticket_id || "").trim(),
    title: String(ticket?.title || ticket?.subject || "Untitled Engineer Case").trim(),
    clientStatus: String(
      ticket?.client_status || ticket?.client_ticket_ref?.status || "open"
    ).trim().toLowerCase(),
    assignmentStatus: ["pending", "assigned", "resolved"].includes(assignmentStatus)
      ? assignmentStatus
      : "pending",
    requester: String(ticket?.requester || ticket?.customer_id || "").trim(),
    priority: String(ticket?.priority || ticket?.client_ticket_ref?.priority || "").trim().toLowerCase(),
    assignedEngineerId: String(ticket?.assigned_engineer_id || "").trim(),
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
  const tone = status === "assigned" || status === "on_schedule"
    ? "is-active"
    : status === "resolved"
    ? "is-resolved"
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
            <p>An administrative workspace for managing engineer access, schedules, assignments, and SLA health.</p>
          </header>
          <section class="admin-login-card" aria-label="Admin sign in">
            <form class="admin-login-form" data-admin-login-form>
              <label class="admin-login-field">
                <span>Email</span>
                <span class="admin-login-input-wrap">
                  <span class="material-symbols-outlined" aria-hidden="true">person</span>
                  <input name="email" autocomplete="username" placeholder="name@company.com" required maxlength="320" />
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
    ["overview", "dashboard", "Operations Overview", "OV"],
    ["automated-cases", "automation", "Automated Cases", "AC"],
    ["agent-config", "smart_toy", "Agent Config", "AG"],
    ["environment-config", "settings", "Environment Config", "EC"],
    ["engineers", "groups", "Engineer Management", "EN"],
    ["schedule", "calendar_month", "Schedule", "SC"],
    ["pending-assignment", "pending_actions", "Pending Assignment", "PA"],
    ["assigned", "assignment_ind", "Assigned", "AS"],
    ["resolved", "task_alt", "Resolved", "RS"],
    ["audit", "history", "Audit", "AU"],
  ];
  const activeNavSection = adminSection === "new-account" ? "engineers" : adminSection;
  const accountName = String(currentAccount?.display_name || currentAccount?.account_id || "Admin");
  const sectionTitle = ADMIN_SECTION_TITLES[adminSection] || ADMIN_SECTION_TITLES.overview;
  return `
    <section class="admin-shell">
      <aside class="admin-sidebar">
        <a class="admin-rail-brand" href="#overview" data-section="overview" aria-label="Admin overview">
          <span class="admin-rail-brand-icon admin-rail-symbol-wrap" aria-hidden="true"><span class="material-symbols-outlined admin-rail-glyph">admin_panel_settings</span><span class="admin-rail-fallback">AD</span></span>
          <span class="admin-rail-copy"><strong>Admin</strong><small>Dispatch control</small></span>
        </a>
        <div class="admin-sidebar-body">
          <nav class="admin-sidebar-nav" aria-label="Admin sections"><ul>
            ${navItems
              .map(
                ([id, icon, label, fallback]) => `
                  <li><a href="#${id}" data-section="${id}" class="${activeNavSection === id ? "is-active" : ""}" title="${escapeHtml(label)}">
                    <span class="admin-rail-symbol-wrap" aria-hidden="true"><span class="material-symbols-outlined admin-rail-glyph">${icon}</span><span class="admin-rail-fallback">${fallback}</span></span>
                    <span class="admin-rail-label">${escapeHtml(label)}</span>
                  </a></li>`
              )
              .join("")}
          </ul></nav>
        </div>
        <footer class="admin-rail-footer">
          <button class="admin-logout-btn" type="button" data-action="sign-out" title="Sign out" aria-label="Sign out">
            <span class="admin-rail-symbol-wrap" aria-hidden="true"><span class="material-symbols-outlined admin-rail-glyph">logout</span><span class="admin-rail-fallback">LO</span></span>
            <span class="admin-rail-label">Logout</span>
          </button>
        </footer>
      </aside>
      <main class="admin-main">
        <header class="admin-workspace-topbar">
          <h1 title="${escapeHtml(sectionTitle)}">${escapeHtml(sectionTitle)}</h1>
          <div class="admin-account-chip" title="${escapeHtml(accountName)}">
            <span class="admin-account-avatar">${escapeHtml(engineerInitials(currentAccount))}</span>
            <span class="admin-account-meta"><strong>${escapeHtml(accountName)}</strong><small>Administrator</small></span>
          </div>
        </header>
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
      <div><p>Current Engineer Case queue, schedule coverage, and SLA health.</p></div>
      <div class="admin-topbar-actions">
        <button class="btn btn-ghost" type="button" data-action="dispatch">Dispatch pending</button>
        <button class="btn btn-primary" type="button" data-action="reassign-due">Reassign overdue</button>
      </div>
    </header>
    <section class="admin-metric-grid">
      ${renderMetricCard("Pending", caseMetrics.pending, "Waiting for an on-schedule engineer", "pending_actions")}
      ${renderMetricCard("Assigned", caseMetrics.assigned, "SLA currently running", "assignment_ind")}
      ${renderMetricCard("SLA overdue", caseMetrics.sla_overdue, "Requires automatic reassignment", "timer_off")}
      ${renderMetricCard("Dispatch eligible", engineerMetrics.dispatch_eligible, `${engineerMetrics.on_schedule || 0} on schedule`, "groups")}
      ${renderMetricCard("Client Tickets", clientMetrics.total, `${clientMetrics.not_automated || 0} not automated`, "support_agent")}
      ${renderMetricCard("Rollout created", caseMetrics.rollout_created, "Engineer Cases from account rollout", "call_split")}
      ${renderMetricCard("SLA reassignments", caseMetrics.sla_reassigned, `${caseMetrics.schedule_reassigned || 0} schedule reassignments`, "move_up")}
      ${renderMetricCard("Email failures", billingMetrics.internal_email_failed, `${billingMetrics.automation || 0} billing automation tickets`, "mark_email_unread")}
    </section>
    <section class="admin-bottom-grid">
      <article class="admin-bottom-card">
        <header class="admin-bottom-card-header"><h3>Pending Engineer Cases</h3></header>
        <div class="admin-bottom-card-body">${renderCompactCases("pending")}</div>
      </article>
      <article class="admin-bottom-card">
        <header class="admin-bottom-card-header"><h3>Schedule Coverage</h3></header>
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
  const engineers = scheduleEngineers();
  if (!engineers.length) return `<p class="admin-card-detail">No engineer accounts.</p>`;
  return engineers
    .map(
      (engineer) => `<div class="admin-triage-item"><div class="admin-triage-item-top"><strong>${escapeHtml(
        engineer.display_name
      )}</strong>${statusPill(engineer.is_on_schedule_now ? "on_schedule" : "off_schedule")}</div><p class="admin-triage-summary">${escapeHtml(
        engineer.is_on_schedule_now ? "Currently on schedule" : "Currently off schedule"
      )}</p></div>`
    )
    .join("");
}

function timeStringToMinutes(value, options = {}) {
  const match = /^(\d{2}):(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (options.allow24 && hours === 24 && minutes === 0) return 24 * 60;
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

function buildScheduleSegments(engineers) {
  const segments = [];
  engineers.forEach((engineer) => {
    (engineer.shifts || []).forEach((shift) => {
      const weekday = Number(shift.weekday);
      const startMinute = timeStringToMinutes(shift.start);
      const endMinute = timeStringToMinutes(shift.end, { allow24: true });
      if (!Number.isInteger(weekday) || weekday < 0 || weekday > 6 || startMinute === null || endMinute === null || startMinute === endMinute) {
        return;
      }
      const common = {
        engineer,
        shift,
        fullLabel: `${shift.start}-${shift.end}`,
      };
      if (endMinute > startMinute) {
        segments.push({ ...common, weekday, startMinute, endMinute, label: common.fullLabel });
        return;
      }
      segments.push({ ...common, weekday, startMinute, endMinute: 24 * 60, label: `${shift.start}-24:00` });
      if (endMinute > 0) {
        segments.push({ ...common, weekday: (weekday + 1) % 7, startMinute: 0, endMinute, label: `00:00-${shift.end}` });
      }
    });
  });
  return segments;
}

function assignScheduleLanes(segments) {
  const withLanes = [];
  for (let weekday = 0; weekday < 7; weekday += 1) {
    const daySegments = segments
      .filter((segment) => segment.weekday === weekday)
      .sort((left, right) => left.startMinute - right.startMinute || left.endMinute - right.endMinute);
    let laneEnds = [];
    let overlapGroup = [];
    let overlapGroupEnd = -1;
    let overlapGroupLaneCount = 1;
    const finishOverlapGroup = () => {
      overlapGroup.forEach((segment) => {
        segment.laneCount = overlapGroupLaneCount;
        withLanes.push(segment);
      });
      laneEnds = [];
      overlapGroup = [];
      overlapGroupEnd = -1;
      overlapGroupLaneCount = 1;
    };
    daySegments.forEach((segment) => {
      if (overlapGroup.length && segment.startMinute >= overlapGroupEnd) finishOverlapGroup();
      let lane = laneEnds.findIndex((endMinute) => endMinute <= segment.startMinute);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = segment.endMinute;
      overlapGroup.push({ ...segment, lane });
      overlapGroupEnd = Math.max(overlapGroupEnd, segment.endMinute);
      overlapGroupLaneCount = Math.max(overlapGroupLaneCount, laneEnds.length);
    });
    if (overlapGroup.length) finishOverlapGroup();
  }
  return withLanes;
}

function buildScheduleSlots(engineers) {
  return assignScheduleLanes(buildScheduleSegments(engineers)).flatMap((segment) => {
    const slots = [];
    const firstSlot = Math.floor(segment.startMinute / 30) * 30;
    for (let slotStart = firstSlot; slotStart < segment.endMinute; slotStart += 30) {
      const slotEnd = Math.min(slotStart + 30, 24 * 60);
      if (slotEnd <= segment.startMinute) continue;
      slots.push({ ...segment, slotStart, slotEnd });
    }
    return slots;
  });
}

function renderWeeklyTimeGrid(engineers, days) {
  const slots = buildScheduleSlots(engineers);
  const hourLabels = Array.from({ length: 24 }, (_, hour) => {
    const row = 2 + hour * 2;
    return `<span class="admin-week-time" data-hour="${hour}" style="grid-row:${row}">${String(hour).padStart(2, "0")}:00</span>`;
  }).join("");
  const dayColumns = days
    .map((day, weekday) => `<div class="admin-week-day" aria-hidden="true" style="grid-column:${weekday + 2};grid-row:2 / span 48"></div>`)
    .join("");
  const scheduleSlots = slots
    .map((slot) => {
      const row = 2 + Math.floor(slot.slotStart / 30);
      const startHour = String(Math.floor(slot.slotStart / 60)).padStart(2, "0");
      const startMinute = String(slot.slotStart % 60).padStart(2, "0");
      const endHour = String(Math.floor(slot.slotEnd / 60)).padStart(2, "0");
      const endMinute = String(slot.slotEnd % 60).padStart(2, "0");
      const slotLabel = `${startHour}:${startMinute}-${endHour}:${endMinute}`;
      return `<button class="admin-week-slot" type="button" role="gridcell"
        data-action="edit-schedule" data-engineer-id="${escapeHtml(slot.engineer.account_id)}"
        style="grid-column:${slot.weekday + 2};grid-row:${row};--lane:${slot.lane};--lane-count:${slot.laneCount}"
        aria-label="Modify ${escapeHtml(slot.engineer.display_name)} schedule, ${escapeHtml(days[slot.weekday])} ${escapeHtml(slotLabel)}">
        <span>${escapeHtml(slot.engineer.display_name)}</span>
      </button>`;
    })
    .join("");
  return `
    <div class="admin-week-grid-wrap" tabindex="0" aria-label="Weekly engineer schedule">
      <div class="admin-week-grid" role="grid">
        <div class="admin-week-corner" aria-hidden="true">TIME</div>
        ${days.map((day, weekday) => `<div class="admin-week-day-heading" role="columnheader" style="grid-column:${weekday + 2}">${escapeHtml(day)}</div>`).join("")}
        <div class="admin-week-time-column" aria-hidden="true"></div>
        ${hourLabels}
        ${dayColumns}
        ${scheduleSlots}
        ${slots.length ? "" : `<p class="admin-week-empty">No shifts scheduled.</p>`}
      </div>
    </div>`;
}

function renderAdminEngineerManagement() {
  const engineers = scheduleEngineers();
  const onSchedule = engineers.filter((engineer) => engineer.is_on_schedule_now);
  return `
    <header class="admin-main-header admin-management-header">
      <div><p>Monitor current coverage and update engineer schedules.</p></div>
      <a class="btn btn-primary admin-new-account-btn" href="#new-account" data-section="new-account">
        <span class="material-symbols-outlined" aria-hidden="true">add</span><span>New Account</span>
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
            <span class="admin-roster-copy"><strong>${escapeHtml(engineer.display_name)}</strong><small>${escapeHtml(engineer.email || engineer.account_id)}</small></span>
            ${statusPill("on_schedule")}
            <button class="admin-icon-btn" type="button" data-action="edit-schedule" data-engineer-id="${escapeHtml(engineer.account_id)}" title="Modify schedule" aria-label="Modify ${escapeHtml(engineer.display_name)} schedule">
              <span class="material-symbols-outlined" aria-hidden="true">edit_calendar</span>
            </button>
          </article>`).join("") : `<p class="admin-empty-state">No engineers are scheduled for the current time.</p>`}
      </div>
    </section>
    <section class="admin-roster-section" aria-labelledby="engineer-schedules-title">
      <header class="admin-section-heading">
        <div><p class="admin-eyebrow">${escapeHtml(scheduleData.timezone || "Asia/Shanghai")}</p><h2 id="engineer-schedules-title">Engineer Schedules</h2></div>
        <span class="admin-count">${engineers.length}</span>
      </header>
      <div class="admin-roster-list">
        ${engineers.length ? engineers.map((engineer) => `
          <article class="admin-roster-person">
            <span class="admin-user-avatar">${escapeHtml(engineerInitials(engineer))}</span>
            <span class="admin-roster-copy"><strong>${escapeHtml(engineer.display_name)}</strong><small>${escapeHtml(engineer.email || engineer.account_id)}</small></span>
            <span class="admin-roster-statuses">
              ${statusPill(engineer.is_on_schedule_now ? "on_schedule" : "off_schedule")}
            </span>
            <button class="admin-icon-btn" type="button" data-action="edit-schedule" data-engineer-id="${escapeHtml(engineer.account_id)}" title="Modify schedule" aria-label="Modify ${escapeHtml(engineer.display_name)} schedule">
              <span class="material-symbols-outlined" aria-hidden="true">edit_calendar</span>
            </button>
          </article>`).join("") : `<p class="admin-empty-state">No active engineer accounts.</p>`}
      </div>
    </section>
  `;
}

function renderAdminSchedule() {
  const engineers = scheduleEngineers();
  const selected = engineers.find((engineer) => engineer.account_id === selectedEngineerId);
  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  return `
    <header class="admin-main-header">
      <div><p>Review coverage and modify engineer shifts.</p></div>
    </header>
    ${scheduleNotice ? `<p class="admin-schedule-notice" role="status">${escapeHtml(scheduleNotice)}</p>` : ""}
    <section class="admin-weekly-section" aria-labelledby="weekly-schedule-title">
      <header class="admin-section-heading">
        <div><p class="admin-eyebrow">${escapeHtml(scheduleData.timezone || "Asia/Shanghai")}</p><h2 id="weekly-schedule-title">Schedule Grid</h2></div>
      </header>
      ${renderWeeklyTimeGrid(engineers, days)}
    </section>
    ${selected ? renderScheduleEditor(selected, days) : ""}
  `;
}

function renderScheduleEditor(engineer, days) {
  const shifts = new Map((engineer.shifts || []).map((shift) => [Number(shift.weekday), shift]));
  const hourOptions = (selected, allow24 = false) => Array.from({ length: allow24 ? 25 : 24 }, (_, hour) => {
    const value = String(hour).padStart(2, "0");
    return `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`;
  }).join("");
  const minuteOptions = (selected) => ["00", "30"].map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`).join("");
  const timeParts = (value, fallback, allow24 = false) => {
    const normalized = value === "23:59" && allow24 ? "24:00" : String(value || fallback);
    const [hour, minute] = normalized.split(":");
    return {
      hour: allow24 && hour === "24" ? "24" : /^(?:[01]\d|2[0-3])$/.test(hour) ? hour : fallback.slice(0, 2),
      minute: minute === "30" ? "30" : "00",
    };
  };
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
            const start = timeParts(shift?.start, "09:00");
            const end = timeParts(shift?.end, "18:00", true);
            return `<fieldset class="admin-shift-row">
              <label class="admin-shift-toggle"><input type="checkbox" name="day_${weekday}" ${shift ? "checked" : ""} /><span>${day}</span></label>
              <label><span>Start</span><span class="admin-time-selects"><select name="start_hour_${weekday}" aria-label="${day} start hour">${hourOptions(start.hour)}</select><select name="start_minute_${weekday}" aria-label="${day} start minute">${minuteOptions(start.minute)}</select></span></label>
              <label><span>End</span><span class="admin-time-selects"><select name="end_hour_${weekday}" data-end-hour="${weekday}" aria-label="${day} end hour">${hourOptions(end.hour, true)}</select><select name="end_minute_${weekday}" data-end-minute="${weekday}" aria-label="${day} end minute" ${end.hour === "24" ? "disabled" : ""}>${minuteOptions(end.hour === "24" ? "00" : end.minute)}</select></span></label>
            </fieldset>`;
          }).join("")}
        </div>
        <p class="login-error" data-schedule-error role="alert"></p>
        <footer><button class="btn btn-ghost" type="button" data-action="close-schedule-editor">Cancel</button><button class="btn btn-primary" type="submit" data-schedule-submit><span class="material-symbols-outlined" aria-hidden="true">save</span><span>Save Schedule</span></button></footer>
      </form>
    </aside>`;
}

function renderAdminNewAccount() {
  return `
    <section class="admin-invite-page">
      <a class="admin-back-link" href="#engineers" data-section="engineers"><span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>Engineer Management</a>
      <header class="admin-main-header"><div><p class="admin-eyebrow">ACCOUNT ACCESS</p><p>Send a secure, single-use setup link by email.</p></div></header>
      <div class="admin-invite-layout">
        <form class="admin-invite-form" data-invitation-form>
          <label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" required maxlength="320" placeholder="name@company.com" /></label>
          <label class="field"><span>Role</span><select name="role"><option value="engineer">Engineer</option><option value="admin">Admin</option></select></label>
          <p class="login-error" data-invitation-error role="alert"></p>
          <button class="btn btn-primary" type="submit" data-invitation-submit><span class="material-symbols-outlined" aria-hidden="true">send</span><span aria-live="polite">Send Invitation Email</span></button>
        </form>
        ${invitationResult ? `<aside class="admin-invite-success" role="status"><span class="material-symbols-outlined" aria-hidden="true">mark_email_read</span><div><strong>Invitation sent</strong><p>${escapeHtml(invitationResult.email)} can use the setup link until ${escapeHtml(formatDateTime(invitationResult.expires_at))}.</p></div></aside>` : ""}
      </div>
    </section>`;
}

function renderAdminTicketBoard(section = adminSection) {
  const tabs = [
    { section: "pending-assignment", status: "pending", label: "Pending Assignment" },
    { section: "assigned", status: "assigned", label: "Assigned" },
    { section: "resolved", status: "resolved", label: "Resolved" },
  ];
  const activeTab = tabs.find((tab) => tab.section === section) || tabs[0];
  const cases = adminTickets.filter((ticket) => ticket.assignmentStatus === activeTab.status);
  const showAssignee = activeTab.status !== "pending";
  const columnCount = showAssignee ? 6 : 5;
  return `
    <header class="admin-main-header admin-case-header"><div><p class="admin-eyebrow">ENGINEER CASES</p><p>Cases grouped by assignment status, with client ticket status shown independently.</p></div></header>
    <nav class="admin-case-tabs" aria-label="Engineer Case assignment status">
      ${tabs.map((tab) => `<a href="#${tab.section}" data-section="${tab.section}" class="${tab.section === activeTab.section ? "is-active" : ""}" ${tab.section === activeTab.section ? 'aria-current="page"' : ""}>${tab.label}</a>`).join("")}
    </nav>
    <section class="admin-pool-panel admin-case-table-wrap" aria-label="${activeTab.label} cases">
      <table class="admin-work-table admin-case-table ${showAssignee ? "has-assignee" : ""}">
        <thead><tr><th>ID</th><th>Subject</th><th>Status</th><th>Requester</th><th>Priority</th>${showAssignee ? "<th>Assignee</th>" : ""}</tr></thead>
        <tbody>
          ${
            cases.length
              ? cases
                  .map(
                    (ticket) => `
                      <tr>
                        <td><strong class="admin-case-id">${escapeHtml(ticket.id)}</strong></td>
                        <td><span class="admin-case-subject">${escapeHtml(ticket.title)}</span></td>
                        <td>${statusPill(ticket.clientStatus)}</td>
                        <td>${escapeHtml(ticket.requester || "—")}</td>
                        <td>${ticket.priority ? `<span class="admin-priority-pill">${escapeHtml(ticket.priority)}</span>` : '<span class="admin-case-empty">—</span>'}</td>
                        ${showAssignee ? `<td><span class="admin-case-assignee">${escapeHtml(ticket.assignedEngineerId || "—")}</span></td>` : ""}
                      </tr>`
                  )
                  .join("")
              : `<tr><td class="admin-case-empty-row" colspan="${columnCount}">No ${activeTab.label.toLowerCase()} cases.</td></tr>`
          }
        </tbody>
      </table>
    </section>
  `;
}

function renderAudit() {
  return `
    <header class="admin-main-header"><div><p>Account, schedule, and assignment administration events.</p></div></header>
    <section class="admin-pool-panel panel-card">
      <table class="admin-work-table"><thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Target</th><th>Reason</th></tr></thead><tbody>
        ${
          auditEvents.length
            ? auditEvents
                .map(
                  (event) => `<tr><td>${escapeHtml(formatDateTime(event.created_at))}</td><td>${escapeHtml(
                    event.event_type
                  )}</td><td>${escapeHtml(event.actor_id)}</td><td>${escapeHtml(event.target_id || "-")}</td><td>${escapeHtml(
                    event.payload?.reason || "-"
                  )}</td></tr>`
                )
                .join("")
            : `<tr><td colspan="5">No audit events.</td></tr>`
        }
      </tbody></table>
    </section>
  `;
}

function renderAutomatedCases() {
  const metric = automationData.metrics || {};
  const rate = Number(metric.automation_rate || 0) * 100;
  const cases = Array.isArray(automationData.cases) ? automationData.cases : [];
  return `
    <header class="admin-main-header"><div><p class="admin-eyebrow">ACCOUNT AUTOMATION</p><p>All /account cases. Automated means the final route was Automated, not that the case was resolved.</p></div></header>
    <section class="admin-metric-strip" aria-label="Account automation metrics">
      <div><span>Total account cases</span><strong>${Number(metric.total_account_cases || 0)}</strong></div>
      <div><span>Routed Automated</span><strong>${Number(metric.automated_cases || 0)}</strong></div>
      <div><span>Not Automated</span><strong>${Number(metric.not_automated_cases || 0)}</strong></div>
      <div class="is-emphasis"><span>Automation share</span><strong>${rate.toFixed(1)}%</strong></div>
    </section><form class="admin-filter-bar" data-automation-filter-form><select name="route_status"><option value="" ${automationRouteStatus ? "" : "selected"}>All routes</option><option value="automated" ${automationRouteStatus === "automated" ? "selected" : ""}>Automated</option><option value="not_automated" ${automationRouteStatus === "not_automated" ? "selected" : ""}>Not Automated</option></select><select name="category" aria-label="Automation category"><option value="automation">Automation</option></select><input name="created_from" type="date" aria-label="Created from" /><input name="created_to" type="date" aria-label="Created to" /><button class="btn btn-ghost" type="submit">Apply filters</button></form>
    <section class="admin-ops-surface"><table class="admin-work-table"><thead><tr><th>Account Case</th><th>Subject</th><th>Category</th><th>Subcategory</th><th>Route status</th><th>Created</th></tr></thead><tbody>${cases.length ? cases.map(item => `<tr><td>${escapeHtml(item.account_case_id || item.client_ticket_id || item.ticket_id)}</td><td>${escapeHtml(item.title || "Untitled")}</td><td>${escapeHtml(item.category === "automation" ? "Automation" : item.category || "-")}</td><td>${escapeHtml(String(item.subcategory || "-").replaceAll("_", " "))}</td><td>${statusPill(item.route_status || "not_automated")}</td><td>${escapeHtml(formatDateTime(item.created_at))}</td></tr>`).join("") : `<tr><td colspan="6">No /account cases.</td></tr>`}</tbody></table></section>`;
}

function agentStatusLabel(status) {
  return String(status || "unknown").replaceAll("_", " ");
}

function renderAgentBadge(label, tone = "") {
  return `<span class="admin-agent-badge ${tone}">${escapeHtml(label)}</span>`;
}

function renderAgentPromptPanel(entry) {
  const prompts = Array.isArray(entry.prompts) ? entry.prompts : [];
  if (!prompts.length) {
    return `<div class="admin-agent-empty"><span class="material-symbols-outlined" aria-hidden="true">text_snippet</span><p>No system prompt configured. This component is deterministic.</p></div>`;
  }
  const preferred = prompts.find(prompt => prompt.metadata?.is_published) || prompts[0];
  const selectedKey = selectedAgentPrompts[entry.key] || preferred.key;
  const selected = prompts.find(prompt => prompt.key === selectedKey) || preferred;
  const metadata = selected.metadata || {};
  if (metadata.managed) return renderManagedPromptPanel(entry, selected, prompts);
  const details = [
    selected.version ? `Version ${selected.version}` : "Unversioned",
    selected.component_key ? `Component ${selected.component_key}` : "",
    metadata.status ? agentStatusLabel(metadata.status) : "",
    metadata.scope ? `Scope ${metadata.scope}` : "",
    metadata.variant || "",
  ].filter(Boolean);
  return `
    <div class="admin-agent-prompt-layout">
      <nav class="admin-agent-prompt-list" aria-label="${escapeHtml(entry.name)} prompts">
        ${prompts.map(prompt => `<button type="button" data-action="select-agent-prompt" data-agent-key="${escapeHtml(entry.key)}" data-prompt-key="${escapeHtml(prompt.key)}" class="${prompt.key === selected.key ? "is-active" : ""}" aria-pressed="${prompt.key === selected.key ? "true" : "false"}"><strong>${escapeHtml(prompt.name)}</strong><small>${escapeHtml(prompt.version ? `v${prompt.version}` : prompt.component_key || "System prompt")}${prompt.metadata?.is_published ? " · Published" : ""}</small></button>`).join("")}
      </nav>
      <section class="admin-agent-prompt-viewer" aria-live="polite">
        <header><div><p class="admin-eyebrow">SYSTEM PROMPT</p><h3>${escapeHtml(selected.name)}</h3></div><span>${escapeHtml(details.join(" · "))}</span></header>
        ${metadata.change_note ? `<p class="admin-agent-change-note">${escapeHtml(metadata.change_note)}</p>` : ""}
        <pre tabindex="0">${escapeHtml(selected.content || "No prompt content available")}</pre>
      </section>
    </div>
  `;
}

function promptVersionLabel(version) {
  return `v${version.version} · ${agentStatusLabel(version.status)}`;
}

function buildPromptLineDiff(beforeContent, afterContent) {
  const before = String(beforeContent || "").split("\n");
  const after = String(afterContent || "").split("\n");
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < before.length - prefix
    && suffix < after.length - prefix
    && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  ) suffix += 1;
  const renderLines = (lines, tone) => lines.map((line, index) => {
    const changed = index >= prefix && index < lines.length - suffix;
    return `<span class="${changed ? `is-${tone}` : ""}">${escapeHtml(line) || " "}</span>`;
  }).join("");
  return {
    beforeHtml: renderLines(before, "removed"),
    afterHtml: renderLines(after, "added"),
    changed: prefix !== before.length || prefix !== after.length,
  };
}

function renderManagedPromptPanel(entry, selected, prompts) {
  const metadata = selected.metadata || {};
  const versions = Array.isArray(metadata.versions) ? metadata.versions : [];
  const active = versions.find(item => item.status === "active") || versions[0] || {};
  const scheduled = versions.find(item => item.status === "scheduled") || null;
  const selectedVersionNumber = selectedPromptVersions[selected.key] || active.version;
  const inspected = versions.find(item => Number(item.version) === Number(selectedVersionNumber)) || active;
  const isEditing = promptEditorKeys.has(selected.key);
  const isDiffing = promptDiffKeys.has(selected.key) && inspected.version !== active.version;
  const diff = buildPromptLineDiff(active.content, inspected.content);
  const draftValues = promptDraftValues[selected.key] || { content: active.content || "", change_note: "" };
  const notice = promptOperationNotice[selected.key];
  return `
    <div class="admin-agent-prompt-layout">
      <nav class="admin-agent-prompt-list" aria-label="${escapeHtml(entry.name)} prompts">
        ${prompts.map(prompt => `<button type="button" data-action="select-agent-prompt" data-agent-key="${escapeHtml(entry.key)}" data-prompt-key="${escapeHtml(prompt.key)}" class="${prompt.key === selected.key ? "is-active" : ""}" aria-pressed="${prompt.key === selected.key ? "true" : "false"}"><strong>${escapeHtml(prompt.name)}</strong><small>Active v${escapeHtml(prompt.metadata?.active_version || prompt.version || "-")}${prompt.metadata?.scheduled_version ? ` · Next v${escapeHtml(prompt.metadata.scheduled_version)}` : ""}</small></button>`).join("")}
      </nav>
      <section class="admin-agent-prompt-viewer admin-prompt-managed" aria-live="polite">
        <header><div><p class="admin-eyebrow">DEPLOYMENT-BOUND PROMPT</p><h3>${escapeHtml(selected.name)}</h3></div><div class="admin-prompt-statuses">${renderAgentBadge(`Active v${active.version}`, "is-active")}${scheduled ? renderAgentBadge(`Next deploy v${scheduled.version}`, "is-scheduled") : ""}</div></header>
        ${notice ? `<p class="admin-prompt-notice ${notice.tone === "error" ? "is-error" : ""}" role="status">${escapeHtml(notice.message)}</p>` : ""}
        <div class="admin-prompt-toolbar">
          <label><span>Version</span><select data-prompt-version-select="${escapeHtml(selected.key)}">${versions.map(item => `<option value="${item.version}" ${item.version === inspected.version ? "selected" : ""}>${escapeHtml(promptVersionLabel(item))}</option>`).join("")}</select></label>
          <button class="btn btn-ghost" type="button" data-action="toggle-prompt-diff" data-prompt-key="${escapeHtml(selected.key)}" ${inspected.version === active.version ? "disabled" : ""}><span class="material-symbols-outlined" aria-hidden="true">difference</span>Diff</button>
          <button class="btn btn-primary" type="button" data-action="edit-prompt" data-prompt-key="${escapeHtml(selected.key)}"><span class="material-symbols-outlined" aria-hidden="true">edit</span>New draft</button>
        </div>
        ${isEditing ? `<form class="admin-prompt-editor" data-prompt-draft-form data-prompt-key="${escapeHtml(selected.key)}" data-based-on-version="${active.version}"><label><span>Prompt</span><textarea name="content" required maxlength="100000" data-prompt-draft-content="${escapeHtml(selected.key)}">${escapeHtml(draftValues.content)}</textarea></label><label><span>Change note</span><input name="change_note" required maxlength="500" data-prompt-draft-note="${escapeHtml(selected.key)}" value="${escapeHtml(draftValues.change_note)}" placeholder="Describe the intended behavior change" /></label><div><button class="btn btn-primary" type="submit" ${promptOperationBusy ? "disabled" : ""}>Save draft</button><button class="btn btn-ghost" type="button" data-action="cancel-prompt-edit" data-prompt-key="${escapeHtml(selected.key)}">Cancel</button></div></form>` : isDiffing ? `<div class="admin-prompt-diff"><section><header>Active v${active.version}</header><pre tabindex="0" class="admin-prompt-diff-lines">${diff.beforeHtml}</pre></section><section><header>Selected v${inspected.version}${diff.changed ? "" : " · No content changes"}</header><pre tabindex="0" class="admin-prompt-diff-lines">${diff.afterHtml}</pre></section></div>` : `<pre tabindex="0">${escapeHtml(inspected.content || "No prompt content available")}</pre>`}
        <div class="admin-prompt-version-meta"><span>${escapeHtml(inspected.change_note || "No change note")}</span><span>${escapeHtml(formatDateTime(inspected.created_at))}</span></div>
        <div class="admin-prompt-version-actions">
          ${inspected.status === "draft" ? `<button class="btn btn-primary" type="button" data-action="schedule-prompt-version" data-prompt-key="${escapeHtml(selected.key)}" data-prompt-version="${inspected.version}" ${promptOperationBusy ? "disabled" : ""}>Schedule for next deploy</button>` : ""}
          ${inspected.status === "scheduled" ? `<button class="btn btn-ghost" type="button" data-action="unschedule-prompt-version" data-prompt-key="${escapeHtml(selected.key)}" data-prompt-version="${inspected.version}" ${promptOperationBusy ? "disabled" : ""}>Unschedule</button>` : ""}
          ${inspected.status === "superseded" ? `<button class="btn btn-ghost" type="button" data-action="restore-prompt-version" data-prompt-key="${escapeHtml(selected.key)}" data-prompt-version="${inspected.version}" ${promptOperationBusy ? "disabled" : ""}><span class="material-symbols-outlined" aria-hidden="true">restore</span>Restore as draft</button>` : ""}
        </div>
      </section>
    </div>`;
}

async function runPromptVersionAction(action, promptKey, version) {
  promptOperationBusy = true;
  promptOperationNotice[promptKey] = null;
  renderAdmin();
  try {
    const payload = await fetchJson(`/api/workspace/admin/prompts/${encodeURIComponent(promptKey)}/versions/${version}/${action}`, { method: "POST" });
    if (action === "restore" && payload?.version?.version) selectedPromptVersions[promptKey] = payload.version.version;
    promptOperationNotice[promptKey] = { tone: "success", message: action === "schedule" ? "Scheduled for the next daily deployment." : `${action[0].toUpperCase()}${action.slice(1)} completed.` };
    agentConfigData = null;
    await loadAgentConfig({ force: true });
  } catch (error) {
    promptOperationNotice[promptKey] = { tone: "error", message: error.message };
  } finally {
    promptOperationBusy = false;
    renderAdmin();
  }
}

async function createPromptDraft(form) {
  const promptKey = form.dataset.promptKey;
  const values = new FormData(form);
  promptDraftValues[promptKey] = {
    content: String(values.get("content") || ""),
    change_note: String(values.get("change_note") || ""),
  };
  promptOperationBusy = true;
  renderAdmin();
  try {
    const payload = await fetchJson(`/api/workspace/admin/prompts/${encodeURIComponent(promptKey)}/drafts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: values.get("content"), change_note: values.get("change_note"), based_on_version: Number(form.dataset.basedOnVersion) }),
    });
    selectedPromptVersions[promptKey] = payload.version.version;
    promptEditorKeys.delete(promptKey);
    delete promptDraftValues[promptKey];
    promptOperationNotice[promptKey] = { tone: "success", message: `Draft v${payload.version.version} saved. It is not live until scheduled and deployed.` };
    agentConfigData = null;
    await loadAgentConfig({ force: true });
  } catch (error) {
    promptOperationNotice[promptKey] = { tone: "error", message: error.message };
  } finally {
    promptOperationBusy = false;
    renderAdmin();
  }
}

async function refreshAgentConfigAfterPersonaOperation(message) {
  personaOperationNotice = message;
  await loadAgentConfig({ force: true, render: false });
}

async function createPersona(form) {
  const values = new FormData(form);
  personaOperationBusy = true;
  personaOperationNotice = "";
  renderAdmin();
  try {
    const payload = await fetchJson("/api/workspace/admin/account-personas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        persona_key: String(values.get("persona_key") || "").trim(),
        display_name: String(values.get("display_name") || "").trim(),
        content: {
          instruction: String(values.get("instruction") || "").trim(),
          opener: "",
          signoff_name: "Sid",
        },
      }),
    });
    selectedPersonaKey = String(values.get("persona_key") || "").trim();
    personaCreateOpen = false;
    await refreshAgentConfigAfterPersonaOperation(`Persona v${payload.version.version} created and published.`);
  } catch (error) {
    personaOperationNotice = error.message;
  } finally {
    personaOperationBusy = false;
    renderAdmin();
  }
}

async function createPersonaDraft(form) {
  const personaKey = form.dataset.personaKey;
  const values = new FormData(form);
  personaDraftValues[personaKey] = {
    instruction: String(values.get("instruction") || ""),
    opener: String(values.get("opener") || ""),
    signoff_name: String(values.get("signoff_name") || "Sid"),
    change_note: String(values.get("change_note") || ""),
  };
  personaOperationBusy = true;
  personaOperationNotice = "";
  renderAdmin();
  try {
    const payload = await fetchJson(`/api/workspace/admin/account-personas/${encodeURIComponent(personaKey)}/drafts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: {
          instruction: values.get("instruction"),
          opener: values.get("opener"),
          signoff_name: values.get("signoff_name"),
        },
        change_note: values.get("change_note"),
        based_on_version: Number(values.get("based_on_version")) || null,
      }),
    });
    delete personaDraftValues[personaKey];
    await refreshAgentConfigAfterPersonaOperation(`Persona draft v${payload.version.version} saved.`);
  } catch (error) {
    personaOperationNotice = error.message;
  } finally {
    personaOperationBusy = false;
    renderAdmin();
  }
}

async function runPersonaVersionAction(action, personaKey, version) {
  personaOperationBusy = true;
  personaOperationNotice = "";
  renderAdmin();
  try {
    const payload = await fetchJson(`/api/workspace/admin/account-personas/${encodeURIComponent(personaKey)}/versions/${version}/${action}`, { method: "POST" });
    delete personaDraftValues[personaKey];
    comparePersonaVersions = [];
    await refreshAgentConfigAfterPersonaOperation(`${action === "publish" ? "Published" : "Rolled back"} as v${payload.version.version}.`);
  } catch (error) {
    personaOperationNotice = error.message;
  } finally {
    personaOperationBusy = false;
    renderAdmin();
  }
}

async function setPersonaEnabled(personaKey, enabled) {
  personaOperationBusy = true;
  personaOperationNotice = "";
  renderAdmin();
  try {
    await fetchJson(`/api/workspace/admin/account-personas/${encodeURIComponent(personaKey)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    await refreshAgentConfigAfterPersonaOperation(`Persona ${enabled ? "enabled" : "disabled"}.`);
  } catch (error) {
    personaOperationNotice = error.message;
  } finally {
    personaOperationBusy = false;
    renderAdmin();
  }
}

function renderAgentSkillsPanel(entry) {
  const skills = Array.isArray(entry.skills) ? entry.skills : [];
  if (!skills.length) {
    return `<div class="admin-agent-empty"><span class="material-symbols-outlined" aria-hidden="true">extension_off</span><p>No formal skill registry configured.</p></div>`;
  }
  return `<div class="admin-agent-capability-list">${skills.map(skill => `<div><code>${escapeHtml(skill.key)}</code><strong>${escapeHtml(skill.name)}</strong><p>${escapeHtml(skill.description)}</p></div>`).join("")}</div>`;
}

function renderAgentMcpPanel(entry) {
  const servers = Array.isArray(entry.mcp_servers) ? entry.mcp_servers : [];
  if (!servers.length) {
    return `<div class="admin-agent-empty"><span class="material-symbols-outlined" aria-hidden="true">hub</span><p>No MCP configured.</p></div>`;
  }
  return `<div class="admin-agent-capability-list">${servers.map(server => `<div><code>${escapeHtml(server.key || server.name)}</code><strong>${escapeHtml(server.name)}</strong><p>${escapeHtml(server.description || "")}</p></div>`).join("")}</div>`;
}

function agentConfigAgents() {
  return Array.isArray(agentConfigData?.agents) ? agentConfigData.agents : [];
}

function findRouteNode(path) {
  if (path[0] !== "route-agent" || !agentConfigData?.route_navigation) return null;
  let node = agentConfigData.route_navigation;
  for (const key of path.slice(1)) {
    node = (node.children || []).find(child => child.key === key);
    if (!node) return null;
  }
  return node;
}

function agentPathHref(path) {
  return `#agent-config${path.length ? `/${path.map(encodeURIComponent).join("/")}` : ""}`;
}

function pathsMatch(left, right) {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function pathStartsWith(path, prefix) {
  return prefix.every((item, index) => path[index] === item);
}

function renderAgentTreeNode(node, path, depth = 0) {
  const selected = pathsMatch(selectedAgentPath.slice(0, path.length), path);
  const children = node.key === "automation-router" ? [] : (node.children || []);
  const expanded = children.length && (expandedAgentKeys.has(node.key) || pathStartsWith(selectedAgentPath, path));
  return `<li class="admin-agent-tree-item" data-depth="${depth}">
    <div class="admin-agent-tree-row ${selected ? "is-path" : ""}">
      <a href="${agentPathHref(path)}" class="${pathsMatch(selectedAgentPath, path) ? "is-active" : ""}" ${pathsMatch(selectedAgentPath, path) ? 'aria-current="page"' : ""}>
        <span class="material-symbols-outlined" aria-hidden="true">${node.kind === "agent" ? "smart_toy" : node.kind === "router" ? "account_tree" : node.kind === "handoff" ? "person_alert" : "arrow_outward"}</span>
        <span><strong>${escapeHtml(node.name)}</strong><small>${escapeHtml(node.description)}</small></span>
      </a>
      ${children.length ? `<button type="button" data-action="toggle-agent-tree" data-agent-key="${escapeHtml(node.key)}" aria-expanded="${expanded ? "true" : "false"}" title="${expanded ? "Collapse" : "Expand"} ${escapeHtml(node.name)}"><span class="material-symbols-outlined" aria-hidden="true">expand_more</span></button>` : ""}
    </div>
    ${children.length ? `<ul ${expanded ? "" : "hidden"}>${children.map(child => renderAgentTreeNode(child, [...path, child.key], depth + 1)).join("")}</ul>` : ""}
  </li>`;
}

function renderAgentTree(agents) {
  const routeEntry = agentConfigData.route_navigation;
  return `<nav class="admin-agent-tree" aria-label="Agent configuration"><ul>
    ${agents.map(agent => renderAgentTreeNode(
      agent.key === "route-agent" && routeEntry ? routeEntry : { ...agent, children: [] },
      [agent.key]
    )).join("")}
  </ul></nav>`;
}

function agentBreadcrumbItems(agents) {
  const items = [{ name: "Agent Config", path: [] }];
  if (!selectedAgentPath.length) return items;
  const agent = agents.find(item => item.key === selectedAgentPath[0]);
  if (!agent) return items;
  items.push({ name: agent.name, path: [agent.key] });
  if (agent.key !== "route-agent") return items;
  let node = agentConfigData.route_navigation;
  for (const key of selectedAgentPath.slice(1)) {
    node = (node?.children || []).find(child => child.key === key);
    if (!node) break;
    items.push({ name: node.name, path: selectedAgentPath.slice(0, items.length) });
  }
  return items;
}

function renderAgentBreadcrumbs(agents) {
  const items = agentBreadcrumbItems(agents);
  return `<nav class="admin-agent-breadcrumbs" aria-label="Agent configuration path">${items.map((item, index) => index === items.length - 1
    ? `<span aria-current="page">${escapeHtml(item.name)}</span>`
    : `<a href="${agentPathHref(item.path)}">${escapeHtml(item.name)}</a><span class="material-symbols-outlined" aria-hidden="true">chevron_right</span>`).join("")}</nav>`;
}

function renderAgentCatalogOverview(agents) {
  return `<section class="admin-agent-overview" aria-labelledby="agent-catalog-title">
    <header><p class="admin-eyebrow">RUNTIME INVENTORY</p><h2 id="agent-catalog-title">Configured Agents</h2><p>Select an Agent to inspect its responsibilities, routing, Prompt, skill, and MCP configuration.</p></header>
    <div class="admin-agent-directory">${agents.map(agent => `<a href="${agentPathHref([agent.key])}">
      <span class="admin-agent-summary-icon material-symbols-outlined" aria-hidden="true">smart_toy</span>
      <span><strong>${escapeHtml(agent.name)}</strong><small>${escapeHtml(agent.description)}</small></span>
      ${renderAgentBadge(agentStatusLabel(agent.status), agent.status === "feature_gated" ? "is-gated" : "is-active")}
      <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
    </a>`).join("")}</div>
  </section>`;
}

function promptsForRouteNode(routeAgent, node) {
  const promptKeys = new Set(node?.prompt_keys || []);
  return (routeAgent?.prompts || []).filter(prompt => promptKeys.has(prompt.key));
}

function renderCapabilities(capabilities, kind) {
  if (!capabilities.length) {
    const message = kind === "router"
      ? "Routing responsibilities are represented by the destinations and Prompt configuration below."
      : "This node is a routing outcome with no independent configuration.";
    return `<p class="admin-agent-detail-note">${message}</p>`;
  }
  return `<div class="admin-agent-components" aria-label="Capabilities">${capabilities.map(component => `<div><span class="material-symbols-outlined" aria-hidden="true">${component.status === "feature_gated" ? "lock_clock" : "check_circle"}</span><span><strong>${escapeHtml(component.name)}</strong><small>${escapeHtml(component.description)}</small></span></div>`).join("")}</div>`;
}

function renderRouteRuntime() {
  const runtime = agentConfigData.route_runtime || {};
  const stages = Array.isArray(runtime.stage_details) ? runtime.stage_details : [];
  return `<section class="admin-agent-runtime" aria-label="Route runtime"><div><span>Current route</span><strong>${escapeHtml(runtime.router_prompt_version || "unversioned")}</strong></div><ol>${stages.map(stage => `<li><strong>${escapeHtml(stage.name)}</strong><small>${escapeHtml(stage.description)}</small></li>`).join("")}</ol></section>`;
}

function renderNodeLinks(node, basePath, selectedBehavior = null) {
  const children = node?.children || [];
  if (!children.length) return "";
  return `<section class="admin-agent-destinations" aria-labelledby="agent-destinations-title"><header><h3 id="agent-destinations-title">${node.key === "automation-router" ? "Automation behavior" : "Next route"}</h3><span>${children.length}</span></header><div>${children.map(child => `<a href="${agentPathHref([...basePath, child.key])}" class="${selectedBehavior?.key === child.key ? "is-active" : ""}"><span><strong>${escapeHtml(child.name)}</strong><small>${escapeHtml(child.description)}</small></span><span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></a>`).join("")}</div></section>`;
}

function personaVersions(persona) {
  return Array.isArray(persona?.versions) ? persona.versions : [];
}

function publishedPersonaVersion(persona) {
  const versions = personaVersions(persona);
  return versions.find(item => Number(item.version) === Number(persona?.published_version)) || versions.at(-1) || null;
}

function renderAutomationPersonaPanel() {
  const personas = Array.isArray(agentConfigData.automation_personas) ? agentConfigData.automation_personas : [];
  const persona = personas.find(item => item.persona_key === selectedPersonaKey) || personas[0];
  if (!persona) return `<div class="admin-agent-empty"><p>No Automation Persona configured.</p></div>`;
  const versions = personaVersions(persona);
  const published = publishedPersonaVersion(persona);
  const draft = {
    instruction: published?.content?.instruction || "",
    opener: published?.content?.opener || "",
    signoff_name: published?.content?.signoff_name || "Sid",
    change_note: "",
    ...(personaDraftValues[persona.persona_key] || {}),
  };
  const leftVersion = comparePersonaVersions[0] || persona.published_version || versions[0]?.version;
  const rightVersion = comparePersonaVersions[1] || versions.at(-1)?.version;
  const compared = [leftVersion, rightVersion].map(version => versions.find(item => Number(item.version) === Number(version)));
  return `<section class="admin-persona-workspace" aria-label="Automation Persona management">
    <aside class="admin-persona-context">
      <header><div><p class="admin-eyebrow">UNIFIED VOICE</p><h3>Automation Persona</h3></div><button class="admin-icon-btn" type="button" data-action="toggle-persona-create" title="Create Persona" aria-label="Create Persona"><span class="material-symbols-outlined" aria-hidden="true">add</span></button></header>
      <p>Applied after the selected Automation behavior generates its business response.</p>
      ${personaCreateOpen ? `<form class="admin-persona-create" data-persona-create-form><label><span>Key</span><input name="persona_key" pattern="[a-z][a-z0-9-]{1,63}" placeholder="persona-key" required /></label><label><span>Name</span><input name="display_name" placeholder="Display name" required /></label><label><span>Instruction</span><textarea name="instruction" rows="4" required></textarea></label><div><button class="btn btn-primary" type="submit" ${personaOperationBusy ? "disabled" : ""}>Create</button><button class="btn btn-ghost" type="button" data-action="toggle-persona-create">Cancel</button></div></form>` : ""}
      <nav class="admin-persona-list" aria-label="Automation Personas">${personas.map(item => `<button type="button" data-action="select-persona" data-persona-key="${escapeHtml(item.persona_key)}" class="${item.persona_key === persona.persona_key ? "is-active" : ""}"><strong>${escapeHtml(item.display_name)}</strong><small>${item.enabled ? "Enabled" : "Disabled"} · Published v${escapeHtml(item.published_version || "-")}</small></button>`).join("")}</nav>
      <button class="btn btn-ghost" type="button" data-action="toggle-persona" data-persona-key="${escapeHtml(persona.persona_key)}" data-enabled="${persona.enabled ? "false" : "true"}" ${personaOperationBusy ? "disabled" : ""}>${persona.enabled ? "Disable Persona" : "Enable Persona"}</button>
      <div class="admin-version-list"><h4>Version history</h4>${versions.map(item => `<button type="button" data-action="rollback-persona" data-persona-key="${escapeHtml(persona.persona_key)}" data-version="${item.version}" ${item.status === "draft" || personaOperationBusy ? "disabled" : ""}><strong>v${item.version}</strong><span>${escapeHtml(item.status)}</span><small>${escapeHtml(item.change_note || "No change note")}</small></button>`).join("")}</div>
    </aside>
    <div class="admin-persona-editor">
      ${personaOperationNotice ? `<p class="admin-prompt-notice" role="status">${escapeHtml(personaOperationNotice)}</p>` : ""}
      <form class="admin-prompt-editor" data-persona-draft-form data-persona-key="${escapeHtml(persona.persona_key)}">
        <div class="admin-persona-editor-heading"><div><p class="admin-eyebrow">PUBLISHED v${escapeHtml(persona.published_version || "-")}</p><h3>${escapeHtml(persona.display_name)}</h3></div>${renderAgentBadge(persona.enabled ? "Enabled" : "Disabled", persona.enabled ? "is-active" : "is-gated")}</div>
        <label><span>Persona instruction</span><textarea name="instruction" rows="8" required data-persona-draft-field="instruction">${escapeHtml(draft.instruction)}</textarea></label>
        <label><span>Opener</span><input name="opener" value="${escapeHtml(draft.opener)}" data-persona-draft-field="opener" placeholder="Optional opening line" /></label>
        <label><span>Signoff name</span><input name="signoff_name" value="${escapeHtml(draft.signoff_name)}" required data-persona-draft-field="signoff_name" /></label>
        <label><span>Change note</span><input name="change_note" value="${escapeHtml(draft.change_note)}" required maxlength="500" data-persona-draft-field="change_note" /></label>
        <input type="hidden" name="based_on_version" value="${escapeHtml(persona.published_version || "")}" />
        <div class="admin-editor-actions"><button class="btn btn-ghost" type="submit" ${personaOperationBusy ? "disabled" : ""}>Save draft</button>${versions.filter(item => item.status === "draft").map(item => `<button class="btn btn-primary" type="button" data-action="publish-persona" data-persona-key="${escapeHtml(persona.persona_key)}" data-version="${item.version}" ${personaOperationBusy ? "disabled" : ""}>Publish v${item.version}</button>`).join("")}</div>
      </form>
      <section class="admin-version-compare"><header><h4>Compare versions</h4><div>${[0, 1].map(index => `<select data-version-compare="${index}" aria-label="Compare Persona version ${index + 1}">${versions.map(item => `<option value="${item.version}" ${Number(item.version) === Number(compared[index]?.version) ? "selected" : ""}>v${item.version} · ${escapeHtml(item.status)}</option>`).join("")}</select>`).join("")}</div></header><div class="admin-compare-grid">${compared.map(item => `<pre>${escapeHtml(JSON.stringify(item?.content || {}, null, 2))}</pre>`).join("")}</div></section>
    </div>
  </section>`;
}

function renderAgentTabs(entry, node, options = {}) {
  const available = ["overview", ...(options.persona ? ["persona"] : []), "prompts", "skills", "mcp"];
  const view = available.includes(selectedAgentViews[entry.key]) ? selectedAgentViews[entry.key] : "overview";
  const tabs = [
    ["overview", "Overview", ""],
    ...(options.persona ? [["persona", "Persona", (agentConfigData.automation_personas || []).length]] : []),
    ["prompts", "Prompts", (entry.prompts || []).length],
    ["skills", "Skills", (entry.skills || []).length],
    ["mcp", "MCP", (entry.mcp_servers || []).length],
  ];
  let panel = options.overview;
  if (view === "persona") panel = renderAutomationPersonaPanel();
  else if (view === "prompts") panel = renderAgentPromptPanel(entry);
  else if (view === "skills") panel = renderAgentSkillsPanel(entry);
  else if (view === "mcp") panel = renderAgentMcpPanel(entry);
  return `<div class="admin-agent-tabs" role="tablist" aria-label="${escapeHtml(node.name)} configuration">${tabs.map(([id, label, count]) => `<button type="button" role="tab" aria-selected="${view === id ? "true" : "false"}" data-action="select-agent-view" data-agent-key="${escapeHtml(entry.key)}" data-agent-view="${id}" class="${view === id ? "is-active" : ""}">${label}${count === "" ? "" : `<span>${count}</span>`}</button>`).join("")}</div><div class="admin-agent-panel" role="tabpanel">${panel}</div>`;
}

function renderSelectedAgent(agents) {
  if (!selectedAgentPath.length) return renderAgentCatalogOverview(agents);
  const agent = agents.find(item => item.key === selectedAgentPath[0]);
  if (!agent) return renderAgentCatalogOverview(agents);
  let node = agent;
  let behavior = null;
  if (agent.key === "route-agent") {
    const automationPath = ["route-agent", "agora-router", "automation-router"];
    if (pathStartsWith(selectedAgentPath, automationPath) && selectedAgentPath.length > automationPath.length) {
      const automation = findRouteNode(automationPath);
      behavior = (automation?.children || []).find(item => item.key === selectedAgentPath[automationPath.length]) || null;
      node = behavior || automation;
    } else {
      node = findRouteNode(selectedAgentPath) || agentConfigData.route_navigation;
    }
  }
  const routeAgent = agents.find(item => item.key === "route-agent");
  const entry = agent.key === "route-agent"
    ? { ...routeAgent, key: node.key, name: node.name, prompts: promptsForRouteNode(routeAgent, node), skills: [], mcp_servers: [] }
    : agent;
  const basePath = behavior ? selectedAgentPath.slice(0, 3) : selectedAgentPath;
  const capabilities = agent.key === "route-agent" ? (node.capabilities || []) : (agent.components || []);
  const overview = `${agent.key === "route-agent" && node.key === "route-agent" ? renderRouteRuntime() : ""}${renderCapabilities(capabilities, node.kind || agent.kind)}${renderNodeLinks(behavior ? findRouteNode(basePath) : node, basePath, behavior)}`;
  return `<article class="admin-agent-detail">
    <header class="admin-agent-detail-header"><div><p class="admin-eyebrow">${escapeHtml((node.kind || agent.kind || "agent").replaceAll("_", " "))}</p><h2>${escapeHtml(node.name || agent.name)}</h2><p>${escapeHtml(node.description || agent.description)}</p></div><div>${renderAgentBadge(agentStatusLabel(node.status || agent.status), (node.status || agent.status) === "feature_gated" ? "is-gated" : "is-active")}${behavior ? renderAgentBadge("Automation behavior") : ""}</div></header>
    ${renderAgentTabs(entry, node, { persona: node.key === "automation-router" && !behavior, overview })}
  </article>`;
}

function renderAgentMobileNav(agents) {
  if (!selectedAgentPath.length) return "";
  const items = agentBreadcrumbItems(agents);
  const parent = items.at(-2);
  return `<div class="admin-agent-mobile-nav">${parent ? `<a href="${agentPathHref(parent.path)}"><span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>${escapeHtml(parent.name)}</a>` : ""}</div>`;
}

function renderAgentConfig() {
  let content = "";
  if (agentConfigLoading) {
    content = `<div class="admin-agent-loading" aria-live="polite"><span class="material-symbols-outlined admin-invite-spinner" aria-hidden="true">progress_activity</span><p>Loading Agent configuration...</p></div>`;
  } else if (agentConfigLoadError) {
    content = `<div class="admin-agent-error" role="alert"><span class="material-symbols-outlined" aria-hidden="true">error</span><div><strong>Agent configuration unavailable</strong><p>${escapeHtml(agentConfigLoadError)}</p><button class="btn btn-ghost" type="button" data-action="retry-agent-config">Retry</button></div></div>`;
  } else if (!agentConfigData) {
    content = `<div class="admin-agent-loading"><p>Agent configuration has not been loaded.</p></div>`;
  } else {
    const agents = agentConfigAgents();
    content = `<div class="admin-agent-workspace">${renderAgentTree(agents)}<section class="admin-agent-main">${renderAgentBreadcrumbs(agents)}${renderAgentMobileNav(agents)}${renderSelectedAgent(agents)}</section></div>`;
  }
  return `<header class="admin-main-header"><div><p>Inspect Agent responsibilities, routing, runtime configuration, and deployment-bound Prompts. Scheduled Prompt versions become active after the next successful daily deployment.</p></div></header><div class="admin-agent-catalog">${content}</div>`;
}

function renderEnvironmentConfig() {
  const sourceItems = Array.isArray(environmentData.items) && environmentData.items.length
    ? environmentData.items
    : (environmentData.names || []).map(name => ({ name, description: "Description unavailable until the API is updated." }));
  const normalizedQuery = environmentQuery.toLowerCase();
  const items = sourceItems.filter(({ name, description }) => (
    name.toLowerCase().includes(normalizedQuery) || description.toLowerCase().includes(normalizedQuery)
  ));
  return `<header class="admin-main-header"><div><p class="admin-eyebrow">NAMES ONLY</p><p>Configuration names from the project root .env. Values and value-derived metadata are never returned.</p></div></header><section class="admin-ops-surface">${environmentLoadError ? `<p class="login-error" role="alert">${escapeHtml(environmentLoadError)}</p><button class="btn btn-ghost" type="button" data-action="retry-environment-config">Retry</button>` : `<label class="admin-config-search"><span class="material-symbols-outlined" aria-hidden="true">search</span><input data-env-search type="search" value="${escapeHtml(environmentQuery)}" placeholder="Search names or descriptions" /></label><h2>Configuration names <span class="admin-count">${items.length}</span></h2><div class="admin-config-list">${items.length ? items.map(({ name, description }) => `<div class="admin-config-item"><div class="admin-config-copy"><code>${escapeHtml(name)}</code><span class="admin-config-description">${escapeHtml(description)}</span></div><button type="button" data-action="copy-config-name" data-config-name="${escapeHtml(name)}" title="Copy ${escapeHtml(name)}" aria-label="Copy ${escapeHtml(name)}"><span class="material-symbols-outlined" aria-hidden="true">content_copy</span></button></div>`).join("") : `<p>No matching configuration names or descriptions.</p>`}</div>`}</section>`;
}

function syncAdminRailScrollPosition() {
  const sidebarBody = root.querySelector(".admin-sidebar-body");
  const activeLink = root.querySelector(".admin-sidebar-nav a.is-active");
  if (!sidebarBody || !activeLink) return;
  const isMobileRail = globalThis.matchMedia?.("(max-width: 720px)")?.matches === true;
  if (!isMobileRail) {
    sidebarBody.scrollLeft = 0;
    return;
  }
  const centeredLeft = activeLink.offsetLeft - (sidebarBody.clientWidth - activeLink.offsetWidth) / 2;
  sidebarBody.scrollLeft = Math.max(0, Math.min(centeredLeft, sidebarBody.scrollWidth - sidebarBody.clientWidth));
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
    : adminSection === "schedule"
    ? renderAdminSchedule()
    : adminSection === "new-account"
    ? renderAdminNewAccount()
    : adminSection === "pending-assignment" || adminSection === "assigned" || adminSection === "resolved"
    ? renderAdminTicketBoard(adminSection)
    : adminSection === "audit"
    ? renderAudit()
    : adminSection === "automated-cases"
    ? renderAutomatedCases()
    : adminSection === "agent-config"
    ? renderAgentConfig()
    : adminSection === "environment-config"
    ? renderEnvironmentConfig()
    : renderOverview();
  root.innerHTML = renderAdminShell(content);
  syncAdminRailScrollPosition();
}

async function loadEnvironmentConfig({ render = true } = {}) {
  environmentLoadError = "";
  try {
    const payload = await fetchJson("/api/workspace/admin/environment-config");
    environmentData = payload || { names: [], items: [] };
  } catch (error) {
    environmentData = { names: [], items: [] };
    environmentLoadError = error.message;
  }
  if (render) renderAdmin();
}

async function loadAgentConfig({ render = true, force = false } = {}) {
  if (agentConfigLoading || (agentConfigData && !force)) return;
  agentConfigLoading = true;
  agentConfigLoadError = "";
  if (render) renderAdmin();
  try {
    const payload = await fetchJson("/api/workspace/admin/agent-config");
    agentConfigData = payload || { agents: [], route_navigation: null, route_runtime: {}, automation_personas: [] };
  } catch (error) {
    agentConfigData = null;
    agentConfigLoadError = error.message;
  } finally {
    agentConfigLoading = false;
    if (render) renderAdmin();
  }
}

async function loadAdminData() {
  if (!isAdminAuthenticated()) return;
  loading = true;
  loadError = "";
  renderAdmin();
  const environmentRequest = loadEnvironmentConfig({ render: false });
  const automationParams = new URLSearchParams();
  if (automationRouteStatus) automationParams.set("route_status", automationRouteStatus);
  try {
    const [accountPayload, casePayload, metricPayload, auditPayload, schedulePayload, automationPayload] = await Promise.all([
      fetchJson("/api/workspace/admin/accounts"),
      fetchJson("/api/workspace/cases?assignment_status=all"),
      fetchJson("/api/workspace/admin/metrics"),
      fetchJson("/api/workspace/admin/audit?limit=200"),
      fetchJson("/api/workspace/admin/engineer-schedules"),
      fetchJson(`/api/workspace/admin/account-automation?${automationParams}`),
    ]);
    accounts = Array.isArray(accountPayload.accounts) ? accountPayload.accounts : [];
    adminTickets = Array.isArray(casePayload.cases) ? casePayload.cases.map(normalizeAdminTicket) : [];
    metrics = metricPayload || null;
    auditEvents = Array.isArray(auditPayload.events) ? auditPayload.events : [];
    scheduleData = schedulePayload || { timezone: "Asia/Shanghai", engineers: [] };
    automationData = automationPayload || { metrics: {}, cases: [] };
  } catch (error) {
    loadError = error.message;
  } finally {
    await environmentRequest;
    loading = false;
    renderAdmin();
    if (adminSection === "agent-config") loadAgentConfig();
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
  agentConfigData = null;
  agentConfigLoading = false;
  agentConfigLoadError = "";
  expandedAgentKeys = new Set();
  selectedAgentViews = {};
  selectedAgentPrompts = {};
  selectedPersonaKey = "";
  comparePersonaVersions = [];
  personaDraftValues = {};
  personaOperationNotice = "";
  personaOperationBusy = false;
  personaCreateOpen = false;
  environmentData = { names: [], items: [] };
  environmentLoadError = "";
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
      email: String(data.get("email") || "").trim(),
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
  if (form.dataset.submitting === "true") return;
  const data = new FormData(form);
  const submit = form.querySelector('button[type="submit"]');
  const errorNode = form.querySelector("[data-invitation-error]");
  const originalSubmitMarkup = submit.innerHTML;
  form.dataset.submitting = "true";
  form.setAttribute("aria-busy", "true");
  submit.disabled = true;
  submit.innerHTML = `<span class="material-symbols-outlined admin-invite-spinner" aria-hidden="true">progress_activity</span><span aria-live="polite">Sending invitation...</span>`;
  errorNode.textContent = "";
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
    errorNode.textContent = error.message;
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
    submit.innerHTML = originalSubmitMarkup;
    submit.disabled = false;
  }
}

async function handleScheduleUpdate(form) {
  if (form.dataset.submitting === "true") return;
  const data = new FormData(form);
  const engineerId = form.dataset.engineerId;
  const shifts = [];
  for (let weekday = 0; weekday < 7; weekday += 1) {
    if (!data.get(`day_${weekday}`)) continue;
    const startHour = String(data.get(`start_hour_${weekday}`) || "00");
    const startMinute = String(data.get(`start_minute_${weekday}`) || "00");
    const endHour = String(data.get(`end_hour_${weekday}`) || "00");
    const endMinute = endHour === "24" ? "00" : String(data.get(`end_minute_${weekday}`) || "00");
    shifts.push({
      weekday,
      start: `${startHour}:${startMinute}`,
      end: `${endHour}:${endMinute}`,
    });
  }
  const submit = form.querySelector("[data-schedule-submit]");
  const errorNode = form.querySelector("[data-schedule-error]");
  const originalSubmitMarkup = submit.innerHTML;
  form.dataset.submitting = "true";
  form.setAttribute("aria-busy", "true");
  submit.disabled = true;
  submit.innerHTML = `<span class="material-symbols-outlined admin-invite-spinner" aria-hidden="true">progress_activity</span><span aria-live="polite">Saving schedule...</span>`;
  errorNode.textContent = "";
  try {
    const payload = await fetchJson(`/api/workspace/admin/engineers/${encodeURIComponent(engineerId)}/schedule`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shifts }),
    });
    scheduleData = {
      timezone: payload.timezone || scheduleData.timezone,
      engineers: Array.isArray(payload.engineers) ? payload.engineers : scheduleData.engineers,
    };
    selectedEngineerId = "";
    scheduleNotice = "Schedule saved";
    renderAdmin();
  } catch (error) {
    errorNode.textContent = error.message;
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
    submit.innerHTML = originalSubmitMarkup;
    submit.disabled = false;
  }
}

root.addEventListener("click", (event) => {
  const sectionLink = event.target.closest("[data-section]");
  if (sectionLink) {
    event.preventDefault();
    adminSection = sectionLink.dataset.section;
    selectedAgentPath = adminSection === "agent-config" ? [] : selectedAgentPath;
    selectedEngineerId = "";
    if (adminSection !== "schedule") scheduleNotice = null;
    if (adminSection !== "new-account") invitationResult = null;
    if (globalThis.location) globalThis.location.hash = adminSection;
    renderAdmin();
    if (adminSection === "agent-config") loadAgentConfig();
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "sign-out") {
    signOut();
  } else if (action === "edit-schedule") {
    adminSection = "schedule";
    selectedEngineerId = event.target.closest("[data-engineer-id]")?.dataset.engineerId || "";
    scheduleNotice = null;
    if (globalThis.location) globalThis.location.hash = "schedule";
    renderAdmin();
  } else if (action === "close-schedule-editor") {
    selectedEngineerId = "";
    renderAdmin();
  } else if (action === "refresh") {
    loadAdminData();
  } else if (action === "retry-environment-config") {
    loadEnvironmentConfig();
  } else if (action === "retry-agent-config") {
    loadAgentConfig({ force: true });
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
  } else if (action === "copy-config-name") {
    const name = event.target.closest("[data-config-name]")?.dataset.configName || "";
    globalThis.navigator?.clipboard?.writeText(name);
  } else if (action === "toggle-agent-tree") {
    const button = event.target.closest("[data-agent-key]");
    if (expandedAgentKeys.has(button.dataset.agentKey)) expandedAgentKeys.delete(button.dataset.agentKey);
    else expandedAgentKeys.add(button.dataset.agentKey);
    renderAdmin();
  } else if (action === "select-agent-view") {
    const button = event.target.closest("[data-agent-key]");
    selectedAgentViews[button.dataset.agentKey] = button.dataset.agentView;
    renderAdmin();
  } else if (action === "select-agent-prompt") {
    const promptButton = event.target.closest("[data-agent-key]");
    selectedAgentPrompts[promptButton.dataset.agentKey] = promptButton.dataset.promptKey;
    renderAdmin();
  } else if (action === "edit-prompt") {
    const promptKey = event.target.closest("[data-prompt-key]").dataset.promptKey;
    const selectedPrompt = (agentConfigData?.agents || [])
      .flatMap(entry => entry.prompts || []).find(prompt => prompt.key === promptKey);
    promptDraftValues[promptKey] = {
      content: selectedPrompt?.content || "",
      change_note: "",
    };
    promptEditorKeys.add(promptKey);
    promptDiffKeys.delete(promptKey);
    renderAdmin();
  } else if (action === "cancel-prompt-edit") {
    const promptKey = event.target.closest("[data-prompt-key]").dataset.promptKey;
    promptEditorKeys.delete(promptKey);
    delete promptDraftValues[promptKey];
    renderAdmin();
  } else if (action === "toggle-prompt-diff") {
    const promptKey = event.target.closest("[data-prompt-key]").dataset.promptKey;
    if (promptDiffKeys.has(promptKey)) promptDiffKeys.delete(promptKey);
    else promptDiffKeys.add(promptKey);
    renderAdmin();
  } else if (["schedule-prompt-version", "unschedule-prompt-version", "restore-prompt-version"].includes(action)) {
    const button = event.target.closest("[data-prompt-key]");
    const apiAction = action.replace("-prompt-version", "");
    runPromptVersionAction(apiAction, button.dataset.promptKey, button.dataset.promptVersion);
  } else if (action === "toggle-persona-create") {
    personaCreateOpen = !personaCreateOpen;
    renderAdmin();
  } else if (action === "select-persona") {
    selectedPersonaKey = event.target.closest("[data-persona-key]").dataset.personaKey;
    comparePersonaVersions = [];
    personaOperationNotice = "";
    renderAdmin();
  } else if (action === "toggle-persona") {
    const button = event.target.closest("[data-persona-key]");
    setPersonaEnabled(button.dataset.personaKey, button.dataset.enabled === "true");
  } else if (action === "publish-persona" || action === "rollback-persona") {
    const button = event.target.closest("[data-persona-key]");
    const operation = action === "publish-persona" ? "publish" : "rollback";
    if (operation === "rollback" && !globalThis.confirm?.(`Create a new published version from v${button.dataset.version}?`)) return;
    runPersonaVersionAction(operation, button.dataset.personaKey, button.dataset.version);
  }
});

root.addEventListener("change", (event) => {
  const versionSelect = event.target.closest("[data-prompt-version-select]");
  if (versionSelect) {
    selectedPromptVersions[versionSelect.dataset.promptVersionSelect] = Number(versionSelect.value);
    promptDiffKeys.delete(versionSelect.dataset.promptVersionSelect);
    renderAdmin();
    return;
  }
  const personaCompare = event.target.closest("[data-version-compare]");
  if (personaCompare) {
    comparePersonaVersions[Number(personaCompare.dataset.versionCompare)] = Number(personaCompare.value);
    renderAdmin();
    return;
  }
  const endHour = event.target.closest("[data-end-hour]");
  if (!endHour) return;
  const weekday = endHour.dataset.endHour;
  const minuteSelect = root.querySelector(`[data-end-minute="${weekday}"]`);
  if (!minuteSelect) return;
  const isEndOfDay = endHour.value === "24";
  if (isEndOfDay) minuteSelect.value = "00";
  minuteSelect.disabled = isEndOfDay;
});

root.addEventListener("input", (event) => {
  const draftContent = event.target.closest("[data-prompt-draft-content]");
  const draftNote = event.target.closest("[data-prompt-draft-note]");
  if (draftContent || draftNote) {
    const promptKey = (draftContent || draftNote).dataset.promptDraftContent || (draftContent || draftNote).dataset.promptDraftNote;
    const current = promptDraftValues[promptKey] || { content: "", change_note: "" };
    promptDraftValues[promptKey] = {
      content: draftContent ? draftContent.value : current.content,
      change_note: draftNote ? draftNote.value : current.change_note,
    };
    return;
  }
  const personaDraftField = event.target.closest("[data-persona-draft-field]");
  if (personaDraftField) {
    const form = personaDraftField.closest("[data-persona-draft-form]");
    const personaKey = form.dataset.personaKey;
    const current = personaDraftValues[personaKey] || {};
    personaDraftValues[personaKey] = { ...current, [personaDraftField.dataset.personaDraftField]: personaDraftField.value };
    return;
  }
  if (!event.target.matches("[data-env-search]")) return;
  environmentQuery = event.target.value;
  renderAdmin();
  root.querySelector("[data-env-search]")?.focus();
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
  if (form.matches("[data-automation-filter-form]")) {
    automationRouteStatus = String(new FormData(form).get("route_status") || "").trim();
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(form).entries()) if (String(value).trim()) params.set(key, String(value).trim());
    fetchJson(`/api/workspace/admin/account-automation?${params}`).then((payload) => { automationData = payload; renderAdmin(); }).catch((error) => { loadError = error.message; renderAdmin(); });
    return;
  }
  if (form.matches("[data-prompt-draft-form]")) {
    createPromptDraft(form);
    return;
  }
  if (form.matches("[data-persona-create-form]")) {
    createPersona(form);
    return;
  }
  if (form.matches("[data-persona-draft-form]")) {
    createPersonaDraft(form);
    return;
  }
});

window.addEventListener?.("hashchange", () => {
  const nextSection = sectionFromHash();
  if (nextSection !== adminSection) selectedEngineerId = "";
  adminSection = nextSection;
  selectedAgentPath = agentPathFromHash();
  renderAdmin();
  if (adminSection === "agent-config") loadAgentConfig();
});

renderAdmin();
if (isAdminAuthenticated()) {
  loadAdminData();
}
