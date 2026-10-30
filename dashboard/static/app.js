"use strict";
/* SilverFile review console. Plain JavaScript, no build step. */

const $ = (sel, root = document) => root.querySelector(sel);
const main = $("#main");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null || n === "" || isNaN(n)) ? "—" : "$" + Number(n).toLocaleString("en-US");
const FILING = { single: "Single", mfj: "Married filing jointly", mfs: "Married filing separately", hoh: "Head of household", qss: "Qualifying surviving spouse" };
const AGENTS = {
  orchestrator: "Orchestrator", intake_agent: "Intake", intake: "Intake", extract: "Document extraction", extraction: "Document extraction",
  extraction_agent: "Document extraction", document_extraction: "Document extraction", eligibility_agent: "Eligibility & rules",
  eligibility: "Eligibility & rules", eligibility_rules: "Eligibility & rules", computation_agent: "Computation", computation: "Computation",
  review_agent: "Review & audit", review: "Review & audit", review_audit: "Review & audit", human_review: "Kushim reviewer",
  release_gate: "Release gate", __end__: "Finished",
};
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const agentName = (k) => AGENTS[k] || String(k || "").replace(/_/g, " ");

const ICON = {
  trace: '<svg viewBox="0 0 20 20"><circle cx="9" cy="9" r="5.5"/><path d="M13 13l4 4"/></svg>',
  back: '<svg viewBox="0 0 20 20"><path d="M12 4l-6 6 6 6"/></svg>',
  plus: '<svg viewBox="0 0 20 20"><path d="M10 4v12M4 10h12"/></svg>',
  search: '<svg viewBox="0 0 20 20"><circle cx="9" cy="9" r="5.5"/><path d="M13 13l4 4"/></svg>',
  flag: '<svg viewBox="0 0 20 20"><path d="M5 17V3m0 1h9l-2 3.5 2 3.5H5"/></svg>',
  check: '<svg viewBox="0 0 20 20"><path d="M4 10.5l4 4 8-9"/></svg>',
  chev: '<svg viewBox="0 0 12 12"><path d="M4 2l4 4-4 4"/></svg>',
  ledger: '<svg viewBox="0 0 48 48"><rect x="10" y="6" width="28" height="36" rx="3"/><path d="M16 15h16M16 21h16M16 27h10M16 33h7"/></svg>',
};

const SECTIONS = [
  { title: "Income", match: (n) => n <= 9 },
  { title: "Adjustments and adjusted gross income", match: (n) => n === 10 || n === 11 },
  { title: "Deductions and taxable income", match: (n) => n >= 12 && n <= 15 },
  { title: "Tax and credits", match: (n) => n >= 16 && n <= 24 },
  { title: "Payments", match: (n) => n >= 25 && n <= 33 },
  { title: "Refund or amount you owe", match: (n) => n >= 34 },
];
const TOTALS = new Set(["9", "11a", "15", "24", "33", "34", "37"]);
const lineNum = (l) => parseInt(l, 10);

/* ---------------- state ---------------- */
const app = {
  sessions: [], filter: "all", query: "", session: null, refreshedAt: null,
  trail: [], poll: null, meta: null, fixtures: null,
};

const reviewerInput = $("#reviewerName");
try { reviewerInput.value = localStorage.getItem("silverfile.reviewer") || ""; } catch (_) {}
reviewerInput.addEventListener("input", () => { try { localStorage.setItem("silverfile.reviewer", reviewerInput.value.trim()); } catch (_) {} });
const reviewer = () => reviewerInput.value.trim();

/* ---------------- api ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    throw new Error(detail || `The server answered ${res.status}.`);
  }
  return body;
}

function toast(msg, kind = "ok") {
  const t = document.createElement("div");
  t.className = "toast" + (kind === "error" ? " error" : "");
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

function busy(btn, on) { if (!btn) return; btn.classList.toggle("busy", on); btn.disabled = on; }

const ago = (iso) => {
  if (!iso) return "";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
};

/* ---------------- routing ---------------- */
function route() {
  clearInterval(app.poll);
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  document.querySelectorAll("[data-nav]").forEach((a) => a.removeAttribute("aria-current"));
  if (parts[0] === "s" && parts[1]) {
    openSession(parts[1], parts[2] || null, parts[3] || null);
  } else {
    $('[data-nav="queue"]').setAttribute("aria-current", "page");
    renderQueueShell();
    loadQueue();
    app.poll = setInterval(loadQueue, 8000);
  }
}
window.addEventListener("hashchange", route);

