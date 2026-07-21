const WORKSPACE_ACCESS_TOKEN_KEY = "supportportal_admin_workspace_access_token";
const WORKSPACE_ACCOUNT_KEY = "supportportal_admin_workspace_account";
const WORKSPACE_AUTH_KEY = "supportportal_admin_workspace_account_id";

const root = document.getElementById("workspace-admin-root");

let accessToken = readStorage(WORKSPACE_ACCESS_TOKEN_KEY, "");
let currentAccount = readStorage(WORKSPACE_ACCOUNT_KEY, null);
let adminSection = sectionFromHash();
let accounts = [];
let adminTickets = [];
let metrics = null;
let auditEvents = [];
let scheduleData = { timezone: "Asia/Shanghai", engineers: [] };
let automationData = { metrics: {}, cases: [] };
let routingData = { stages: [], system_prompt: "" };
let routeData = { routes: [] };
let selectedRouteDetail = null;
let personaData = { personas: [] };
let environmentData = { names: [] };
let environmentQuery = "";
let selectedPersonaKey = "";
let comparePersonaVersions = [];
let selectedEngineerId = "";
let invitationResult = null;
let scheduleNotice = null;
let loading = false;
let loadError = "";

function sectionFromHash() {
  const section = String(globalThis.location?.hash || window.location?.hash || "").replace(/^#/, "");
  return ["overview", "automated-cases", "route-prompt", "persona-prompts", "environment-config", "engineers", "schedule", "new-account", "pending-assignment", "assigned", "resolved", "audit"].includes(section)
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
    ["route-prompt", "account_tree", "Route & Prompt", "RP"],
    ["persona-prompts", "record_voice_over", "Persona Prompts", "PP"],
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
          <div class="admin-user-chip" title="${escapeHtml(accountName)}">
            <span class="admin-user-avatar">${escapeHtml(engineerInitials(currentAccount))}</span>
            <span class="admin-user-meta"><strong>${escapeHtml(accountName)}</strong><small>Administrator</small></span>
          </div>
          <button class="admin-logout-btn" type="button" data-action="sign-out" title="Sign out" aria-label="Sign out">
            <span class="admin-rail-symbol-wrap" aria-hidden="true"><span class="material-symbols-outlined admin-rail-glyph">logout</span><span class="admin-rail-fallback">LO</span></span>
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
      <div><h1>Operations Overview</h1><p>Current Engineer Case queue, schedule coverage, and SLA health.</p></div>
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
      <div><h1>Engineer Management</h1><p>Monitor current coverage and update engineer schedules.</p></div>
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
      <div><h1>Weekly Schedule</h1><p>Review coverage and modify engineer shifts.</p></div>
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
      <header class="admin-main-header"><div><p class="admin-eyebrow">ACCOUNT ACCESS</p><h1>Invite a workspace member</h1><p>Send a secure, single-use setup link by email.</p></div></header>
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
    <header class="admin-main-header admin-case-header"><div><p class="admin-eyebrow">ENGINEER CASES</p><h1>${activeTab.label}</h1><p>Cases grouped by assignment status, with client ticket status shown independently.</p></div></header>
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
    <header class="admin-main-header"><div><h1>Audit</h1><p>Account, schedule, and assignment administration events.</p></div></header>
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
    <header class="admin-main-header"><div><p class="admin-eyebrow">ACCOUNT AUTOMATION</p><h1>Automated Cases</h1><p>All /account cases. Automated means the final route was Automated, not that the case was resolved.</p></div></header>
    <section class="admin-metric-strip" aria-label="Account automation metrics">
      <div><span>Total account cases</span><strong>${Number(metric.total_account_cases || 0)}</strong></div>
      <div><span>Routed Automated</span><strong>${Number(metric.automated_cases || 0)}</strong></div>
      <div><span>Not Automated</span><strong>${Number(metric.not_automated_cases || 0)}</strong></div>
      <div class="is-emphasis"><span>Automation share</span><strong>${rate.toFixed(1)}%</strong></div>
    </section><form class="admin-filter-bar" data-automation-filter-form><select name="route_status"><option value="">All routes</option><option value="automation">Automated</option><option value="not_automated">Not Automated</option></select><input name="category" placeholder="Route category" /><input name="created_from" type="date" aria-label="Created from" /><input name="created_to" type="date" aria-label="Created to" /><button class="btn btn-ghost" type="submit">Apply filters</button></form>
    <section class="admin-ops-surface"><table class="admin-work-table"><thead><tr><th>Case</th><th>Subject</th><th>Route status</th><th>Created</th></tr></thead><tbody>${cases.length ? cases.map(item => `<tr><td>${escapeHtml(item.client_ticket_id || item.ticket_id)}</td><td>${escapeHtml(item.title || "Untitled")}</td><td>${statusPill(item.automation_status)}</td><td>${escapeHtml(formatDateTime(item.created_at))}</td></tr>`).join("") : `<tr><td colspan="4">No /account cases.</td></tr>`}</tbody></table></section>`;
}

function renderRoutePrompt() {
  const routes = Array.isArray(routeData.routes) ? routeData.routes : [];
  const detail = selectedRouteDetail?.executions?.[0] || null;
  return `
    <header class="admin-main-header"><div><p class="admin-eyebrow">ROUTING AUDIT</p><h1>Route &amp; Prompt</h1><p>Inspect the actual route execution and persisted Prompt snapshot for each /account case.</p></div></header>
    <section class="admin-route-layout">
      <div class="admin-ops-surface"><h2>Current route</h2><p><strong>${escapeHtml(routingData.router_prompt_version || "unversioned")}</strong></p><ol class="admin-route-timeline">${(routingData.stages || []).map(stage => `<li>${escapeHtml(stage)}</li>`).join("")}</ol><h3>Current router Prompt</h3><pre>${escapeHtml(routingData.system_prompt || "No LLM prompt used")}</pre></div>
      <div class="admin-ops-surface"><h2>Route execution</h2>${routes.length ? routes.map(item => `<button class="admin-route-row" data-action="inspect-route" data-ticket-id="${escapeHtml(item.ticket_id)}"><span><strong>${escapeHtml(item.ticket_id)}</strong><small>${escapeHtml(item.route_source || "legacy")}</small></span><span>${escapeHtml(item.final_route || "unknown")}</span></button>`).join("") : `<p>No route executions recorded.</p>`}</div>
    </section>
    ${selectedRouteDetail ? `<section class="admin-ops-surface admin-route-detail"><h2>${escapeHtml(selectedRouteDetail.ticket_id)}</h2>${selectedRouteDetail.legacy ? `<p class="admin-empty-state">Prompt snapshot unavailable for this historical case.</p>` : `<ol class="admin-route-timeline">${(detail?.stages || []).map(stage => `<li><strong>${escapeHtml(stage.name)}</strong><span>${escapeHtml(stage.status)}</span></li>`).join("")}</ol><div class="admin-prompt-inspector"><h3>System Prompt</h3><pre>${escapeHtml(detail?.system_prompt || "No LLM prompt used")}</pre><h3>User Prompt</h3><pre>${escapeHtml(detail?.user_prompt || "No LLM prompt used")}</pre></div>`}</section>` : ""}`;
}

function renderPersonaPrompts() {
  const personas = Array.isArray(personaData.personas) ? personaData.personas : [];
  const persona = personas.find(item => item.persona_key === selectedPersonaKey) || personas[0];
  const versions = persona?.versions || [];
  const active = versions.find(item => Number(item.version) === Number(persona?.published_version)) || versions.at(-1);
  return `
    <header class="admin-main-header"><div><p class="admin-eyebrow">CUSTOMER VOICE</p><h1>Persona Prompt Template</h1><p>Draft, publish, compare, and roll back the Prompt used for /account customer replies.</p></div></header>
    <form class="admin-filter-bar" data-persona-create-form><input name="persona_key" pattern="[a-z][a-z0-9-]{1,63}" placeholder="persona-key" required /><input name="display_name" placeholder="Display name" required /><input name="instruction" placeholder="Persona instruction" required /><button class="btn btn-ghost" type="submit">Create Persona</button></form>
    ${persona ? `<section class="admin-persona-workspace"><aside class="admin-ops-surface"><nav class="admin-persona-list">${personas.map(item => `<button type="button" data-action="select-persona" data-persona-key="${escapeHtml(item.persona_key)}" class="${item.persona_key === persona.persona_key ? "is-active" : ""}">${escapeHtml(item.display_name)}</button>`).join("")}</nav><h2>${escapeHtml(persona.display_name)}</h2><p>${statusPill(persona.enabled ? "active" : "disabled")} Published v${escapeHtml(persona.published_version || "-")}</p><button class="btn btn-ghost" type="button" data-action="toggle-persona" data-persona-key="${escapeHtml(persona.persona_key)}" data-enabled="${persona.enabled ? "false" : "true"}">${persona.enabled ? "Disable" : "Enable"}</button><h3>Version history</h3><div class="admin-version-list">${versions.map(item => `<button type="button" data-action="rollback-persona" data-persona-key="${escapeHtml(persona.persona_key)}" data-version="${item.version}" title="Create a new published version from v${item.version}"><strong>v${item.version}</strong><span>${escapeHtml(item.status)}</span><small>${escapeHtml(item.change_note)}</small></button>`).join("")}</div></aside><div><form class="admin-ops-surface admin-prompt-editor" data-persona-draft-form data-persona-key="${escapeHtml(persona.persona_key)}"><label>Persona instruction<textarea name="instruction" rows="10" required>${escapeHtml(active?.content?.instruction || "")}</textarea></label><label>Reply opener<input name="opener" value="${escapeHtml(active?.content?.opener || "")}" placeholder="Optional opening sentence" /></label><label>Signoff name<input name="signoff_name" value="${escapeHtml(active?.content?.signoff_name || "Sid")}" required /></label><label>Change note<input name="change_note" required maxlength="500" /></label><input type="hidden" name="based_on_version" value="${escapeHtml(persona.published_version || "")}" /><div class="admin-editor-actions"><button class="btn btn-ghost" type="submit">Save draft</button>${versions.filter(item => item.status === "draft").map(item => `<button class="btn btn-primary" type="button" data-action="publish-persona" data-persona-key="${escapeHtml(persona.persona_key)}" data-version="${item.version}">Publish v${item.version}</button>`).join("")}</div></form><section class="admin-ops-surface admin-version-compare"><h3>Compare versions</h3><div><select data-version-compare="0">${versions.map(item => `<option value="${item.version}">v${item.version}</option>`).join("")}</select><select data-version-compare="1">${versions.map(item => `<option value="${item.version}" ${item.version === versions.at(-1)?.version ? "selected" : ""}>v${item.version}</option>`).join("")}</select></div><div class="admin-compare-grid">${[comparePersonaVersions[0] || versions[0]?.version, comparePersonaVersions[1] || versions.at(-1)?.version].map(version => { const item = versions.find(candidate => Number(candidate.version) === Number(version)); return `<pre>${escapeHtml(JSON.stringify(item?.content || {}, null, 2))}</pre>`; }).join("")}</div></section></div></section>` : `<p class="admin-empty-state">No Persona templates available.</p>`}`;
}

function renderEnvironmentConfig() {
  const names = (environmentData.names || []).filter(name => name.toLowerCase().includes(environmentQuery.toLowerCase()));
  return `<header class="admin-main-header"><div><p class="admin-eyebrow">NAMES ONLY</p><h1>Environment Config</h1><p>Configuration names from the project root .env. Values and value-derived metadata are never returned.</p></div></header><section class="admin-ops-surface"><label class="admin-config-search"><span class="material-symbols-outlined" aria-hidden="true">search</span><input data-env-search type="search" value="${escapeHtml(environmentQuery)}" placeholder="Search configuration names" /></label><h2>Configuration names <span class="admin-count">${names.length}</span></h2><div class="admin-config-list">${names.length ? names.map(name => `<button type="button" data-action="copy-config-name" data-config-name="${escapeHtml(name)}" title="Copy configuration name"><code>${escapeHtml(name)}</code><span class="material-symbols-outlined" aria-hidden="true">content_copy</span></button>`).join("") : `<p>No matching configuration names.</p>`}</div></section>`;
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
    : adminSection === "route-prompt"
    ? renderRoutePrompt()
    : adminSection === "persona-prompts"
    ? renderPersonaPrompts()
    : adminSection === "environment-config"
    ? renderEnvironmentConfig()
    : renderOverview();
  root.innerHTML = renderAdminShell(content);
  syncAdminRailScrollPosition();
}

async function loadAdminData() {
  if (!isAdminAuthenticated()) return;
  loading = true;
  loadError = "";
  renderAdmin();
  try {
    const [accountPayload, casePayload, metricPayload, auditPayload, schedulePayload, automationPayload, routingPayload, routesPayload, personasPayload, environmentPayload] = await Promise.all([
      fetchJson("/api/workspace/admin/accounts"),
      fetchJson("/api/workspace/cases?assignment_status=all"),
      fetchJson("/api/workspace/admin/metrics"),
      fetchJson("/api/workspace/admin/audit?limit=200"),
      fetchJson("/api/workspace/admin/engineer-schedules"),
      fetchJson("/api/workspace/admin/account-automation"),
      fetchJson("/api/workspace/admin/account-routing/config"),
      fetchJson("/api/workspace/admin/account-routes"),
      fetchJson("/api/workspace/admin/account-personas"),
      fetchJson("/api/workspace/admin/environment-config"),
    ]);
    accounts = Array.isArray(accountPayload.accounts) ? accountPayload.accounts : [];
    adminTickets = Array.isArray(casePayload.cases) ? casePayload.cases.map(normalizeAdminTicket) : [];
    metrics = metricPayload || null;
    auditEvents = Array.isArray(auditPayload.events) ? auditPayload.events : [];
    scheduleData = schedulePayload || { timezone: "Asia/Shanghai", engineers: [] };
    automationData = automationPayload || { metrics: {}, cases: [] };
    routingData = routingPayload || { stages: [], system_prompt: "" };
    routeData = routesPayload || { routes: [] };
    personaData = personasPayload || { personas: [] };
    environmentData = environmentPayload || { names: [] };
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
    selectedEngineerId = "";
    if (adminSection !== "schedule") scheduleNotice = null;
    if (adminSection !== "new-account") invitationResult = null;
    if (globalThis.location) globalThis.location.hash = adminSection;
    renderAdmin();
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
  } else if (action === "inspect-route") {
    const ticketId = event.target.closest("[data-ticket-id]")?.dataset.ticketId;
    fetchJson(`/api/workspace/admin/account-routes/${encodeURIComponent(ticketId)}`).then((payload) => { selectedRouteDetail = payload; renderAdmin(); }).catch((error) => { loadError = error.message; renderAdmin(); });
  } else if (action === "publish-persona" || action === "rollback-persona") {
    const button = event.target.closest("[data-persona-key]");
    const operation = action === "publish-persona" ? "publish" : "rollback";
    if (operation === "rollback" && !globalThis.confirm?.(`Create a new published version from v${button.dataset.version}?`)) return;
    fetchJson(`/api/workspace/admin/account-personas/${encodeURIComponent(button.dataset.personaKey)}/versions/${button.dataset.version}/${operation}`, { method: "POST" }).then(loadAdminData).catch((error) => { loadError = error.message; renderAdmin(); });
  } else if (action === "copy-config-name") {
    const name = event.target.closest("[data-config-name]")?.dataset.configName || "";
    globalThis.navigator?.clipboard?.writeText(name);
  } else if (action === "select-persona") {
    selectedPersonaKey = event.target.closest("[data-persona-key]")?.dataset.personaKey || "";
    comparePersonaVersions = [];
    renderAdmin();
  } else if (action === "toggle-persona") {
    const button = event.target.closest("[data-persona-key]");
    fetchJson(`/api/workspace/admin/account-personas/${encodeURIComponent(button.dataset.personaKey)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: button.dataset.enabled === "true" }) }).then(loadAdminData).catch((error) => { loadError = error.message; renderAdmin(); });
  }
});

