const ASSIGNMENT_AUTH_KEY = "supportportal_assignment_selected_engineer";
const ASSIGNMENT_SHIFT_KEY = "supportportal_assignment_daily_shift";
const ASSIGNMENT_ACTIVE_TICKET_KEY = "supportportal_assignment_active_ticket";
const ASSIGNMENT_QUEUE_KEY = "supportportal_assignment_queue";
const ASSIGNMENT_EVENTS_KEY = "supportportal_assignment_events";
const SLA_MS = 3 * 60 * 60 * 1000;
const UTC8_OFFSET_MS = 8 * 60 * 60 * 1000;

const DEMO_ENGINEERS = [
  { id: "Jack", name: "Jack", role: "Tier One Engineer", initials: "J" },
  { id: "Maya", name: "Maya", role: "Tier One Engineer", initials: "M" },
  { id: "Leo", name: "Leo", role: "Tier One Engineer", initials: "L" },
];

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
    issue: "Client AI opened this Engineer Ticket for one unresolved display issue.",
    draft:
      "Hi there, we found that the device is booting but the display service is not restarting after the firmware update. Please restart the display service once, then confirm whether the screen returns before we schedule a replacement.",
  },
  {
    id: "TK-041-1",
    title: "VPN connection drops every 20 minutes",
    clientTicket: "TK-041",
    requester: "Northwind IT",
    issue: "Client AI needs engineer confirmation before sending network remediation steps.",
    draft:
      "Hi there, the logs point to a session rekey mismatch. Please update the VPN profile to the current gateway policy and reconnect. If the drop repeats, send us the latest tunnel log timestamp.",
  },
  {
    id: "TK-042-1",
    title: "Billing export missing settled route items",
    clientTicket: "TK-042",
    requester: "Finance Admin",
    issue: "Client AI escalated one precise product question for engineer review.",
    draft:
      "Hi there, the export is filtering out settled route items because the selected report type only includes open items. Switch the report scope to All route items, then regenerate the export.",
  },
];

let selectedEngineerId = readStorage(ASSIGNMENT_AUTH_KEY, "");
let selectedEngineerCandidate = selectedEngineerId || DEMO_ENGINEERS[0].id;
let shift = readStorage(ASSIGNMENT_SHIFT_KEY, DEFAULT_SHIFT);
let activeTicket = readStorage(ASSIGNMENT_ACTIVE_TICKET_KEY, null);
let queue = readStorage(ASSIGNMENT_QUEUE_KEY, INITIAL_QUEUE);
let events = readStorage(ASSIGNMENT_EVENTS_KEY, []);

const root = document.getElementById("assignment-root");

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