/* ---------------- queue ---------------- */
async function loadQueue() {
  try {
    app.sessions = await api("/api/sessions");
    app.refreshedAt = new Date();
    renderQueueBody();
  } catch (e) {
    $("#queueBody") && ($("#queueBody").innerHTML = `<div class="empty"><h2>The console can't reach the sessions</h2><p>${esc(e.message)}</p></div>`);
  }
  updateNavCount();
}

function updateNavCount() {
  const n = app.sessions.filter((s) => s.status === "needs_review").length;
  $("#navCount").textContent = n ? String(n) : "";
}

function renderQueueShell() {
  main.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Review queue</h1>
        <p>Every SilverFile session, newest first. Sessions waiting for a Kushim reviewer are held here until someone clears them.</p>
      </div>
      <button class="btn primary" data-action="new">${ICON.plus}New session</button>
    </div>
    <div class="filters" id="filters" role="group" aria-label="Filter by status"></div>
    <div class="toolbar">
      <label class="search"><span class="sr-only">Search sessions</span>${ICON.search}
        <input id="q" type="search" placeholder="Search taxpayer or session" value="${esc(app.query)}"></label>
      <span class="refreshed" id="refreshed"></span>
    </div>
    <div id="queueBody"><div class="table-wrap"><div style="padding:22px;display:grid;gap:14px">${'<div class="skeleton"></div>'.repeat(5)}</div></div></div>`;
  $("#q").addEventListener("input", (e) => { app.query = e.target.value; renderQueueBody(); });
}

function renderQueueBody() {
  const body = $("#queueBody");
  if (!body) return;
  const S = app.sessions;
  const counts = {
    all: S.length,
    needs_review: S.filter((s) => s.status === "needs_review").length,
    awaiting_taxpayer: S.filter((s) => s.status === "awaiting_taxpayer").length,
    draft_ready: S.filter((s) => s.status === "draft_ready").length,
    failed: S.filter((s) => s.status === "failed").length,
  };
  const labels = { all: "All sessions", needs_review: "Awaiting review", awaiting_taxpayer: "Awaiting taxpayer", draft_ready: "Draft ready", failed: "Stopped" };
  $("#filters").innerHTML = Object.keys(labels).map((k) =>
    `<button class="filter${k === "needs_review" && counts[k] ? " alert" : ""}" data-filter="${k}" aria-pressed="${app.filter === k}"><b>${counts[k]}</b>${labels[k]}</button>`).join("");
  $("#refreshed").textContent = app.refreshedAt ? `Updated ${app.refreshedAt.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}` : "";

  const q = app.query.trim().toLowerCase();
  const rows = S.filter((s) => app.filter === "all" || s.status === app.filter)
    .filter((s) => !q || `${s.taxpayer} ${s.id}`.toLowerCase().includes(q));

  if (!S.length) {
    body.innerHTML = `<div class="table-wrap"><div class="empty"><h2>No sessions yet</h2>
      <p>Start a session from one of the prepared taxpayer cases, or run <code>python run.py</code>. New sessions appear here automatically.</p>
      <button class="btn primary" data-action="new">${ICON.plus}New session</button></div></div>`;
    return;
  }
  if (!rows.length) {
    body.innerHTML = `<div class="table-wrap"><div class="empty"><h2>Nothing matches</h2><p>No session fits this filter${q ? " and search" : ""}.</p></div></div>`;
    return;
  }
  body.innerHTML = `<div class="table-wrap"><table class="queue">
    <thead><tr><th>Taxpayer</th><th>Status</th><th class="hide-sm">Filing status</th><th class="hide-sm">Open items</th>
    <th class="num">Refund or owed</th><th class="num hide-sm">Last activity</th></tr></thead>
    <tbody>${rows.map(rowHtml).join("")}</tbody></table></div>`;
}

function rowHtml(s) {
  let bottom = '<span class="dim">Not computed</span>';
  if (s.owed) bottom = `<span class="amount-neg">${money(s.owed)} owed</span>`;
  else if (s.refund) bottom = `<span class="amount-pos">${money(s.refund)} refund</span>`;
  else if (s.refund != null) bottom = '<span class="dim">No balance due</span>';
  const items = s.status === "needs_review" && s.open_escalations
    ? `<span class="esc-count">${ICON.flag}${plural(s.open_escalations, "item", "items")}</span>` : '<span class="dim">None</span>';
  return `<tr tabindex="0" data-open="${esc(s.id)}">
    <td><div class="who"><strong>${esc(s.taxpayer)}</strong><span>${esc(s.id)}</span></div></td>
    <td><span class="pill ${esc(s.status)}">${esc(s.status_label)}</span></td>
    <td class="hide-sm">${esc(FILING[s.filing_status] || "—")}</td>
    <td class="hide-sm">${items}</td>
    <td class="num">${bottom}</td>
    <td class="num hide-sm dim">${esc(ago(s.updated_at))}</td></tr>`;
}

/* ---------------- session ---------------- */
async function openSession(id, tab, line) {
  if (!app.session || app.session.id !== id) {
    main.innerHTML = `<a class="back" href="#/">${ICON.back}Review queue</a><div style="display:grid;gap:14px;max-width:520px">${'<div class="skeleton"></div>'.repeat(4)}</div>`;
    app.trail = [];
  }
  try {
    app.session = await api(`/api/sessions/${encodeURIComponent(id)}`);
  } catch (e) {
    main.innerHTML = `<a class="back" href="#/">${ICON.back}Review queue</a><div class="empty"><h2>Session not found</h2><p>${esc(e.message)}</p></div>`;
    return;
  }
  const s = app.session;
  const defaultTab = s.status === "needs_review" || s.status === "awaiting_taxpayer" ? "review" : "return";
  renderSession(tab || defaultTab, line);
  api("/api/sessions").then((all) => { app.sessions = all; updateNavCount(); }).catch(() => {});
}

function renderSession(tab, line) {
  const s = app.session;
  const owedOrRefund = s.owed ? `<div class="bottom-line owed"><span>Amount owed</span><strong>${money(s.owed)}</strong></div>`
    : s.refund ? `<div class="bottom-line refund"><span>Expected refund</span><strong>${money(s.refund)}</strong></div>`
    : s.refund != null ? `<div class="bottom-line"><span>Balance</span><strong>$0</strong></div>` : "";
  const pending = s.interrupt?.escalations?.length || 0;
  const tabs = [
    ["return", "Draft return", s.lines.length ? `<span class="badge quiet">${s.lines.length}</span>` : ""],
    ["review", "Review", pending ? `<span class="badge">${pending}</span>` : ""],
    ["audit", "Audit", ""],
    ["documents", "Documents", s.documents.length ? `<span class="badge quiet">${s.documents.length}</span>` : ""],
    ["activity", "Activity", ""],
  ];
  main.innerHTML = `
    <a class="back" href="#/">${ICON.back}Review queue</a>
    <div class="case-head">
      <div>
        <h1>${esc(s.taxpayer)}</h1>
        <div class="case-meta"><span class="pill ${esc(s.status)}">${esc(s.status_label)}</span>
          <span>${esc(FILING[s.filing_status] || "Filing status not set")}</span>
          <span>Session <code>${esc(s.id)}</code></span><span>Updated ${esc(ago(s.updated_at))}</span></div>
      </div>${owedOrRefund}
    </div>
    ${calloutHtml(s)}
    <div class="tabs" role="tablist">${tabs.map(([k, label, b]) =>
      `<button class="tab" role="tab" data-tab="${k}" aria-selected="${k === tab}">${label}${b}</button>`).join("")}</div>
    <section id="tabBody" role="tabpanel"></section>`;
  const body = $("#tabBody");
  ({ return: renderReturn, review: renderReview, audit: renderAudit, documents: renderDocuments, activity: renderActivity }[tab] || renderReturn)(body, line);
}

function calloutHtml(s) {
  if (s.status === "needs_review") {
    return `<div class="callout review"><p><strong>Held for a Kushim reviewer.</strong> ${plural(s.interrupt?.escalations?.length || 0, "item needs", "items need")} a decision before this return can continue.</p>
      <button class="btn primary" data-tab="review">Review items</button></div>`;
  }
  if (s.status === "awaiting_taxpayer") {
    return `<div class="callout taxpayer"><p><strong>Waiting on the taxpayer.</strong> Intake has asked a question and the session will continue once it's answered.</p>
      <button class="btn ghost" data-tab="review">See the question</button></div>`;
  }
  if (s.status === "draft_ready") {
    return `<div class="callout ready"><p><strong>Draft ready for the volunteer preparer.</strong> Review and audit approved every line, and the release gate passed. Nothing has been filed.</p></div>`;
  }
  if (s.status === "failed") {
    return `<div class="callout stopped"><p><strong>Session stopped by a reviewer.</strong> No draft will be released from this session.</p></div>`;
  }
  return "";
}

const setHash = (id, tab, line) => {
  const h = `#/s/${encodeURIComponent(id)}/${tab}${line ? "/" + encodeURIComponent(line) : ""}`;
  if (location.hash !== h) history.replaceState(null, "", h);
};

