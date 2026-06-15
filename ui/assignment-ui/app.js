const ASSIGNMENT_AUTH_KEY = "supportportal_assignment_selected_engineer";
const ASSIGNMENT_SHIFT_KEY = "supportportal_assignment_daily_shift";
const ASSIGNMENT_ACTIVE_TICKET_KEY = "supportportal_assignment_active_ticket";
const ASSIGNMENT_QUEUE_KEY = "supportportal_assignment_queue";
const ASSIGNMENT_EVENTS_KEY = "supportportal_assignment_events";
const ASSIGNMENT_SIDEBAR_KEY = "supportportal_assignment_sidebar_collapsed";
const ASSIGNMENT_WORKSPACE_KEY = "supportportal_assignment_workspace_active";
const ASSIGNMENT_ADMIN_SCHEDULE_KEY = "supportportal_assignment_admin_schedule";
const ASSIGNMENT_BREAK_AFTER_CASE_KEY = "supportportal_assignment_break_after_case";
const SLA_MS = 3 * 60 * 60 * 1000;
const UTC8_OFFSET_MS = 8 * 60 * 60 * 1000;

const DEMO_ENGINEERS = [
  { id: "Jack", name: "Jack", role: "Tier One Engineer", initials: "J" },
  { id: "Maya", name: "Maya", role: "Tier One Engineer", initials: "M" },
  { id: "Leo", name: "Leo", role: "Tier One Engineer", initials: "L" },
];

const ENGINEER_COLORS = {
  Jack: { bg: "#cae6ff", fg: "#006493", cssVar: "var(--primary)" },
  Maya: { bg: "#b8e8e0", fg: "#006875", cssVar: "var(--success)" },
  Leo: { bg: "#ffe0b2", fg: "#9f5d12", cssVar: "var(--warning)" },
};

const ADMIN_PRESENCE_MOCK = {
  Jack: "online",
  Maya: "online",
  Leo: "offline",
};

const DEFAULT_ADMIN_SCHEDULE = {
  Jack: {
    monday:    { start: "09:00", end: "18:00" },
    tuesday:   { start: "09:00", end: "18:00" },
    wednesday: { start: "09:00", end: "18:00" },
    thursday:  { start: "09:00", end: "18:00" },
    friday:    { start: "09:00", end: "18:00" },
    saturday:  { start: "", end: "" },
    sunday:    { start: "", end: "" },
  },
  Maya: {
    monday:    { start: "14:00", end: "22:00" },
    tuesday:   { start: "14:00", end: "22:00" },
    wednesday: { start: "14:00", end: "22:00" },
    thursday:  { start: "14:00", end: "22:00" },
    friday:    { start: "14:00", end: "22:00" },
    saturday:  { start: "", end: "" },
    sunday:    { start: "", end: "" },
  },
  Leo: {
    monday:    { start: "", end: "" },
    tuesday:   { start: "", end: "" },
    wednesday: { start: "22:00", end: "06:00" },
    thursday:  { start: "22:00", end: "06:00" },
    friday:    { start: "22:00", end: "06:00" },
    saturday:  { start: "22:00", end: "06:00" },
    sunday:    { start: "22:00", end: "06:00" },
  },
};

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const DEFAULT_SHIFT = {
  start: "09:00",
  end: "18:00",
};

const INITIAL_QUEUE = [
  {
    id: "TK-040-1",
    title: "Black screen after firmware update",
    clientTicket: "TK-040",
    requester: "Acme Operations",
    priority: "First response",
    issue:
      "Customer says the device completed a firmware update, rebooted, and now shows a black screen while the power LED remains on.",
    context: [
      "Client AI opened this Engineer Ticket for one unresolved display issue.",
      "Last customer message: It finished updating, restarted, then the screen stayed black.",
      "Device telemetry still reports heartbeat and normal temperature.",
    ],
    investigation: [
      "Display service is likely not restarting after firmware update.",
      "Power and telemetry signals reduce the likelihood of a full device failure.",
      "Customer-safe next step is service restart before replacement scheduling.",
    ],
    draft:
      "Hi there, we found that the device is still online after the firmware update, but the display service may not have restarted correctly. Please restart the display service once and confirm whether the screen comes back. If it stays black after that restart, we will move to the replacement path.",
  },
  {
    id: "TK-041-1",
    title: "VPN connection drops every 20 minutes",
    clientTicket: "TK-041",
    requester: "Northwind IT",
    priority: "Regular response",
    issue:
      "Customer reports the VPN stays connected for roughly 20 minutes, then disconnects without a visible client-side error.",
    context: [
      "Client AI needs engineer confirmation before sending network remediation steps.",
      "Gateway logs show repeated session rekey events.",
      "No account lockout or credential error is present.",
    ],
    investigation: [
      "Drop timing matches a session rekey mismatch.",
      "Current gateway policy requires the updated VPN profile.",
      "Ask for tunnel log timestamp only if the profile update does not resolve the drop.",
    ],
    draft:
      "Hi there, the logs point to a VPN session rekey mismatch rather than an account issue. Please update the VPN profile to the current gateway policy and reconnect. If the connection still drops, send us the latest tunnel log timestamp so we can compare it against the gateway event.",
  },
  {
    id: "TK-042-1",
    title: "Billing export missing settled route items",
    clientTicket: "TK-042",
    requester: "Finance Admin",
    priority: "Regular response",
    issue:
      "Customer generated a billing export and cannot find settled route items that should be included in monthly reconciliation.",
    context: [
      "Client AI escalated one precise product question for engineer review.",
      "The selected export type currently includes only open route items.",
      "Settled items are present when the report scope is widened.",
    ],
    investigation: [
      "The data exists; the issue is report scope selection.",
      "No backend data repair is needed for this customer.",
      "Customer should regenerate with All route items.",
    ],
    draft:
      "Hi there, the settled route items are available, but the export you selected only includes open items. Please change the report scope to All route items and regenerate the export. The settled route items should appear in that version.",
  },
];

let selectedEngineerId = readStorage(ASSIGNMENT_AUTH_KEY, "");
let selectedEngineerCandidate = selectedEngineerId || DEMO_ENGINEERS[0].id;
let shift = readStorage(ASSIGNMENT_SHIFT_KEY, DEFAULT_SHIFT);
let activeTicket = readStorage(ASSIGNMENT_ACTIVE_TICKET_KEY, null);
let queue = readStorage(ASSIGNMENT_QUEUE_KEY, INITIAL_QUEUE);
let events = readStorage(ASSIGNMENT_EVENTS_KEY, []);
let sidebarCollapsed = readStorage(ASSIGNMENT_SIDEBAR_KEY, false);
let workspaceActive = readStorage(ASSIGNMENT_WORKSPACE_KEY, false);
let readyTransitionActive = false;
let readyTransitionTimer = null;
let breakAfterCase = readStorage(ASSIGNMENT_BREAK_AFTER_CASE_KEY, false);
let slaCountdownTimer = null;
let adminSchedule = normalizeAdminSchedule(readStorage(ASSIGNMENT_ADMIN_SCHEDULE_KEY, DEFAULT_ADMIN_SCHEDULE));
let adminEditState = null; // { engineerId, weekday } | null

const root = document.getElementById("assignment-root");
const isAdminPage = window.location.pathname.includes("/admin");

function readStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function getSelectedEngineer() {
  return DEMO_ENGINEERS.find((engineer) => engineer.id === selectedEngineerId) || null;
}

function getCandidateEngineer() {
  return DEMO_ENGINEERS.find((engineer) => engineer.id === selectedEngineerCandidate) || DEMO_ENGINEERS[0];
}

function utc8Now() {
  return new Date(Date.now() + UTC8_OFFSET_MS);
}

