const root = document.getElementById("workspace-setup-root");
const token = new URLSearchParams(window.location.search).get("token") || "";
let invitation = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try {
      reason = (await response.json())?.detail || reason;
    } catch {
      // Keep the HTTP fallback.
    }
    throw new Error(reason);
  }
  return response.json();
}

function shell(content) {
  return `
    <section class="setup-page">
      <header class="setup-header"><a href="/workspace/" class="setup-brand"><span class="material-symbols-outlined" aria-hidden="true">workspaces</span><strong>Workspace</strong></a></header>
      <div class="setup-main">${content}</div>
      <footer class="setup-footer">SupportPortal · Secure account setup</footer>
    </section>`;
}

function renderLoading() {
  root.innerHTML = shell(`<section class="setup-state"><span class="material-symbols-outlined setup-state-icon">progress_activity</span><h1>Checking invitation</h1></section>`);
}

function renderUnavailable() {
  root.innerHTML = shell(`
    <section class="setup-state"><span class="material-symbols-outlined setup-state-icon">link_off</span><h1>Invitation unavailable</h1><p>This setup link is invalid, expired, or has already been used.</p></section>`);
}

function renderForm() {
  root.innerHTML = shell(`
    <section class="setup-content">
      <header><p class="setup-eyebrow">${escapeHtml(invitation.role.toUpperCase())} INVITATION</p><h1>Set up your account</h1><p>${escapeHtml(invitation.email)}</p></header>
      <form class="setup-form" data-setup-form>
        <label><span>Email</span><input name="email" type="email" value="${escapeHtml(invitation.email)}" autocomplete="username" readonly aria-readonly="true" /></label>
        <label><span>Display name</span><input name="display_name" autocomplete="name" required maxlength="160" /></label>
        <label><span>Password</span><input name="password" type="password" autocomplete="new-password" required minlength="10" maxlength="512" /></label>
        <label><span>Confirm password</span><input name="confirm_password" type="password" autocomplete="new-password" required minlength="10" maxlength="512" /></label>
        <p class="setup-error" data-setup-error role="alert"></p>
        <button type="submit">Create Account<span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></button>
      </form>
    </section>`);
}

function renderSuccess(account) {
  const destination = account.role === "admin" ? "/workspace/admin/" : "/workspace/";
  root.innerHTML = shell(`
    <section class="setup-state setup-success"><span class="material-symbols-outlined setup-state-icon">check_circle</span><h1>Account ready</h1><p>${escapeHtml(account.display_name)}, your Workspace account has been created.</p><a href="${destination}">Continue to sign in<span class="material-symbols-outlined" aria-hidden="true">login</span></a></section>`);
}

root.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (!form.matches("[data-setup-form]")) return;
  const data = new FormData(form);
  const error = form.querySelector("[data-setup-error]");
  const submit = form.querySelector('button[type="submit"]');
  error.textContent = "";
  submit.disabled = true;
  try {
    const payload = await fetchJson("/api/workspace/invitations/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token,
        display_name: String(data.get("display_name") || "").trim(),
        password: String(data.get("password") || ""),
        confirm_password: String(data.get("confirm_password") || ""),
      }),
    });
    renderSuccess(payload.account);
  } catch (setupError) {
    error.textContent = setupError.message;
    submit.disabled = false;
  }
});

async function initialize() {
  if (!token) {
    renderUnavailable();
    return;
  }
  renderLoading();
  try {
    const payload = await fetchJson(`/api/workspace/invitations/${encodeURIComponent(token)}`);
    invitation = payload.invitation;
    renderForm();
  } catch {
    renderUnavailable();
  }
}

initialize();