/* ---------- draft return + trace ---------- */
function renderReturn(body, selected) {
  const s = app.session;
  setHash(s.id, "return", selected);
  if (!s.lines.length) {
    body.innerHTML = `<div class="panel empty"><h2>No lines computed yet</h2>
      <p>Lines appear once Computation has run. ${s.status === "needs_review" ? "This session is paused for review first." : ""}</p></div>`;
    return;
  }
  const flagged = new Set((s.audit?.findings || []).flatMap((f) => [f.line, ...(f.lines || [])]).filter(Boolean).map(String));
  const sections = SECTIONS.map((sec) => {
    const lines = s.lines.filter((l) => sec.match(lineNum(l.line)));
    if (!lines.length) return "";
    return `<div class="section-head">${sec.title}</div>` + lines.map((l) => {
      const zero = !l.amount;
      return `<button class="line${TOTALS.has(l.line) ? " total-line" : ""}" data-line="${esc(l.line)}" aria-pressed="${l.line === selected}">
        <span class="ln">${esc(l.line)}</span>
        <span class="lbl"><span>${esc(l.label)}</span>${flagged.has(l.line) ? '<span class="flag" title="Audit finding"></span>' : ""}</span>
        <span class="amt${zero ? " zero" : ""}">${money(l.amount)}</span>
        <span class="src" aria-hidden="true">${ICON.trace}</span></button>`;
    }).join("");
  }).join("");
  body.innerHTML = `<div class="return-grid">
    <div class="form-sheet">
      <div class="form-title"><div class="form-no">Form<b>1040-SR</b></div>
        <h2>U.S. Income Tax Return for Seniors<small>Draft for review. Select any line to see where the amount came from.</small></h2>
        <div class="year">20<span>25</span></div></div>
      ${sections}
    </div>
    <aside class="trace" id="trace" aria-live="polite"></aside></div>`;
  renderTrace(selected);
}