function formatUtc8Time(date = utc8Now()) {
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hour = String(date.getUTCHours()).padStart(2, "0");
  const minute = String(date.getUTCMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:${minute} UTC+8`;
}

function formatUtc8TimeShort(date = utc8Now()) {
  const hour = String(date.getUTCHours()).padStart(2, "0");
  const minute = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hour}:${minute} UTC+8`;
}

function minutesFromTime(value) {
  const match = String(value || "").match(/^(\d{2}):(\d{2})$/);
  if (!match) return 0;
  return Number(match[1]) * 60 + Number(match[2]);
}

function isInShift(now = utc8Now()) {
  const current = now.getUTCHours() * 60 + now.getUTCMinutes();
  const start = minutesFromTime(shift.start);
  const end = minutesFromTime(shift.end);
  if (start === end) return false;
  if (start < end) return current >= start && current < end;
  return current >= start || current < end;
}

function nextShiftInfo() {
  const now = utc8Now();
  const current = now.getUTCHours() * 60 + now.getUTCMinutes();
  const start = minutesFromTime(shift.start);
  if (current >= start) {
    return `Tomorrow at ${shift.start} UTC+8`;
  }
  const diffMin = start - current;
  const hours = Math.floor(diffMin / 60);
  const mins = diffMin % 60;
  if (hours > 0) {
    return `In ${hours}h ${String(mins).padStart(2, "0")}m (${shift.start} UTC+8)`;
  }
  return `In ${mins}m (${shift.start} UTC+8)`;
}

function getEngineerColor(engineerId) {
  return ENGINEER_COLORS[engineerId] || { bg: "#e4e9ef", fg: "#6e7882", cssVar: "var(--ink-muted)" };
}

function normalizeAdminSchedule(source) {
  const schedule = source && typeof source === "object" ? source : {};
  return DEMO_ENGINEERS.reduce((result, engineer) => {
    const engineerSchedule = schedule[engineer.id] && typeof schedule[engineer.id] === "object"
      ? schedule[engineer.id]
      : {};
    result[engineer.id] = WEEKDAYS.reduce((days, weekday) => {
      const fallback = (DEFAULT_ADMIN_SCHEDULE[engineer.id] || {})[weekday] || { start: "", end: "" };
      const shiftValue = engineerSchedule[weekday] && typeof engineerSchedule[weekday] === "object"
        ? engineerSchedule[weekday]
        : fallback;
      days[weekday] = {
        start: String(shiftValue.start || ""),
        end: String(shiftValue.end || ""),
      };
      return days;
    }, {});
    return result;
  }, {});
}

function getPreviousWeekday(weekday) {
  const index = WEEKDAYS.indexOf(weekday);
  return WEEKDAYS[(index + WEEKDAYS.length - 1) % WEEKDAYS.length];
}

function getAdminShift(engineerId, weekday) {
  return (adminSchedule[engineerId] || {})[weekday] || { start: "", end: "" };
}

function shiftStartsOnDayAtMinute(daySchedule, minute) {
  if (!daySchedule || !daySchedule.start || !daySchedule.end) return false;
  const startMin = minutesFromTime(daySchedule.start);
  const endMin = minutesFromTime(daySchedule.end);
  if (startMin === endMin) return false;
  if (startMin < endMin) {
    return minute >= startMin && minute < endMin;
  }
  return minute >= startMin;
}

function previousOvernightShiftCoversMinute(daySchedule, minute) {
  if (!daySchedule || !daySchedule.start || !daySchedule.end) return false;
  const startMin = minutesFromTime(daySchedule.start);
  const endMin = minutesFromTime(daySchedule.end);
  return startMin > endMin && minute < endMin;
}

function getShiftForScheduleCell(engineerId, weekday, hour) {
  const hourMin = hour * 60;
  const currentShift = getAdminShift(engineerId, weekday);
  if (shiftStartsOnDayAtMinute(currentShift, hourMin)) {
    return { weekday, shift: currentShift };
  }
  const previousWeekday = getPreviousWeekday(weekday);
  const previousShift = getAdminShift(engineerId, previousWeekday);
  if (previousOvernightShiftCoversMinute(previousShift, hourMin)) {
    return { weekday: previousWeekday, shift: previousShift };
  }
  return null;
}

function isEngineerOnShiftAtHour(engineerId, weekday, hour) {
  return Boolean(getShiftForScheduleCell(engineerId, weekday, hour));
}

function getEngineerActiveShiftNow(engineerId, now = utc8Now()) {
  const dayIndex = (now.getUTCDay() + 6) % 7; // Monday=0
  const weekday = WEEKDAYS[dayIndex];
  const currentMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
  const currentShift = getAdminShift(engineerId, weekday);
  if (shiftStartsOnDayAtMinute(currentShift, currentMinutes)) {
    return { weekday, shift: currentShift };
  }
  const previousWeekday = getPreviousWeekday(weekday);
  const previousShift = getAdminShift(engineerId, previousWeekday);
  if (previousOvernightShiftCoversMinute(previousShift, currentMinutes)) {
    return { weekday: previousWeekday, shift: previousShift };
  }
  return null;
}

function getEngineersOnShiftNow() {
  const now = utc8Now();
  return DEMO_ENGINEERS.filter((eng) => Boolean(getEngineerActiveShiftNow(eng.id, now)));
}

function getShiftSummary(engineerId, weekday) {
  const daySchedule = getAdminShift(engineerId, weekday);
  if (!daySchedule || !daySchedule.start || !daySchedule.end) return "\u2014";
  return `${daySchedule.start}\u2013${daySchedule.end}`;
}

function saveAdminSchedule() {
  writeStorage(ASSIGNMENT_ADMIN_SCHEDULE_KEY, adminSchedule);
}

function getOnlineCoverage() {
  return DEMO_ENGINEERS.filter((eng) => ADMIN_PRESENCE_MOCK[eng.id] === "online").length;
}

function ticketSlaState(ticket = activeTicket) {
  if (!ticket || !ticket.assignedAt) {
    return { label: "3h SLA from assign", className: "is-muted", remainingMs: SLA_MS, overdue: false };
  }
  const elapsed = Date.now() - Number(ticket.assignedAt || 0);
  const remainingMs = SLA_MS - elapsed;
  if (remainingMs <= 0) {
    return { label: "SLA overdue", className: "is-danger", remainingMs, overdue: true };
  }
  if (remainingMs <= 30 * 60 * 1000) {
    return { label: `${formatDuration(remainingMs)} left`, className: "is-warning", remainingMs, overdue: false };
  }
  return { label: `${formatDuration(remainingMs)} left`, className: "is-success", remainingMs, overdue: false };
}

function formatDuration(ms) {
  const totalMinutes = Math.max(0, Math.ceil(ms / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function formatCountdown(ms) {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function getSlaCountdownLabel(sla) {
  if (!activeTicket || !activeTicket.assignedAt) {
    return "3h SLA from assign";
  }
  if (sla.overdue) {
    return "SLA overdue";
  }
  return formatCountdown(sla.remainingMs);
}

function canAssign() {
  return Boolean(getSelectedEngineer() && isInShift() && !activeTicket);
}

function addEvent(title, detail) {
  events = [
    {
      title,
      detail,
      createdAt: formatUtc8Time(),
    },
    ...events,
  ].slice(0, 8);
  writeStorage(ASSIGNMENT_EVENTS_KEY, events);
}

function saveQueue() {
  writeStorage(ASSIGNMENT_QUEUE_KEY, queue);
}

function saveActiveTicket() {
  writeStorage(ASSIGNMENT_ACTIVE_TICKET_KEY, activeTicket);
}

function saveWorkspaceActive() {
  writeStorage(ASSIGNMENT_WORKSPACE_KEY, workspaceActive);
}

function assignNextTicket() {
  if (!canAssign() || queue.length === 0) return false;
  const [next, ...rest] = queue;
  activeTicket = { ...next, assignedAt: Date.now(), engineerId: selectedEngineerId };
  queue = rest;
  saveActiveTicket();
  saveQueue();
  addEvent("Engineer Ticket assigned", `${activeTicket.id} assigned to ${selectedEngineerId}.`);
  return true;
}

function releaseActiveAssignment(title = "Assignment released", detail = "") {
  if (!activeTicket) return false;
  const { assignedAt, engineerId, ...ticket } = activeTicket;
  queue = [ticket, ...queue];
  activeTicket = null;
  stopSlaCountdown();
  saveActiveTicket();
  saveQueue();
  addEvent(title, detail || `${ticket.id} returned to queue.`);
  breakAfterCase = false;
  writeStorage(ASSIGNMENT_BREAK_AFTER_CASE_KEY, breakAfterCase);
  return true;
}

function pauseAssignmentOutsideShift() {
  if (isInShift() || !activeTicket) return false;
  releaseActiveAssignment(
    "Assignment paused",
    `${activeTicket.id} returned to queue because ${selectedEngineerId} is out of shift.`
  );
  return true;
}

// =============================================================================
// Login view
// =============================================================================

function renderLogin() {
  const selected = getCandidateEngineer();
  root.innerHTML = `
    <section class="login-view">
      <aside class="login-intro">
        <div>
          <div class="brand-lockup">
            <span class="brand-icon material-symbols-outlined" aria-hidden="true">assignment_ind</span>
            <div>
              <p class="eyebrow">Engineer Assignment</p>
              <strong>SupportPortal</strong>
            </div>
          </div>
          <h1>Start solving the assigned problem.</h1>
          <p class="intro-copy">
            Sign in with a demo engineer. Before entering the workspace you will see a readiness overview — then explicitly claim your next case.
          </p>
        </div>
        <ul class="policy-list" aria-label="Assignment policy">
          <li><span class="material-symbols-outlined" aria-hidden="true">schedule</span><span>Outside shift: I&rsquo;m ready to roll is disabled until your UTC+8 shift starts.</span></li>
          <li><span class="material-symbols-outlined" aria-hidden="true">assignment</span><span>Inside shift: click I&rsquo;m ready to roll to claim the next waiting case.</span></li>
          <li><span class="material-symbols-outlined" aria-hidden="true">fact_check</span><span>Engineer AI drafts the customer reply; the engineer approves and sends.</span></li>
        </ul>
      </aside>
      <section class="selector-panel">
        <div class="panel-head">
          <p class="eyebrow">Choose a demo engineer</p>
          <h2>Engineer login</h2>
          <p>Selection is stored locally for this mock assignment UI.</p>
        </div>
        <div id="engineer-selector" class="engineer-selector-grid" role="radiogroup" aria-label="Choose a demo engineer">
          ${DEMO_ENGINEERS.map((engineer) => renderEngineerOption(engineer, engineer.id === selected.id)).join("")}
        </div>
        <button class="btn btn-primary" type="button" data-action="enter-welcome">
          View readiness overview
          <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </button>
      </section>
    </section>
  `;
}

function renderEngineerOption(engineer, selected) {
  return `
    <button
      class="engineer-option ${selected ? "is-selected" : ""}"
      type="button"
      role="radio"
      aria-checked="${selected ? "true" : "false"}"
      data-engineer-id="${escapeHtml(engineer.id)}"
    >
      <span class="engineer-avatar" aria-hidden="true">${escapeHtml(engineer.initials)}</span>
      <span>
        <strong>${escapeHtml(engineer.name)}</strong>
        <span>${escapeHtml(engineer.role)}</span>
      </span>
      <span class="select-mark material-symbols-outlined" aria-hidden="true">${selected ? "radio_button_checked" : "radio_button_unchecked"}</span>
    </button>
  `;
}

// =============================================================================
// Welcome (readiness) view
// =============================================================================

function renderWelcome() {
  const engineer = getSelectedEngineer();
  if (!engineer) {
    renderLogin();
    return;
  }
  const inShift = isInShift();
  const sla = ticketSlaState();
  const waitingCount = queue.length;
  const hasActiveTicket = Boolean(activeTicket);

  root.innerHTML = `
    <section class="welcome-view">
      <header class="welcome-hero">
        <div class="welcome-hero-top">
          <div class="brand-lockup">
            <span class="brand-icon material-symbols-outlined" aria-hidden="true">bolt</span>
            <div>
              <p class="eyebrow">Concierge AI</p>
              <strong>Assignment Command</strong>
            </div>
          </div>
          <button class="btn btn-ghost" type="button" data-action="sign-out">Change engineer</button>
        </div>
        <div class="welcome-hero-body">
          <div class="welcome-engineer-card">
            <span class="engineer-avatar welcome-avatar" aria-hidden="true">${escapeHtml(engineer.initials)}</span>
            <div>
              <p class="eyebrow">Signed in as</p>
              <h1>${escapeHtml(engineer.name)}</h1>
              <p>${escapeHtml(engineer.role)}</p>
            </div>
          </div>
          <div class="welcome-status-strip">
            <span class="status-pill ${inShift ? "is-success" : "is-muted"}">${inShift ? "In shift" : "Out of shift"}</span>
            <span class="status-pill ${hasActiveTicket ? "is-warning" : "is-muted"}">${hasActiveTicket ? "Active ticket open" : "No active ticket"}</span>
            <span class="status-pill is-muted">${waitingCount} waiting in queue</span>
          </div>
        </div>
      </header>

      <section class="welcome-grid">
        <article class="panel-card welcome-info-card">
          <div class="section-head">
            <div>
              <p class="ticket-kicker">UTC+8 current time</p>
              <h2>${formatUtc8Time()}</h2>
            </div>
            <span class="material-symbols-outlined" aria-hidden="true">schedule</span>
          </div>
        </article>

        <article class="panel-card welcome-info-card">
          <div class="section-head">
            <div>
              <p class="ticket-kicker">Daily shift</p>
              <h2>${escapeHtml(shift.start)} &#8211; ${escapeHtml(shift.end)} UTC+8</h2>
            </div>
            <span class="material-symbols-outlined" aria-hidden="true">engineering</span>
          </div>
          <form class="welcome-shift-form" data-shift-form>
            <label class="field">
              <span class="field-label">Start</span>
              <input name="start" type="time" value="${escapeHtml(shift.start)}" required />
            </label>
            <label class="field">
              <span class="field-label">End</span>
              <input name="end" type="time" value="${escapeHtml(shift.end)}" required />
            </label>
            <div class="welcome-shift-actions">
              <button class="btn btn-ghost" type="submit">Save shift</button>
            </div>
          </form>
          ${!inShift ? `<p class="welcome-shift-note">Next shift: ${escapeHtml(nextShiftInfo())}</p>` : ""}
        </article>

        <article class="panel-card welcome-info-card">
          <div class="section-head">
            <div>
              <p class="ticket-kicker">Queue status</p>
              <h2>${waitingCount} case${waitingCount !== 1 ? "s" : ""} waiting</h2>
            </div>
            <span class="material-symbols-outlined" aria-hidden="true">queue</span>
          </div>
          ${waitingCount === 0 ? `<p class="welcome-shift-note">No waiting cases in the mock queue right now.</p>` : ""}
        </article>

        <article class="panel-card welcome-info-card">
          <div class="section-head">
            <div>
              <p class="ticket-kicker">Active ticket</p>
              <h2>${hasActiveTicket ? escapeHtml(activeTicket.id) : "None"}</h2>
            </div>
            <span class="material-symbols-outlined" aria-hidden="true">assignment</span>
          </div>
          ${hasActiveTicket ? `<p class="welcome-shift-note">${escapeHtml(activeTicket.title)} &#183; ${escapeHtml(sla.label)}</p>` : ""}
        </article>

        <article class="panel-card welcome-info-card welcome-sla-card">
          <div class="section-head">
            <div>
              <p class="ticket-kicker">SLA policy</p>
              <h2>3h from assignment</h2>
            </div>
            <span class="material-symbols-outlined" aria-hidden="true">timer</span>
          </div>
          <p class="welcome-shift-note">First response target. Overdue cases are flagged and eligible for transfer.</p>
        </article>
      </section>

      <div class="welcome-actions">
        <button
          class="btn btn-primary btn-ready"
          type="button"
          data-action="ready-to-roll"
          ${!inShift ? "disabled" : ""}
        >
          <span class="material-symbols-outlined" aria-hidden="true">rocket_launch</span>
          I&rsquo;m ready to roll
        </button>
        ${!inShift
          ? `<p class="welcome-disabled-reason">You are outside your UTC+8 shift. Ready is disabled until your shift starts. Next shift: ${escapeHtml(nextShiftInfo())}.</p>`
          : `<p class="welcome-ready-hint">Click to claim the next waiting case and enter the problem workspace.</p>`}
      </div>

      <div class="welcome-events panel-card">
        <div class="panel-head">
          <p class="eyebrow">Audit trail</p>
          <h3>Mock events</h3>
        </div>
        <div class="event-list">
          ${events.map((event) => `
            <article class="event-item">
              <strong>${escapeHtml(event.title)}</strong>
              <p>${escapeHtml(event.detail)} &#183; ${escapeHtml(event.createdAt)}</p>
            </article>
          `).join("") || '<article class="event-item"><strong>No events yet</strong><p>Assignment actions will appear here.</p></article>'}
        </div>
        <div class="sidebar-actions" style="margin-top:12px;">
          <button class="btn btn-ghost" type="button" data-action="reset-demo">Reset mock data</button>
        </div>
      </div>
    </section>
  `;
}

// =============================================================================
// Workspace view
// =============================================================================

function renderWorkspace() {
  const engineer = getSelectedEngineer();
  if (!engineer) {
    stopSlaCountdown();
    renderLogin();
    return;
  }
  pauseAssignmentOutsideShift();
  const inShift = isInShift();
  const eligible = canAssign();
  const sla = ticketSlaState();
  root.innerHTML = `
    <section class="assignment-shell">
      ${renderSidebarHtml(engineer, inShift, eligible, sla)}
      <main class="problem-workspace" aria-label="Problem workspace">
        ${renderWorkspaceHeaderHtml(inShift, sla)}
        ${activeTicket ? renderActiveTicketHtml(activeTicket, sla) : renderNoTicketHtml(inShift, eligible)}
      </main>
    </section>
  `;
  startSlaCountdown();
}

function renderSidebarHtml(engineer, inShift, eligible, sla) {
  return `
    <aside class="engineer-rail assignment-sidebar" aria-label="Engineer context" tabindex="0">
      <div class="sidebar-inner">
        <div class="rail-brand">
          <div class="rail-brand-icon">
            <span class="material-symbols-outlined" aria-hidden="true">bolt</span>
          </div>
          <div class="rail-brand-copy">
            <span class="rail-brand-title">Concierge AI</span>
            <span class="rail-brand-subtitle">Assignment Command</span>
          </div>
        </div>
        <div class="rail-compact-stack" aria-hidden="true">
          <span class="engineer-avatar mono rail-compact-avatar">${escapeHtml(engineer.initials)}</span>
          <span class="rail-compact-status ${inShift ? "is-success" : "is-muted"}">
            <span class="material-symbols-outlined" aria-hidden="true">schedule</span>
          </span>
          <span class="rail-compact-status ${activeTicket ? "is-warning" : "is-success"}">
            <span class="material-symbols-outlined" aria-hidden="true">${activeTicket ? "confirmation_number" : "task_alt"}</span>
          </span>
        </div>
        <section class="engineer-context-card panel-card">
          <div class="sidebar-profile">
            <span class="engineer-avatar mono" aria-hidden="true">${escapeHtml(engineer.initials)}</span>
            <div>
              <p class="eyebrow">Engineer context</p>
              <h2>${escapeHtml(engineer.name)}</h2>
              <p>${escapeHtml(engineer.role)}</p>
            </div>
          </div>
          <div class="status-pills">
            <span class="status-pill ${inShift ? "is-success" : "is-muted"}">${inShift ? "In shift" : "Out of shift"}</span>
            <span class="status-pill ${eligible ? "is-success" : "is-warning"}">${eligible ? "Ready for next" : "Not assignable"}</span>
          </div>
        </section>

        <section class="context-panel panel-card">
          <div class="panel-head">
            <p class="eyebrow">UTC+8 daily shift</p>
            <h3>Shift schedule</h3>
            <p>${formatUtc8Time()}</p>
          </div>
          <form class="shift-form" data-shift-form>
            <label class="field">
              <span class="field-label">Start</span>
              <input name="start" type="time" value="${escapeHtml(shift.start)}" required />
            </label>
            <label class="field">
              <span class="field-label">End</span>
              <input name="end" type="time" value="${escapeHtml(shift.end)}" required />
            </label>
            <button class="btn btn-ghost" type="submit">Save shift</button>
          </form>
        </section>

        <section class="context-panel panel-card">
          <div class="panel-head">
            <p class="eyebrow">Assignment</p>
            <h3>${activeTicket ? "One active ticket" : "No active Engineer Ticket"}</h3>
            <p>${activeTicket ? `${activeTicket.id} is locked until reply is sent.` : `${queue.length} tickets waiting in mock queue.`}</p>
          </div>
          <div class="sidebar-metrics">
            <span><strong>${queue.length}</strong> waiting</span>
            <span><strong>3h</strong> SLA</span>
            <span><strong>${escapeHtml(sla.label)}</strong></span>
          </div>
        </section>

        <section class="context-panel panel-card">
          <div class="panel-head">
            <p class="eyebrow">Audit trail</p>
            <h3>Mock events</h3>
          </div>
          <div class="event-list">
            ${events.map((event) => `
              <article class="event-item">
                <strong>${escapeHtml(event.title)}</strong>
                <p>${escapeHtml(event.detail)} &#183; ${escapeHtml(event.createdAt)}</p>
              </article>
            `).join("") || '<article class="event-item"><strong>No events yet</strong><p>Assignment actions will appear here.</p></article>'}
          </div>
        </section>

        <div class="sidebar-actions">
          <button class="btn btn-ghost" type="button" data-action="reset-demo">Reset mock data</button>
          <button class="btn btn-outline" type="button" data-action="sign-out">Change engineer</button>
        </div>
      </div>
    </aside>
  `;
}

function renderWorkspaceHeaderHtml(inShift, sla) {
  const title = activeTicket ? activeTicket.title : inShift ? "No active Engineer Ticket" : "Waiting for your UTC+8 shift";
  const breakButtonHtml = activeTicket ? `
    <button class="btn btn-ghost break-after-case-btn ${breakAfterCase ? "is-active" : ""}" type="button" data-action="toggle-break-after-case" aria-pressed="${breakAfterCase ? "true" : "false"}">
      ${breakAfterCase ? "Break queued after this case" : "Break after this case"}
    </button>
  ` : "";
  return `
    <header class="workspace-header">
      <div>
        <p class="eyebrow">Problem workspace</p>
        <h1>${escapeHtml(title)}</h1>
        <p>${activeTicket ? "Work the assigned problem with Engineer AI, then send the customer-facing reply." : "Click I&rsquo;m ready for the next case to claim a waiting ticket from the queue."}</p>
      </div>
      <div class="workspace-header-actions">
        <span class="current-ticket-sla ${escapeHtml(sla.className)}" data-sla-countdown>${escapeHtml(getSlaCountdownLabel(sla))}</span>
        ${breakButtonHtml}
      </div>
    </header>
  `;
}

function renderActiveTicketHtml(ticket, sla) {
  return `
    <article class="solver-board">
      <section class="problem-card panel-card customer-problem-card">
        <div class="section-head">
          <div>
            <p class="ticket-kicker">Current Engineer Ticket</p>
            <h2>Customer problem</h2>
          </div>
          <span class="priority-chip">${escapeHtml(ticket.priority)}</span>
        </div>
        <div class="ticket-meta">
          <span>${escapeHtml(ticket.id)}</span>
          <span>Client Ticket ${escapeHtml(ticket.clientTicket)}</span>
          <span>${escapeHtml(ticket.requester)}</span>
        </div>
        <p class="problem-statement">${escapeHtml(ticket.issue)}</p>
      </section>

      <section class="problem-card panel-card context-card">
        <div class="section-head">
          <div>
            <p class="ticket-kicker">Client AI context</p>
            <h2>Known facts</h2>
          </div>
        </div>
        <ul class="evidence-list">
          ${ticket.context.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ul>
      </section>

      <section class="problem-card panel-card investigation-panel">
        <div class="section-head">
          <div>
            <p class="ticket-kicker">Engineer AI investigation</p>
            <h2>Working conclusion</h2>
          </div>
          <span class="status-pill ${sla.overdue ? "is-danger" : "is-success"}">${sla.overdue ? "Timeout transfer" : "Ready to review"}</span>
        </div>
        <ol class="investigation-list">
          ${ticket.investigation.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ol>
        ${sla.overdue ? `
          <div class="timeout-callout" role="status">
            <strong>Timeout transfer</strong>
            <p>mark engineer timeout, then transfer to next eligible engineer when available.</p>
          </div>
        ` : ""}
      </section>

      <section class="problem-card panel-card reply-panel">
        <div class="section-head">
          <div>
            <p class="ticket-kicker">Draft Customer Reply</p>
            <h2>Approve final reply</h2>
          </div>
        </div>
        <div class="detail-investigation-draft">
          <p class="detail-investigation-draft-label">Draft Customer Reply</p>
          <div class="detail-investigation-draft-body">${escapeHtml(ticket.draft)}</div>
        </div>
        <div class="ticket-actions">
          <button class="btn btn-primary" type="button" data-action="approve-ticket">
            Approve & send customer reply
          </button>
          <button class="btn btn-ghost" type="button" data-action="simulate-timeout">
            Simulate timeout
          </button>
        </div>
      </section>
    </article>
  `;
}

function renderNoTicketHtml(inShift, eligible) {
  const title = inShift ? "No active Engineer Ticket" : "Waiting for your UTC+8 shift";
  const waitingCount = queue.length;

  if (inShift && waitingCount > 0) {
    return `
      <section class="no-ticket-state panel-card">
        <span class="material-symbols-outlined" aria-hidden="true">assignment_turned_in</span>
        <div>
          <p class="ticket-kicker">Ready</p>
          <h2>${waitingCount} case${waitingCount !== 1 ? "s" : ""} waiting in queue</h2>
          <p>You are in shift. Click below to claim the next waiting Engineer Ticket.</p>
        </div>
        <button class="btn btn-primary" type="button" data-action="ready-for-next">
          <span class="material-symbols-outlined" aria-hidden="true">rocket_launch</span>
          I&rsquo;m ready for the next case
        </button>
      </section>
    `;
  }

  if (inShift && waitingCount === 0) {
    return `
      <section class="no-ticket-state panel-card">
        <span class="material-symbols-outlined" aria-hidden="true">inventory_2</span>
        <div>
          <p class="ticket-kicker">Queue empty</p>
          <h2>No waiting cases</h2>
          <p>You are in shift, but the mock queue is currently empty. Check back later or reset the demo data to reload the initial queue.</p>
        </div>
      </section>
    `;
  }

  return `
    <section class="no-ticket-state panel-card">
      <span class="material-symbols-outlined" aria-hidden="true">schedule</span>
      <div>
        <p class="ticket-kicker">Out of shift</p>
        <h2>${title}</h2>
        <p>You are signed in, but assignment is paused outside your shift. When your UTC+8 shift starts, click I&rsquo;m ready for the next case to claim a ticket. Next shift: ${escapeHtml(nextShiftInfo())}.</p>
      </div>
    </section>
  `;
}

// =============================================================================
// Admin view
// =============================================================================

function renderAdmin() {
  const waitingCases = queue.map((ticket, index) => ({
    ...ticket,
    position: index + 1,
  }));

  const hasActive = Boolean(activeTicket);
  const onShiftNow = getEngineersOnShiftNow();
  const onlineCount = getOnlineCoverage();

  // Build weekly schedule grid HTML
  let scheduleGridHtml = "";
  for (let hour = 0; hour < 24; hour++) {
    const hourLabel = `${String(hour).padStart(2, "0")}:00`;
    let cellsHtml = "";
    for (let d = 0; d < 7; d++) {
      const weekday = WEEKDAYS[d];
      const covering = DEMO_ENGINEERS.filter((eng) =>
        isEngineerOnShiftAtHour(eng.id, weekday, hour)
      );
      let cellContent = "";
      if (covering.length > 0) {
        cellContent = covering
          .map((eng) => {
            const color = getEngineerColor(eng.id);
            const cellShift = getShiftForScheduleCell(eng.id, weekday, hour);
            const editWeekday = cellShift ? cellShift.weekday : weekday;
            return `<span
              class="schedule-chip"
              style="background:${color.bg};color:${color.fg}"
              data-engineer-id="${escapeHtml(eng.id)}"
              data-weekday="${editWeekday}"
              title="${escapeHtml(eng.name)}: ${getShiftSummary(eng.id, editWeekday)}"
            >${escapeHtml(eng.name)}</span>`;
          })
          .join("");
      }
      cellsHtml += `<div class="schedule-cell ${covering.length === 0 ? "is-empty" : ""}" data-weekday="${weekday}" data-hour="${hour}">${cellContent}</div>`;
    }
    scheduleGridHtml += `
      <div class="schedule-row">
        <div class="schedule-hour-label">${hourLabel}</div>
        ${cellsHtml}
      </div>`;
  }

  // Build edit panel HTML if editing
  let editPanelHtml = "";
  if (adminEditState) {
    const eng = DEMO_ENGINEERS.find((e) => e.id === adminEditState.engineerId);
    const currentShift = (adminSchedule[adminEditState.engineerId] || {})[adminEditState.weekday] || { start: "", end: "" };
    const dayLabel = WEEKDAY_LABELS[WEEKDAYS.indexOf(adminEditState.weekday)];
    editPanelHtml = `
      <aside class="admin-edit-panel" aria-label="Edit shift">
        <div class="admin-edit-panel-head">
          <div>
            <p class="eyebrow">Edit shift</p>
            <h3>${escapeHtml(eng ? eng.name : adminEditState.engineerId)} &middot; ${dayLabel}</h3>
          </div>
          <button class="btn btn-ghost admin-edit-close" type="button" data-action="admin-close-panel" aria-label="Close edit panel">
            <span class="material-symbols-outlined" aria-hidden="true">close</span>
          </button>
        </div>
        <form class="admin-edit-form" data-action="admin-save-shift" data-shift-edit-form>
          <div class="field">
            <label class="field-label" for="admin-edit-start">Start (UTC+8)</label>
            <input id="admin-edit-start" name="start" type="time" value="${escapeHtml(currentShift.start)}" />
          </div>
          <div class="field">
            <label class="field-label" for="admin-edit-end">End (UTC+8)</label>
            <input id="admin-edit-end" name="end" type="time" value="${escapeHtml(currentShift.end)}" />
          </div>
          <p class="admin-edit-hint">Leave both empty to clear this day&rsquo;s shift. Overnight shifts (e.g. 22:00&ndash;06:00) are supported.</p>
          <div class="admin-edit-actions">
            <button class="btn btn-ghost" type="button" data-action="admin-cancel-edit">Cancel</button>
            <button class="btn btn-primary" type="submit">Save shift</button>
          </div>
        </form>
      </aside>`;
  }

  // Compute derived metrics
  var assignmentVolume = queue.length + (activeTicket ? 1 : 0) + events.length;
  var highPriCount = waitingCases.filter(function (t) { return t.priority && t.priority.toLowerCase().indexOf("first") !== -1; }).length;

  root.innerHTML = `
    <div class="admin-shell">
      <!-- ====== Top Navigation Bar ====== -->
      <nav class="admin-topbar">
        <div class="admin-topbar-brand">
          <span class="material-symbols-outlined" aria-hidden="true">admin_panel_settings</span>
          <span>Nexus Intelligence</span>
        </div>
        <div class="admin-topbar-search">
          <span class="material-symbols-outlined" aria-hidden="true">search</span>
          <input type="text" placeholder="Search systems, tickets, engineers..." aria-label="Search" />
        </div>
        <div class="admin-topbar-actions">
          <a href="/assignment" class="admin-topbar-btn" title="Back to engineer demo" aria-label="Back to engineer demo">
            <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
          </a>
          <button class="admin-topbar-btn" aria-label="Notifications">
            <span class="material-symbols-outlined" aria-hidden="true">notifications</span>
          </button>
          <button class="admin-topbar-btn" aria-label="Settings">
            <span class="material-symbols-outlined" aria-hidden="true">settings</span>
          </button>
          <button class="admin-topbar-btn" aria-label="Help">
            <span class="material-symbols-outlined" aria-hidden="true">help_outline</span>
          </button>
          <div class="admin-topbar-avatar" aria-label="Administrator profile">
            <span class="material-symbols-outlined" aria-hidden="true" style="font-size:20px">person</span>
          </div>
        </div>
      </nav>

      <!-- ====== Body: Sidebar + Main ====== -->
      <div class="admin-body">
        <!-- Sidebar -->
        <aside class="admin-sidebar">
          <div class="admin-sidebar-header">
            <div class="admin-sidebar-identity">
              <div class="admin-sidebar-icon">
                <span class="material-symbols-outlined" aria-hidden="true">admin_panel_settings</span>
              </div>
              <div>
                <h2>System Admin</h2>
                <p>Global Operations</p>
              </div>
            </div>
            <button class="admin-sidebar-ai-btn">
              <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
              AI Insights
            </button>
          </div>
          <nav class="admin-sidebar-nav">
            <ul>
              <li><a href="#">
                <span class="material-symbols-outlined" aria-hidden="true">confirmation_number</span>
                Ticket Pool
              </a></li>
              <li><a href="#" class="is-active">
                <span class="material-symbols-outlined" aria-hidden="true">engineering</span>
                Engineer Management
              </a></li>
              <li><a href="#">
                <span class="material-symbols-outlined" aria-hidden="true">menu_book</span>
                Knowledge Base
              </a></li>
              <li><a href="#">
                <span class="material-symbols-outlined" aria-hidden="true">monitoring</span>
                System Health
              </a></li>
            </ul>
          </nav>
          <div class="admin-sidebar-footer">
            <a href="#">
              <span class="material-symbols-outlined" aria-hidden="true">contact_support</span>
              Support
            </a>
            <a href="#">
              <span class="material-symbols-outlined" aria-hidden="true">terminal</span>
              Logs
            </a>
          </div>
        </aside>

        <!-- Main Content Area -->
        <main class="admin-main ${adminEditState ? "has-edit-panel" : ""}">
          <!-- Header -->
          <header class="admin-main-header">
            <div>
              <h1>Operations Overview</h1>
              <p>Real-time engineer distribution and queue status.</p>
            </div>
          </header>

          <!-- Metric Cards -->
          <div class="admin-metric-grid">
            <div class="admin-metric-card">
              <div class="admin-metric-card-top">
                <span class="admin-metric-label">Cases in Queue</span>
                <span class="material-symbols-outlined" aria-hidden="true">pending_actions</span>
              </div>
              <div class="admin-metric-value">${waitingCases.length}</div>
              <div class="admin-metric-sub" style="color: ${waitingCases.length > 0 ? 'var(--danger)' : 'var(--success)'}">
                ${waitingCases.length > 0
                  ? '<span class="material-symbols-outlined" aria-hidden="true">trending_up</span> Needs attention'
                  : '<span class="material-symbols-outlined" aria-hidden="true">check_circle</span> All clear'}
              </div>
            </div>
            <div class="admin-metric-card">
              <div class="admin-metric-card-top">
                <span class="admin-metric-label">On Shift Engineers</span>
                <span class="material-symbols-outlined" aria-hidden="true">group</span>
              </div>
              <div class="admin-metric-value is-accent">${onShiftNow.length}</div>
              <div class="admin-metric-sub" style="color: var(--success)">
                <span class="material-symbols-outlined" aria-hidden="true" style="font-size:10px">circle</span>
                ${onlineCount} Online
              </div>
            </div>
            <div class="admin-metric-card">
              <div class="admin-metric-card-top">
                <span class="admin-metric-label">Assignment Volume</span>
                <span class="material-symbols-outlined" aria-hidden="true">stacked_line_chart</span>
              </div>
              <div class="admin-metric-value">${assignmentVolume}</div>
              <div class="admin-metric-sub" style="color: var(--ink-muted)">
                Queue + active + events
              </div>
            </div>
            <div class="admin-metric-card">
              <div class="admin-metric-card-top">
                <span class="admin-metric-label">Online Coverage</span>
                <span class="material-symbols-outlined" aria-hidden="true">wifi</span>
              </div>
              <div class="admin-metric-value is-accent">${onlineCount}/${DEMO_ENGINEERS.length}</div>
              <div class="admin-metric-sub" style="color: ${onlineCount >= DEMO_ENGINEERS.length ? 'var(--success)' : 'var(--ink-muted)'}">
                Engineers online
              </div>
            </div>
          </div>

          <!-- Shift Schedule -->
          <section class="admin-main-schedule" id="admin-schedule-section">
            <div class="panel-card">
              <div class="admin-schedule-card-header">
                <h2>
                  <span class="material-symbols-outlined" aria-hidden="true">calendar_view_week</span>
                  Shift Schedule
                </h2>
                <button class="admin-modify-shifts-btn" type="button" data-action="admin-modify-shifts">
                  <span class="material-symbols-outlined" aria-hidden="true">edit_calendar</span>
                  Modify Shifts
                </button>
              </div>
              <div class="admin-schedule-grid-wrapper" style="border:0; box-shadow:none; background:transparent; padding:14px;">
                <div class="section-head admin-schedule-head" style="display:none"></div>
                <form class="admin-shift-picker" data-admin-shift-picker>
                  <label class="field">
                    <span class="field-label" id="admin-picker-engineer-label">Engineer</span>
                    <select name="engineerId" aria-labelledby="admin-picker-engineer-label">
                      ${DEMO_ENGINEERS.map((eng) => `<option value="${escapeHtml(eng.id)}">${escapeHtml(eng.name)}</option>`).join("")}
                    </select>
                  </label>
                  <label class="field">
                    <span class="field-label" id="admin-picker-day-label">Day</span>
                    <select name="weekday" aria-labelledby="admin-picker-day-label">
                      ${WEEKDAYS.map((weekday, index) => `<option value="${weekday}">${WEEKDAY_LABELS[index]}</option>`).join("")}
                    </select>
                  </label>
                  <button class="btn btn-ghost" type="submit">Edit selected shift</button>
                </form>
                <div class="admin-schedule-grid-scroll">
                  <div class="admin-schedule-grid">
                    <div class="schedule-header-row">
                      <div class="schedule-hour-label"></div>
                      ${WEEKDAY_LABELS.map((label, d) => `<div class="schedule-day-label"><span>${label}</span></div>`).join("")}
                    </div>
                    ${scheduleGridHtml}
                  </div>
                </div>
              </div>
            </div>
          </section>

          <!-- Bottom Grid: Pending Triage + Active Work Distribution -->
          <div class="admin-bottom-grid">
            <!-- Pending Triage -->
            <section class="admin-bottom-card">
              <div class="admin-bottom-card-header">
                <h3>Pending Triage</h3>
                ${highPriCount > 0 ? `<span class="admin-prio-badge">${highPriCount} High Pri</span>` : ""}
              </div>
              <div class="admin-bottom-card-body">
                ${waitingCases.length === 0 ? `
                  <div style="padding:24px;text-align:center;color:var(--ink-muted);font-size:13px;">
                    <span class="material-symbols-outlined" aria-hidden="true" style="font-size:32px;display:block;margin-bottom:8px;">inventory_2</span>
                    Queue is empty
                  </div>
                ` : waitingCases.slice(0, 8).map((ticket) => `
                  <div class="admin-triage-item">
                    <div class="admin-triage-item-top">
                      <span class="admin-triage-id">#${escapeHtml(ticket.id)}</span>
                      <span class="admin-triage-sla">
                        <span class="material-symbols-outlined" aria-hidden="true">timer</span>
                        ${ticket.priority && ticket.priority.toLowerCase().indexOf("first") !== -1 ? "Urgent" : "Standard"}
                      </span>
                    </div>
                    <span class="admin-triage-summary">${escapeHtml(ticket.issue)}</span>
                  </div>
                `).join("")}
              </div>
            </section>

            <!-- Active Work Distribution -->
            <section class="admin-bottom-card">
              <div class="admin-bottom-card-header">
                <h3>Active Work Distribution</h3>
              </div>
              <div class="admin-bottom-card-body">
                <table class="admin-work-table">
                  <thead>
                    <tr>
                      <th>Engineer</th>
                      <th>Active Ticket</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${DEMO_ENGINEERS.map((eng) => {
                      var color = getEngineerColor(eng.id);
                      var isOnline = ADMIN_PRESENCE_MOCK[eng.id] === "online";
                      var isOnShift = onShiftNow.some(function (e) { return e.id === eng.id; });
                      var hasTicket = hasActive && activeTicket.engineerId === eng.id;
                      var statusHtml = "";
                      var ticketHtml = '<span style="color:var(--ink-muted)">\u2014</span>';

                      if (hasTicket) {
                        ticketHtml = '<span class="admin-work-ticket">#' + escapeHtml(activeTicket.id) + '</span>';
                        statusHtml = '<span class="admin-work-status is-active"><span class="material-symbols-outlined" aria-hidden="true">psychiatry</span>Active</span>';
                      } else if (isOnShift && isOnline) {
                        statusHtml = '<span class="admin-work-status is-available"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>Available</span>';
                      } else if (isOnShift && !isOnline) {
                        statusHtml = '<span class="admin-work-status is-offline"><span class="material-symbols-outlined" aria-hidden="true">wifi_off</span>Offline</span>';
                      } else if (!isOnShift && isOnline) {
                        statusHtml = '<span class="admin-work-status"><span class="material-symbols-outlined" aria-hidden="true">bedtime</span>Off shift</span>';
                      } else {
                        statusHtml = '<span class="admin-work-status is-offline"><span class="material-symbols-outlined" aria-hidden="true">do_not_disturb</span>Offline</span>';
                      }

                      return '<tr>' +
                        '<td>' +
                          '<div class="admin-work-engineer">' +
                            '<div class="admin-work-avatar" style="background:' + color.bg + ';color:' + color.fg + '">' + escapeHtml(eng.initials) + '</div>' +
                            '<span class="admin-work-name">' + escapeHtml(eng.name) + '</span>' +
                          '</div>' +
                        '</td>' +
                        '<td>' + ticketHtml + '</td>' +
                        '<td>' + statusHtml + '</td>' +
                      '</tr>';
                    }).join("")}
                  </tbody>
                </table>
              </div>
            </section>
          </div>

          <!-- Audit Trail (collapsed section) -->
          <section class="admin-events-collapsed">
            <div class="panel-card">
              <div class="panel-head">
                <p class="eyebrow">Audit trail</p>
                <h3>Mock events</h3>
              </div>
              <div class="event-list">
                ${events.map((event) => `
                  <article class="event-item">
                    <strong>${escapeHtml(event.title)}</strong>
                    <p>${escapeHtml(event.detail)} &#183; ${escapeHtml(event.createdAt)}</p>
                  </article>
                `).join("") || '<article class="event-item"><strong>No events yet</strong><p>Assignment actions will appear here.</p></article>'}
              </div>
            </div>
          </section>

          ${editPanelHtml}
        </main>
      </div>
    </div>
  `;
}

// =============================================================================
// Actions
// =============================================================================

function enterWelcome() {
  selectedEngineerId = getCandidateEngineer().id;
  writeStorage(ASSIGNMENT_AUTH_KEY, selectedEngineerId);
  workspaceActive = false;
  saveWorkspaceActive();
  addEvent("Engineer selected", `${selectedEngineerId} opened /assignment.`);
  renderWelcome();
}

function readyToRoll() {
  const engineer = getSelectedEngineer();
  if (!engineer) {
    renderLogin();
    return;
  }
  if (!isInShift()) {
    renderWelcome();
    return;
  }

  if (activeTicket && activeTicket.engineerId !== selectedEngineerId) {
    releaseActiveAssignment(
      "Assignment released",
      `${activeTicket.id} returned to queue before ${selectedEngineerId} entered the workspace.`
    );
  }

  cancelReadyTransition();
  const transitionEngineerId = selectedEngineerId;
  readyTransitionActive = true;
  renderReadyLoading();
  readyTransitionTimer = window.setTimeout(() => {
    readyTransitionTimer = null;
    if (!readyTransitionActive || selectedEngineerId !== transitionEngineerId) return;
    readyTransitionActive = false;

    if (!getSelectedEngineer()) {
      renderLogin();
      return;
    }
    if (!isInShift()) {
      workspaceActive = false;
      saveWorkspaceActive();
      renderWelcome();
      return;
    }

    if (!activeTicket) {
      assignNextTicket();
    }

    workspaceActive = true;
    saveWorkspaceActive();
    addEvent("Ready to roll", `${selectedEngineerId} entered the problem workspace.`);
    renderWorkspace();
  }, 900);
}

function readyForNextCase() {
  if (!canAssign()) {
    renderWorkspace();
    return;
  }
  assignNextTicket();
  renderWorkspace();
}

function renderReadyLoading() {
  const engineer = getSelectedEngineer();
  root.innerHTML = `
    <section class="ready-loading-view">
      <div class="ready-loading-card">
        <div class="ready-loading-spinner" aria-label="Preparing your workspace">
          <span class="material-symbols-outlined" aria-hidden="true">bolt</span>
        </div>
        <h1>Preparing your workspace</h1>
        <p>Engineer AI is handing off the assignment queue for ${escapeHtml(engineer ? engineer.name : "you")}.</p>
        <div class="ready-loading-bar">
          <span class="ready-loading-bar-fill"></span>
        </div>
      </div>
    </section>
  `;
}

function cancelReadyTransition() {
  if (readyTransitionTimer) {
    window.clearTimeout(readyTransitionTimer);
    readyTransitionTimer = null;
  }
  readyTransitionActive = false;
}

function approveTicket() {
  if (!activeTicket) return;
  addEvent("Customer reply sent", `${activeTicket.id} approved and closed by ${selectedEngineerId}.`);

  const shouldBreak = breakAfterCase;
  activeTicket = null;
  saveActiveTicket();

  if (shouldBreak) {
    breakAfterCase = false;
    writeStorage(ASSIGNMENT_BREAK_AFTER_CASE_KEY, breakAfterCase);
    workspaceActive = false;
    saveWorkspaceActive();
    stopSlaCountdown();
    renderWelcome();
  } else {
    renderWorkspace();
  }
}

function simulateTimeout() {
  if (!activeTicket) return;
  activeTicket = { ...activeTicket, assignedAt: Date.now() - SLA_MS - 60000 };
  saveActiveTicket();
  addEvent("Timeout transfer", `${activeTicket.id}: mark engineer timeout, transfer to next eligible engineer.`);
  renderWorkspace();
}

function saveShift(form) {
  const formData = new FormData(form);
  shift = {
    start: String(formData.get("start") || DEFAULT_SHIFT.start),
    end: String(formData.get("end") || DEFAULT_SHIFT.end),
  };
  writeStorage(ASSIGNMENT_SHIFT_KEY, shift);
  addEvent("Shift updated", `${shift.start}-${shift.end} UTC+8 daily shift saved.`);
  if (workspaceActive) {
    renderWorkspace();
  } else {
    renderWelcome();
  }
}

function signOut() {
  cancelReadyTransition();
  stopSlaCountdown();
  releaseActiveAssignment();
  selectedEngineerId = "";
  workspaceActive = false;
  breakAfterCase = false;
  localStorage.removeItem(ASSIGNMENT_AUTH_KEY);
  localStorage.removeItem(ASSIGNMENT_WORKSPACE_KEY);
  localStorage.removeItem(ASSIGNMENT_BREAK_AFTER_CASE_KEY);
  renderLogin();
}

function resetDemo() {
  cancelReadyTransition();
  shift = { ...DEFAULT_SHIFT };
  activeTicket = null;
  stopSlaCountdown();
  queue = INITIAL_QUEUE.map((ticket) => ({ ...ticket }));
  events = [];
  workspaceActive = false;
  breakAfterCase = false;
  adminSchedule = normalizeAdminSchedule(DEFAULT_ADMIN_SCHEDULE);
  adminEditState = null;
  writeStorage(ASSIGNMENT_SHIFT_KEY, shift);
  writeStorage(ASSIGNMENT_QUEUE_KEY, queue);
  writeStorage(ASSIGNMENT_ACTIVE_TICKET_KEY, activeTicket);
  writeStorage(ASSIGNMENT_EVENTS_KEY, events);
  writeStorage(ASSIGNMENT_WORKSPACE_KEY, workspaceActive);
  writeStorage(ASSIGNMENT_BREAK_AFTER_CASE_KEY, breakAfterCase);
  writeStorage(ASSIGNMENT_ADMIN_SCHEDULE_KEY, adminSchedule);
  renderWelcome();
}

function toggleSidebar() {
  sidebarCollapsed = !sidebarCollapsed;
  writeStorage(ASSIGNMENT_SIDEBAR_KEY, sidebarCollapsed);
  renderWorkspace();
}

function toggleBreakAfterCase() {
  breakAfterCase = !breakAfterCase;
  writeStorage(ASSIGNMENT_BREAK_AFTER_CASE_KEY, breakAfterCase);
  renderWorkspace();
}

function stopSlaCountdown() {
  if (slaCountdownTimer) {
    clearInterval(slaCountdownTimer);
    slaCountdownTimer = null;
  }
}

function startSlaCountdown() {
  stopSlaCountdown();
  if (!activeTicket) return;
  slaCountdownTimer = setInterval(() => {
    if (!activeTicket) {
      stopSlaCountdown();
      return;
    }
    const sla = ticketSlaState();
    const el = root.querySelector("[data-sla-countdown]");
    if (!el) {
      stopSlaCountdown();
      return;
    }
    el.textContent = getSlaCountdownLabel(sla);
    el.className = `current-ticket-sla ${sla.className}`;
    if (sla.overdue) {
      stopSlaCountdown();
    }
  }, 1000);
}

// =============================================================================
// Event delegation
// =============================================================================

root.addEventListener("click", (event) => {
  // Admin schedule chip click — opens edit panel
  if (isAdminPage) {
    const scheduleChip = event.target.closest(".schedule-chip[data-engineer-id][data-weekday]");
    if (scheduleChip) {
      const engineerId = String(scheduleChip.dataset.engineerId || "");
      const weekday = String(scheduleChip.dataset.weekday || "");
      if (engineerId && weekday) {
        adminEditState = { engineerId, weekday };
        renderAdmin();
        return;
      }
    }
  }

  const engineerButton = event.target.closest("[data-engineer-id]");
  if (engineerButton) {
    selectedEngineerCandidate = String(engineerButton.dataset.engineerId || DEMO_ENGINEERS[0].id);
    renderLogin();
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  const action = String(actionButton.dataset.action || "");

  if (action === "enter-welcome") enterWelcome();
  if (action === "ready-to-roll") readyToRoll();
  if (action === "ready-for-next") readyForNextCase();
  if (action === "sign-out") signOut();
  if (action === "approve-ticket") approveTicket();
  if (action === "simulate-timeout") simulateTimeout();
  if (action === "reset-demo") resetDemo();
  if (action === "toggle-sidebar") toggleSidebar();
  if (action === "toggle-break-after-case") toggleBreakAfterCase();
  if (action === "admin-close-panel" || action === "admin-cancel-edit") {
    adminEditState = null;
    renderAdmin();
  }
  if (action === "admin-modify-shifts") {
    var pickerForm = root.querySelector("[data-admin-shift-picker]");
    if (pickerForm) {
      pickerForm.scrollIntoView({ behavior: "smooth", block: "center" });
      var firstSelect = pickerForm.querySelector("select");
      if (firstSelect) firstSelect.focus();
    }
  }
});

root.addEventListener("submit", (event) => {
  if (event.target.matches("[data-shift-form]")) {
    event.preventDefault();
    saveShift(event.target);
  }
  if (event.target.matches("[data-admin-shift-picker]")) {
    event.preventDefault();
    const formData = new FormData(event.target);
    const engineerId = String(formData.get("engineerId") || "");
    const weekday = String(formData.get("weekday") || "");
    if (engineerId && WEEKDAYS.includes(weekday)) {
      adminEditState = { engineerId, weekday };
      renderAdmin();
    }
  }
  if (event.target.matches("[data-shift-edit-form]")) {
    event.preventDefault();
    const formData = new FormData(event.target);
    const start = String(formData.get("start") || "").trim();
    const end = String(formData.get("end") || "").trim();
    if (adminEditState) {
      if (!adminSchedule[adminEditState.engineerId]) {
        adminSchedule[adminEditState.engineerId] = {};
      }
      if (start && end) {
        adminSchedule[adminEditState.engineerId][adminEditState.weekday] = { start, end };
      } else {
        adminSchedule[adminEditState.engineerId][adminEditState.weekday] = { start: "", end: "" };
      }
      saveAdminSchedule();
      addEvent("Admin schedule updated", `${adminEditState.engineerId} ${adminEditState.weekday}: ${start && end ? `${start}-${end}` : "cleared"}`);
      adminEditState = null;
      renderAdmin();
    }
  }
});

window.setInterval(() => {
  if (readyTransitionActive) return;
  if (isAdminPage) {
    renderAdmin();
    return;
  }
  const engineer = getSelectedEngineer();
  if (engineer) {
    if (workspaceActive) {
      renderWorkspace();
    } else {
      renderWelcome();
    }
  }
}, 30000);

// =============================================================================
// Initial render
// =============================================================================

if (isAdminPage) {
  renderAdmin();
} else if (selectedEngineerId) {
  if (workspaceActive) {
    renderWorkspace();
  } else {
    renderWelcome();
  }
} else {
  renderLogin();
}