root.addEventListener("change", (event) => {
  const compare = event.target.closest("[data-version-compare]");
  if (compare) {
    comparePersonaVersions[Number(compare.dataset.versionCompare)] = Number(compare.value);
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
  if (form.matches("[data-persona-draft-form]")) {
    const data = new FormData(form);
    fetchJson(`/api/workspace/admin/account-personas/${encodeURIComponent(form.dataset.personaKey)}/drafts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: { instruction: String(data.get("instruction") || ""), opener: String(data.get("opener") || ""), signoff_name: String(data.get("signoff_name") || "Sid") },
        change_note: String(data.get("change_note") || ""),
        based_on_version: Number(data.get("based_on_version")) || null,
      }),
    }).then(loadAdminData).catch((error) => { loadError = error.message; renderAdmin(); });
    return;
  }
  if (form.matches("[data-persona-create-form]")) {
    const data = new FormData(form);
    fetchJson("/api/workspace/admin/account-personas", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ persona_key: String(data.get("persona_key") || ""), display_name: String(data.get("display_name") || ""), content: { instruction: String(data.get("instruction") || ""), signoff_name: "Sid" } }) }).then(loadAdminData).catch((error) => { loadError = error.message; renderAdmin(); });
    return;
  }
  if (form.matches("[data-automation-filter-form]")) {
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(form).entries()) if (String(value).trim()) params.set(key, String(value).trim());
    fetchJson(`/api/workspace/admin/account-automation?${params}`).then((payload) => { automationData = payload; renderAdmin(); }).catch((error) => { loadError = error.message; renderAdmin(); });
    return;
  }
});

window.addEventListener?.("hashchange", () => {
  const nextSection = sectionFromHash();
  if (nextSection !== adminSection) selectedEngineerId = "";
  adminSection = nextSection;
  renderAdmin();
});

renderAdmin();
if (isAdminAuthenticated()) {
  loadAdminData();
}