function renderTrace(lineId, push = true) {
  const s = app.session;
  const box = $("#trace");
  if (!box) return;
  document.querySelectorAll(".line").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.line === lineId)));
  const l = s.lines.find((x) => x.line === lineId);
  if (!l) {
    box.innerHTML = `<div class="trace-empty">${ICON.ledger}<h3 style="font:400 20px var(--serif);margin:0 0 6px;color:var(--text)">Trace any amount</h3>
      <p style="margin:0">Choose a line on the return. You'll see the formula, every input, and the document or answer it came from. Inputs that are other lines can be followed back step by step.</p></div>`;
    return;
  }
  if (push) {
    const i = app.trail.indexOf(lineId);
    app.trail = i >= 0 ? app.trail.slice(0, i + 1) : [...app.trail, lineId].slice(-6);
  }
  setHash(s.id, "return", lineId);
  const lineIds = new Set(s.lines.map((x) => x.line));
  const kindLabel = { computed: "Computed", document: "From a document", user_input: "Taxpayer answer" }[l.kind] || (l.kind || "Recorded");
  // JSON objects put numeric keys like "26" before "25d", so put line inputs back in form order.
  const order = (k) => { const m = /^(\d+)([a-z]?)$/.exec(k); return m ? [0, +m[1], m[2]] : [1, 0, ""]; };
  const inputs = Object.entries(l.inputs || {}).sort(([a], [b]) => {
    const x = order(a), y = order(b);
    return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2]);
  });
  const inputRows = inputs.map(([k, v]) => {
    const isLine = lineIds.has(k);
    const val = typeof v === "number" ? (isLine || Math.abs(v) >= 100 ? money(v) : v.toLocaleString("en-US")) : (typeof v === "object" ? JSON.stringify(v) : String(v));
    const label = isLine ? `Line ${k}` : k.replace(/_/g, " ");
    return isLine
      ? `<button class="input-row" data-trace="${esc(k)}"><span class="k">${esc(label)}</span><span class="v">${esc(val)}</span></button>`
      : `<div class="input-row"><span class="k">${esc(label)}</span><span class="v">${esc(val)}</span></div>`;
  }).join("");
  let sourcing = "";
  if (l.kind === "document") {
    const d = s.documents.find((x) => x.doc_id === l.document);
    sourcing = `<p class="sourcing">Read from <b>${esc(d?.doc_type || "document")} ${esc(l.document || "")}</b>, field <b>${esc((l.field || "").replace(/_/g, " "))}</b>.
      <a href="#" data-tab="documents">View the document</a></p>`;
  } else if (l.kind === "user_input") {
    sourcing = `<p class="sourcing">Entered directly by <b>${esc(l.entered_by || "the taxpayer")}</b> during intake.</p>`;
  } else if (l.tool) {
    sourcing = `<p class="sourcing">Calculated by the deterministic tool <b>${esc(l.tool)}</b>. No language model did this arithmetic.</p>`;
  }
  const auditState = l.audit === "ok"
    ? `<span class="audit-ok">${ICON.check}Checked and approved by Review &amp; audit</span>`
    : l.audit ? `<span class="esc-count">${ICON.flag}Audit: ${esc(l.audit)}</span>` : '<span class="dim">Not yet audited</span>';
  const trail = app.trail.length > 1 ? `<div class="trail" aria-label="Trace path">${app.trail.map((t, i) =>
    (i ? ICON.chev : "") + `<button data-trace="${esc(t)}" data-notrail="1">Line ${esc(t)}</button>`).join("")}</div>` : "";

  box.innerHTML = `
    <div class="trace-head">
      <div class="kicker">Line ${esc(l.line)} <span class="kind ${esc(l.kind)}">${esc(kindLabel)}</span></div>
      <h3>${esc(l.label)}</h3>
      <div class="big">${money(l.amount)}</div>
    </div>
    <div class="trace-body">
      ${trail}
      ${l.formula ? `<div><h4>How it was calculated</h4><div class="formula">${esc(l.formula)}</div></div>` : ""}
      ${inputs.length ? `<div><h4>Inputs</h4><div class="inputs">${inputRows}</div></div>` : ""}
      ${sourcing}
      <div>${auditState}</div>
      ${l.created_at ? `<p class="sourcing">Recorded ${esc(new Date(l.created_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }))}</p>` : ""}
    </div>`;
}