function ticketSlaState(ticket = activeTicket) {
  if (!ticket?.assignedAt) {
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
          <h1>Shift-aware assignment.</h1>
          <p class="intro-copy">
            Mock the first assignment surface without changing the existing Engineer Portal.
          </p>
        </div>
        <ul class="policy-list" aria-label="Assignment policy">
          <li><span class="material-symbols-outlined" aria-hidden="true">schedule</span><span>UTC+8 daily shift controls assignment eligibility.</span></li>
          <li><span class="material-symbols-outlined" aria-hidden="true">filter_1</span><span>One engineer works one active Engineer Ticket at a time.</span></li>
          <li><span class="material-symbols-outlined" aria-hidden="true">timer</span><span>3h SLA from assign drives timeout and transfer state.</span></li>
        </ul>
      </aside>
      <section class="selector-panel">
        <div class="panel-head">
          <p class="eyebrow">Choose a demo engineer</p>
          <h2>Start with a shift identity</h2>
          <p>Selection is stored locally for this mock assignment UI.</p>
        </div>
        <div id="engineer-selector" class="engineer-selector-grid" role="radiogroup" aria-label="Choose a demo engineer">
          ${DEMO_ENGINEERS.map((engineer) => renderEngineerOption(engineer, engineer.id === selected.id)).join("")}
        </div>
        <button class="primary-action" type="button" data-action="enter-workspace">
          Enter assignment workspace
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

function renderWorkspace() {
  const engineer = getSelectedEngineer();
  if (!engineer) {
    renderLogin();
    return;
  }
  const inShift = isInShift();
  const eligible = canAssign();
  const sla = ticketSlaState();
  root.innerHTML = `
    <section class="workspace-view">
      <header class="workspace-topbar">
        <div class="workspace-identity">
          <span class="engineer-avatar" aria-hidden="true">${escapeHtml(engineer.initials)}</span>
          <div>
            <p class="eyebrow">Engineer Assignment</p>
            <h1>${escapeHtml(engineer.name)}</h1>
            <p>${formatUtc8Time()}</p>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="ghost-action" type="button" data-action="reset-demo">Reset mock data</button>
          <button class="secondary-action" type="button" data-action="sign-out">Change engineer</button>
        </div>
      </header>

      ${renderAssignmentStatusStripHtml({ engineer, inShift, eligible, sla })}

      <div class="workspace-grid">
        <aside class="side-stack">
          ${renderShiftPanelHtml(inShift)}
          ${renderQueuePanelHtml()}
          ${renderEventPanelHtml()}
        </aside>
        <main class="main-stack">
          <section class="ticket-workbench" aria-label="Current assignment workbench">
            ${activeTicket ? renderActiveTicketHtml(activeTicket, sla) : renderEmptyTicketHtml(eligible)}
          </section>
        </main>
      </div>
    </section>
  `;
}

function renderAssignmentStatusStripHtml({ engineer, inShift, eligible, sla }) {
  return `
    <section class="assignment-status-strip" aria-label="Assignment status">
      <article class="metric-tile">
        <span class="metric-label">Engineer</span>
        <strong class="metric-value">${escapeHtml(engineer.name)}</strong>
        <p class="metric-note">Demo selector login</p>
      </article>
      <article class="metric-tile">
        <span class="metric-label">UTC+8 daily shift</span>
        <strong class="metric-value">${escapeHtml(shift.start)}-${escapeHtml(shift.end)}</strong>
        <p class="metric-note">${inShift ? "In shift" : "Out of shift"}</p>
      </article>
      <article class="metric-tile">
        <span class="metric-label">Assignment</span>
        <strong class="metric-value">${eligible ? "Eligible for assignment" : "Not assignable"}</strong>
        <p class="metric-note">${activeTicket ? "Active Engineer Ticket locked" : "Single-case policy"}</p>
      </article>
      <article class="metric-tile">
        <span class="metric-label">SLA Policy</span>
        <strong class="metric-value">3h SLA from assign</strong>
        <p class="metric-note">${escapeHtml(sla.label)}</p>
      </article>
    </section>
  `;
}

function renderShiftPanelHtml(inShift) {
  return `
    <section class="shift-panel">
      <div class="panel-head">
        <p class="eyebrow">UTC+8 daily shift</p>
        <h2>Shift schedule</h2>
        <p>Manual fixed daily hours for this prototype.</p>
      </div>
      <div class="status-pills">
        <span class="status-pill ${inShift ? "is-success" : "is-muted"}">${inShift ? "In shift" : "Out of shift"}</span>
        <span class="status-pill ${canAssign() ? "is-success" : "is-warning"}">${canAssign() ? "Eligible for assignment" : "Not assignable"}</span>
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
        <button class="primary-action" type="submit">Save shift</button>
      </form>
    </section>
  `;
}

function renderQueuePanelHtml() {
  return `
    <section class="queue-panel">
      <div class="panel-head">
        <p class="eyebrow">Mock queue</p>
        <h2>Waiting Engineer Tickets</h2>
        <p>${queue.length} one-question Engineer Tickets waiting.</p>
      </div>
      <div class="queue-actions">
        <button class="primary-action" type="button" data-action="assign-next" ${canAssign() ? "" : "disabled"}>
          Assign next mock ticket
        </button>
      </div>
      <div class="queue-list">
        ${queue.map((ticket) => `
          <article class="queue-item">
            <strong>${escapeHtml(ticket.id)} · ${escapeHtml(ticket.title)}</strong>
            <p>${escapeHtml(ticket.requester)} · Client Ticket ${escapeHtml(ticket.clientTicket)}</p>
          </article>
        `).join("") || '<article class="queue-item"><strong>Queue clear</strong><p>No mock Engineer Tickets are waiting.</p></article>'}
      </div>
    </section>
  `;
}

function renderEventPanelHtml() {
  return `
    <section class="event-panel">
      <div class="panel-head">
        <p class="eyebrow">Audit trail</p>
        <h2>Mock events</h2>
      </div>
      <div class="event-list">
        ${events.map((event) => `
          <article class="event-item">
            <strong>${escapeHtml(event.title)}</strong>
            <p>${escapeHtml(event.detail)} · ${escapeHtml(event.createdAt)}</p>
          </article>
        `).join("") || '<article class="event-item"><strong>No events yet</strong><p>Assignment actions will appear here.</p></article>'}
      </div>
    </section>
  `;
}

function renderActiveTicketHtml(ticket, sla) {
  return `
    <section class="current-ticket-card">
      <div class="ticket-head">
        <div>
          <p class="ticket-kicker">Current Engineer Ticket</p>
          <h2>${escapeHtml(ticket.title)}</h2>
          <div class="ticket-meta">
            <span>${escapeHtml(ticket.id)}</span>
            <span>Client Ticket ${escapeHtml(ticket.clientTicket)}</span>
            <span>${escapeHtml(ticket.requester)}</span>
          </div>
        </div>
        <span class="current-ticket-sla ${escapeHtml(sla.className)}">${escapeHtml(sla.label)}</span>
      </div>
      <p>${escapeHtml(ticket.issue)}</p>
      <section class="draft-panel">
        <p class="eyebrow">Engineer AI Draft Customer Reply</p>
        <p>${escapeHtml(ticket.draft)}</p>
      </section>
      <div class="ticket-actions">
        <button class="primary-action" type="button" data-action="approve-ticket">
          Approve & send customer reply
        </button>
        <button class="secondary-action" type="button" data-action="simulate-timeout">
          Simulate timeout
        </button>
      </div>
      ${sla.overdue ? `
        <div class="draft-panel" role="status">
          <p class="eyebrow">Timeout transfer</p>
          <p>mark engineer timeout, then transfer to next eligible engineer when available.</p>
        </div>
      ` : ""}
    </section>
  `;
}

function renderEmptyTicketHtml(eligible) {
  return `
    <section class="current-ticket-card empty-ticket">
      <span class="material-symbols-outlined" aria-hidden="true">assignment</span>
      <div>
        <p class="ticket-kicker">Current Engineer Ticket</p>
        <h2>${eligible ? "Ready for next assignment" : "No assignable state"}</h2>
      </div>
      <p>${eligible ? "Assign the next mock ticket from the queue." : "Log in, stay inside shift, and finish any active ticket before assignment."}</p>
      <button class="primary-action" type="button" data-action="assign-next" ${eligible ? "" : "disabled"}>
        Assign next mock ticket
      </button>
    </section>
  `;
}

function enterWorkspace() {
  selectedEngineerId = getCandidateEngineer().id;
  writeStorage(ASSIGNMENT_AUTH_KEY, selectedEngineerId);
  addEvent("Engineer selected", `${selectedEngineerId} opened /assignment.`);
  renderWorkspace();
}

function assignNextTicket() {
  if (!canAssign() || queue.length === 0) return;
  const [next, ...rest] = queue;
  activeTicket = { ...next, assignedAt: Date.now(), engineerId: selectedEngineerId };
  queue = rest;
  saveActiveTicket();
  saveQueue();
  addEvent("Engineer Ticket assigned", `${activeTicket.id} assigned to ${selectedEngineerId}.`);
  renderWorkspace();
}

function approveTicket() {
  if (!activeTicket) return;
  addEvent("Customer reply sent", `${activeTicket.id} approved and closed by ${selectedEngineerId}.`);
  activeTicket = null;
  saveActiveTicket();
  renderWorkspace();
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
  renderWorkspace();
}

function resetDemo() {
  shift = { ...DEFAULT_SHIFT };
  activeTicket = null;
  queue = INITIAL_QUEUE.map((ticket) => ({ ...ticket }));
  events = [];
  writeStorage(ASSIGNMENT_SHIFT_KEY, shift);
  writeStorage(ASSIGNMENT_QUEUE_KEY, queue);
  writeStorage(ASSIGNMENT_ACTIVE_TICKET_KEY, activeTicket);
  writeStorage(ASSIGNMENT_EVENTS_KEY, events);
  renderWorkspace();
}

root.addEventListener("click", (event) => {
  const engineerButton = event.target.closest("[data-engineer-id]");
  if (engineerButton) {
    selectedEngineerCandidate = String(engineerButton.dataset.engineerId || DEMO_ENGINEERS[0].id);
    renderLogin();
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  const action = String(actionButton.dataset.action || "");
  if (action === "enter-workspace") enterWorkspace();
  if (action === "sign-out") {
    selectedEngineerId = "";
    localStorage.removeItem(ASSIGNMENT_AUTH_KEY);
    renderLogin();
  }
  if (action === "assign-next") assignNextTicket();
  if (action === "approve-ticket") approveTicket();
  if (action === "simulate-timeout") simulateTimeout();
  if (action === "reset-demo") resetDemo();
});

root.addEventListener("submit", (event) => {
  if (event.target.matches("[data-shift-form]")) {
    event.preventDefault();
    saveShift(event.target);
  }
});

window.setInterval(() => {
  if (getSelectedEngineer()) {
    renderWorkspace();
  }
}, 30000);

if (selectedEngineerId) {
  renderWorkspace();
} else {
  renderLogin();
}
