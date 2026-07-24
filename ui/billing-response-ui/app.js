const app = document.querySelector("#app");
const params = new URLSearchParams(window.location.search);
const token = (params.get("token") || "").trim();

const resultOptions = [
  { value: "completed", label: "已完成", hint: "处理已经完成；可填写内部处理细节供 AI 转写。" },
  { value: "refused", label: "拒绝处理", hint: "请填写内部拒绝原因；AI 会生成客户可读回复。" },
  { value: "customer_action_required", label: "需要客户操作", hint: "请填写客户下一步需要提供或完成什么；AI 会转写。" },
];

const state = {
  context: null,
  submitting: false,
  error: "",
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setApp(html) {
  app.innerHTML = html;
}

function renderFrame(content, modifier = "") {
  setApp(`
    <section class="response-card ${modifier}">
      ${content}
    </section>
  `);
}

function renderMessage(title, body, modifier = "") {
  renderFrame(`
    <p class="eyebrow">Billing response</p>
    <h1>${escapeHtml(title)}</h1>
    <p class="body-copy">${escapeHtml(body)}</p>
  `, modifier);
}

function renderLoading() {
  renderMessage("Submit handling result", "Loading secure response context...", "response-card--loading");
}

function renderSuccess(payload) {
  const notifyText = payload.customer_notified ? "AI will notify the customer." : "Customer notification was skipped.";
  renderFrame(`
    <p class="eyebrow">Submitted</p>
    <h1>处理结果已提交</h1>
    <p class="body-copy">${escapeHtml(notifyText)}</p>
    <dl class="summary-grid">
      <div><dt>Account Case ID</dt><dd>${escapeHtml(payload.account_case_id || payload.billing_ticket_id)}</dd></div>
      <div><dt>Automation status</dt><dd>${escapeHtml(payload.automation_status)}</dd></div>
    </dl>
    <p class="subtle-copy">This one-time response link cannot be submitted again.</p>
  `, "response-card--success");
}

function renderForm() {
  const context = state.context;
  if (!context) {
    renderMessage("Response link unavailable", "Unable to load billing context.", "response-card--error");
    return;
  }
  if (context.submitted) {
    renderFrame(`
      <p class="eyebrow">Already submitted</p>
      <h1>这个处理链接已提交</h1>
      <p class="body-copy">Account Case ID: ${escapeHtml(context.account_case_id || context.billing_ticket_id)}</p>
      <p class="subtle-copy">For audit safety, each response link can only be used once.</p>
    `, "response-card--success");
    return;
  }

  const options = resultOptions.map((option, index) => `
    <label class="option-row">
      <input type="radio" name="result" value="${option.value}" ${index === 0 ? "checked" : ""} />
      <span>
        <strong>${option.label}</strong>
        <small>${option.hint}</small>
      </span>
    </label>
  `).join("");

  renderFrame(`
    <div class="response-header">
      <p class="eyebrow">Billing response</p>
      <h1>Submit handling result</h1>
      <p class="body-copy">请提交内部处理结果和处理细节。AI 会读取这些信息，生成面向客户的回复；这里填写的说明不会被原样发送给客户。</p>
    </div>

    <dl class="summary-grid">
      <div><dt>Account Case ID</dt><dd data-field="billing_ticket_id">${escapeHtml(context.account_case_id || context.billing_ticket_id)}</dd></div>
      <div><dt>Customer email</dt><dd>${escapeHtml(context.customer_email || "Not available")}</dd></div>
      <div class="summary-grid__wide"><dt>Title</dt><dd>${escapeHtml(context.title || "Billing request")}</dd></div>
    </dl>

    <form id="response-form" class="response-form" novalidate>
      <fieldset>
        <legend>处理结果</legend>
        <div class="option-stack">${options}</div>
      </fieldset>

      <fieldset>
        <legend>是否通知客户</legend>
        <div class="segmented-control">
          <label><input type="radio" name="notify_customer" value="true" checked /> 是</label>
          <label><input type="radio" name="notify_customer" value="false" /> 否</label>
        </div>
      </fieldset>

      <label class="note-field" for="note">
        <span>内部处理细节 <small id="note-rule">已完成可选；其他结果必填。</small></span>
        <textarea id="note" name="note" rows="5" maxlength="4000" placeholder="例如：已通过邮件发送发票；拒绝原因；或客户还需要补充账单地址。AI 会转写成客户回复。"></textarea>
      </label>

      <p id="form-error" class="form-error" role="alert">${escapeHtml(state.error)}</p>
      <button class="primary-button" type="submit" ${state.submitting ? "disabled" : ""}>${state.submitting ? "Submitting..." : "Submit"}</button>
    </form>
  `);

  const form = document.querySelector("#response-form");
  form.addEventListener("change", updateNoteRule);
  form.addEventListener("submit", submitForm);
  updateNoteRule();
}

function currentResult(form) {
  return new FormData(form).get("result") || "completed";
}

function updateNoteRule() {
  const form = document.querySelector("#response-form");
  const note = document.querySelector("#note");
  const noteRule = document.querySelector("#note-rule");
  if (!form || !note || !noteRule) return;
  const requiresNote = currentResult(form) !== "completed";
  note.required = requiresNote;
  noteRule.textContent = requiresNote ? "此处理结果必须填写内部处理细节。" : "已完成可选；如填写，AI 会转写。";
}

async function submitForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const result = String(formData.get("result") || "completed");
  const note = String(formData.get("note") || "").trim();
  const notify_customer = formData.get("notify_customer") === "true";

  if (result !== "completed" && !note) {
    state.error = "拒绝处理或需要客户操作时，请填写内部处理细节。";
    renderForm();
    return;
  }

  state.submitting = true;
  state.error = "";
  renderForm();

  try {
    const response = await fetch("/api/billing-response/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, result, notify_customer, note }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Submit failed.");
    }
    renderSuccess(payload);
  } catch (error) {
    state.submitting = false;
    state.error = error.message || "Submit failed.";
    renderForm();
  }
}

async function loadContext() {
  if (!token) {
    renderMessage("Response link unavailable", "Missing or invalid response token.", "response-card--error");
    return;
  }
  renderLoading();
  try {
    const response = await fetch(`/api/billing-response?token=${encodeURIComponent(token)}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Response link unavailable.");
    }
    state.context = payload;
    renderForm();
  } catch (error) {
    renderMessage("Response link unavailable", error.message || "Unable to load billing context.", "response-card--error");
  }
}

loadContext();