/* ---------- review (HITL) ---------- */
function renderReview(body) {
  const s = app.session;
  setHash(s.id, "review");
  const p = s.interrupt;
  if (p && p.type === "intake_question") {
    body.innerHTML = `<form class="question" id="answerForm">
      <p class="who-asks">Intake is asking the taxpayer</p>
      <p class="q">${esc(p.text)}</p>
      <div class="field"><label for="answerText">Answer on the taxpayer's behalf</label>
        <input id="answerText" required autocomplete="off" placeholder="Type the taxpayer's answer"></div>
      <div><button class="btn primary" type="submit">Send answer</button></div>
      <p class="sourcing">Use this when the taxpayer answers by phone. The answer is recorded in the session as their reply.</p></form>`;
    $("#answerText").focus();
    $("#answerForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = e.submitter;
      busy(btn, true);
      try {
        app.session = await api(`/api/sessions/${encodeURIComponent(s.id)}/answer`, { method: "POST", body: JSON.stringify({ text: $("#answerText").value }) });
        toast("Answer sent. The session continued.");
        renderSession(app.session.status === "draft_ready" ? "return" : "review");
      } catch (err) { toast(err.message, "error"); busy(btn, false); }
    });
    return;
  }

  const pending = p?.type === "human_review" ? p.escalations || [] : [];
  const resolved = (s.escalations || []).filter((e) => e.resolved);
  const card = (e, open) => {
    const id = e.id || e.reason;
    const title = humanReason(e);
    return `<label class="esc${open ? "" : " resolved"}">
      ${open ? `<input type="checkbox" name="esc" value="${esc(id)}" checked>` : `<span style="color:var(--green)">${ICON.check.replace("<svg", '<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2"')}</span>`}
      <div><h3>${esc(title)}</h3>
        <div class="meta"><span>Raised by ${esc(agentName(e.raised_by))}</span>${e.rule_id ? `<span>Rule ${esc(e.rule_id)}</span>` : ""}${e.slot ? `<span>Question: ${esc(e.slot.replace(/_/g, " "))}</span>` : ""}</div>
        <p>${esc(e.detail || e.message || "")}</p>
        ${e.evidence ? `<blockquote>${esc(typeof e.evidence === "string" ? e.evidence : JSON.stringify(e.evidence))}</blockquote>` : ""}
        ${!open ? `<div class="resolution">Resolved by ${esc(e.resolved_by || "a reviewer")}${e.resolution_note || e.note ? `: ${esc(e.resolution_note || e.note)}` : ""}</div>` : ""}
      </div></label>`;
  };

  if (!pending.length) {
    body.innerHTML = `<div class="panel empty"><h2>Nothing waiting for review</h2>
      <p>${resolved.length ? "Every escalation on this session has been resolved." : "No agent has escalated anything on this session."}</p></div>
      ${resolved.length ? `<div class="esc-list" style="margin-top:16px">${resolved.map((e) => card(e, false)).join("")}</div>` : ""}`;
    return;
  }

  body.innerHTML = `<div class="review-grid">
    <div class="esc-list">${pending.map((e) => card(e, true)).join("")}
      ${resolved.length ? `<h3 style="font:400 19px var(--serif);margin:18px 0 0">Resolved earlier</h3>${resolved.map((e) => card(e, false)).join("")}` : ""}</div>
    <form class="decision" id="decision">
      <h3>Your decision</h3>
      <p>Resolving lets the session continue. Eligibility is re-checked after every review. Stopping ends the session with no draft.</p>
      <div class="field"><label for="decReviewer">Reviewer</label><input id="decReviewer" required value="${esc(reviewer())}" placeholder="Your name"></div>
      <div class="field"><label for="decNote">Note for the record</label>
        <textarea id="decNote" placeholder="What you checked, and why it's fine to continue"></textarea>
        <small>Saved with each resolved item and shown to the volunteer preparer.</small></div>
      <div class="actions">
        <button class="btn primary" type="submit" value="selected">Resolve selected</button>
        <div class="split"><button class="btn ghost" type="submit" value="all">${pending.length > 1 ? `Resolve all ${pending.length}` : "Resolve all"}</button>
          <button class="btn danger" type="submit" value="stop">Stop session</button></div>
      </div>
      <p class="form-error" id="decError" role="alert"></p>
    </form></div>`;

  $("#decision").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    const mode = btn.value;
    const name = $("#decReviewer").value.trim();
    const note = $("#decNote").value.trim();
    const ids = [...document.querySelectorAll('input[name="esc"]:checked')].map((i) => i.value);
    $("#decError").textContent = "";
    if (!name) { $("#decError").textContent = "Add your name so the decision is attributed."; return; }
    if (mode === "selected" && !ids.length) { $("#decError").textContent = "Select at least one item, or use Resolve all."; return; }
    if (mode === "stop" && !confirm("Stop this session? No draft will be released from it.")) return;
    reviewerInput.value = name; reviewerInput.dispatchEvent(new Event("input"));
    const payload = { reviewer: name, note, action: mode === "stop" ? "stop" : "proceed", escalation_ids: mode === "selected" ? ids : null };
    busy(btn, true);
    try {
      app.session = await api(`/api/sessions/${encodeURIComponent(app.session.id)}/resolve`, { method: "POST", body: JSON.stringify(payload) });
      const st = app.session.status;
      toast(mode === "stop" ? "Session stopped." : st === "draft_ready" ? "Resolved. The draft is ready." : st === "needs_review" ? "Resolved. Another item now needs review." : "Resolved. The session continued.");
      renderSession(st === "draft_ready" ? "return" : "review");
    } catch (err) { $("#decError").textContent = err.message; busy(btn, false); }
  });
}

function humanReason(e) {
  const r = String(e.reason || e.code || "Needs review");
  const base = r.split(":")[0];
  const map = {
    advice_request: "Taxpayer asked for tax advice", out_of_scope: "Outside SilverFile's scope", filing_status_ambiguous: "Filing status is unclear",
    financial_exploitation: "Possible financial exploitation", exploitation: "Possible financial exploitation", conflict: "Taxpayer answer conflicts with a document",
    loop_guard: "Agents were passing work back and forth", max_hops: "Session ran too many steps", reextract_requested: "A document needs re-uploading",
    unsure_answer: "Taxpayer wasn't sure of an answer",
    undetermined_taxable_1099r: "1099-R taxable amount not determined", missing_info: "Information is missing",
    exploitation_risk: "Possible financial exploitation",
  };
  return map[base] || base.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/* ---------- audit ---------- */
function renderAudit(body) {
  const s = app.session;
  setHash(s.id, "audit");
  const a = s.audit;
  const gate = s.release_gate;
  if (!a) {
    body.innerHTML = `<div class="panel empty"><h2>Not audited yet</h2><p>Review &amp; audit runs after Computation fills in the return.</p></div>`;
    return;
  }
  const verdict = { approve: "Approved", revise: "Sent back for revision", escalate: "Escalated to a person" }[a.verdict] || a.verdict;
  const findings = a.findings || [];
  body.innerHTML = `<div class="panel"><h3>Review &amp; audit</h3><dl class="facts">
      <div><dt>Verdict</dt><dd>${esc(verdict)}</dd></div>
      <div><dt>Checks run</dt><dd>${esc(a.checks_run ?? "—")}</dd></div>
      <div><dt>Review round</dt><dd>${esc(a.round ?? "—")} of 3</dd></div>
      <div><dt>Release gate</dt><dd>${gate ? (gate.passed ? "Passed" : "Blocked") : "Not reached"}</dd></div></dl></div>
    <div class="panel"><h3>Findings</h3>${findings.length ? findings.map((f) => `<div class="finding">
        <strong>${esc(f.message || f.title || f.check || f.key || "Finding")}</strong>
        <div class="meta">${[f.severity && "Severity " + f.severity, f.line && "Line " + f.line, f.route_to && "Sent to " + agentName(f.route_to)].filter(Boolean).map(esc).join(", ")}</div></div>`).join("")
      : '<p class="sourcing">No findings. Every line matched its provenance record and the totals recomputed exactly.</p>'}</div>
    ${gate && gate.violations?.length ? `<div class="panel"><h3>Release gate</h3>${gate.violations.map((v) => `<div class="finding">${esc(typeof v === "string" ? v : JSON.stringify(v))}</div>`).join("")}</div>` : ""}`;
}

/* ---------- documents ---------- */
function renderDocuments(body) {
  const s = app.session;
  setHash(s.id, "documents");
  if (!s.documents.length) {
    body.innerHTML = `<div class="panel empty"><h2>No documents</h2><p>This session has no extracted documents.</p></div>`;
    return;
  }
  const fmt = (k, v) => /tin|ssn/i.test(k) && typeof v === "string" ? "•••-••-" + v.slice(-4)
    : typeof v === "number" ? money(v) : typeof v === "boolean" ? (v ? "Yes" : "No") : (v ?? "—");
  body.innerHTML = `<div class="docs">${s.documents.map((d) => `<article class="doc">
      <header><strong>${esc(d.doc_type || "Document")}</strong><span>${esc(d.doc_id || "")}</span></header>
      <dl>${Object.entries(d.fields || {}).filter(([, v]) => typeof v !== "object" || v === null).map(([k, v]) =>
        `<dt>${esc(k.replace(/^box_/, "Box ").replace(/_/g, " "))}</dt><dd>${esc(fmt(k, v))}</dd>`).join("")}</dl></article>`).join("")}</div>`;
}

/* ---------- activity ---------- */
function renderActivity(body) {
  const s = app.session;
  setHash(s.id, "activity");
  const items = s.timeline.map((t, i) => {
    if (t.kind === "human") return `<li class="human" data-step="${i + 1}"><div class="t">Kushim reviewer ${t.action === "stop" ? "stopped the session" : "resolved " + plural((t.resolved || []).length || 1, "item", "items")}</div><div class="s">${esc(ago(t.at))}</div></li>`;
    if (t.kind === "gate") return `<li class="gate" data-step="${i + 1}"><div class="t">Release gate ${t.passed ? "passed" : "blocked the draft"}</div></li>`;
    return `<li data-step="${i + 1}"><div class="t">Orchestrator handed off to ${esc(agentName(t.to))}</div><div class="s">${esc(t.at ? new Date(t.at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }) : "")}</div></li>`;
  }).join("");
  body.innerHTML = `<div class="panel"><h3>How this session moved through SilverFile</h3>
    ${items ? `<ol class="timeline">${items}</ol>` : '<p class="sourcing">No activity recorded yet.</p>'}</div>`;
}

/* ---------------- new session dialog ---------------- */
const dlg = $("#newDialog");
const CASE_TITLES = {
  "clean_single.json": "Clean return, single", "clean_mfj.json": "Clean return, joint",
  "hitl_1099r_box2b.json": "1099-R, taxable amount unknown", "hitl_advice.json": "Asks for tax advice",
  "hitl_exploitation.json": "Signs of exploitation", "hitl_qualified_dividends.json": "Qualified dividends",
  "hitl_spouse_died.json": "Spouse died this year", "hitl_unsure_birth_date.json": "Unsure of birth date",
};
async function openNew() {
  $("#newError").textContent = "";
  if (!app.fixtures) {
    try { app.fixtures = await api("/api/fixtures"); } catch (e) { toast(e.message, "error"); return; }
  }
  const f = app.fixtures;
  $("#scenarioGrid").innerHTML = f.scenarios.map((sc, i) => `<div class="choice">
      <input type="radio" name="scenario" id="sc${i}" value="${esc(sc.file)}" ${sc.file === "clean_single.json" ? "checked" : ""}>
      <label for="sc${i}">${esc(CASE_TITLES[sc.file] || sc.title)}<small>${esc(FILING[sc.filing_status] || "")}</small>${sc.hitl ? '<small class="tag-hitl">Triggers review</small>' : ""}</label></div>`).join("")
    + `<div class="choice"><input type="radio" name="scenario" id="scLive" value=""><label for="scLive">Live interview<small>Intake asks every question</small></label></div>`;
  $("#docGrid").innerHTML = f.documents.map((d, i) => `<div class="choice">
      <input type="checkbox" name="doc" id="dc${i}" value="${esc(d)}"><label for="dc${i}">${esc(d)}</label></div>`).join("");
  $("#fSession").value = `session-${new Date().toISOString().slice(5, 16).replace(/[-T:]/g, "")}`;
  dlg.showModal();
  $("#fSession").select();
}
$("#newCancel").addEventListener("click", () => dlg.close());
$("#newForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#newSubmit");
  const body = {
    session_id: $("#fSession").value.trim(),
    scenario: document.querySelector('input[name="scenario"]:checked')?.value || null,
    documents: [...document.querySelectorAll('input[name="doc"]:checked')].map((i) => i.value),
    ssn: $("#fSsn").value.trim() || null,
    spouse_ssn: $("#fSpouseSsn").value.trim() || null,
  };
  busy(btn, true);
  try {
    const s = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    dlg.close();
    toast(`Session started: ${s.status_label}.`);
    app.session = null;
    location.hash = `#/s/${encodeURIComponent(s.id)}`;
  } catch (err) { $("#newError").textContent = err.message; }
  busy(btn, false);
});

/* ---------------- delegated clicks ---------------- */
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-action],[data-open],[data-filter],[data-tab],[data-line],[data-trace]");
  if (!t) return;
  if (t.dataset.action === "new") { openNew(); return; }
  if (t.dataset.open) { location.hash = `#/s/${encodeURIComponent(t.dataset.open)}`; return; }
  if (t.dataset.filter) { app.filter = t.dataset.filter; renderQueueBody(); return; }
  if (t.dataset.tab) { e.preventDefault(); renderSession(t.dataset.tab); return; }
  if (t.dataset.line) { app.trail = []; renderTrace(t.dataset.line); return; }
  if (t.dataset.trace) {
    const id = t.dataset.trace;
    renderTrace(id, !t.dataset.notrail || true);
    const row = document.querySelector(`.line[data-line="${CSS.escape(id)}"]`);
    if (row) { row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash"); row.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  }
});
document.addEventListener("keydown", (e) => {
  const row = e.target.closest?.("tr[data-open]");
  if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); row.click(); }
  if (e.key === "Escape" && location.hash.startsWith("#/s/") && !dlg.open && !/input|textarea/i.test(e.target.tagName)) location.hash = "#/";
});
$("#navNew").addEventListener("click", openNew);

api("/api/meta").then((m) => {
  app.meta = m;
  $("#modeNote").textContent = m.offline ? "Offline mode: W-2 values come from saved answer keys, not from reading the image." : "Live mode: W-2 images are read by the vision model.";
}).catch(() => {});
route();
