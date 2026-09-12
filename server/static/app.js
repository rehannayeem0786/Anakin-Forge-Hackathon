/* ARGUS — UI controller.
   Consumes the agent's live SSE trace and renders the run as it happens. */

const $ = (id) => document.getElementById(id);

const state = {
  runId: null,
  es: null,
  autoScroll: true,
  phase: null,
  events: 0,
  evidence: [],
  evidenceFilter: "all",
  surface: [],
  surfaceSeen: new Set(),
  running: false,
  expanded: false,       // "show all candidates" on the decision panel
  lastDecision: null,
  hasAnakinKey: false,   // set from /api/config; the approval card wording depends on it
};

/* ------------------------------------------------------------------ utils */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

function money(amount, currency) {
  if (amount === null || amount === undefined) return "—";
  const cur = (currency || "USD").toUpperCase();
  const sym = { USD: "$", INR: "₹", EUR: "€", GBP: "£", JPY: "¥" }[cur] || "";
  const zeroDecimal = ["INR", "JPY", "KRW", "IDR", "VND"].includes(cur);
  const n = Number(amount).toLocaleString(undefined, {
    minimumFractionDigits: zeroDecimal ? 0 : 2,
    maximumFractionDigits: zeroDecimal ? 0 : 2,
  });
  return `${sym}${n} ${cur}`;
}

function toast(msg, ms = 3200) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

/* ----------------------------------------------------------------- config */
async function loadConfig() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    const rt = cfg.runtime;
    state.hasAnakinKey = !!rt.has_anakin_key;

    const modePill = $("pill-mode");
    const modeLabel = {
      "zero-touch": "Zero Touch — no key needed",
      "keyed": "Keyed account",
      "offline": "Offline fixtures",
    }[rt.mode] || rt.mode;
    modePill.className = "pill " + (rt.mode === "offline" ? "warn" : "ok");
    modePill.innerHTML = `<i class="dot"></i><span>${esc(modeLabel)}</span>`;

    const a = $("pill-anakin");
    a.className = "pill " + (rt.has_anakin_key ? "ok" : "");
    a.textContent = rt.has_anakin_key ? "Anakin — keyed" : "Anakin — keyless tier";

    const l = $("pill-llm");
    l.className = "pill " + (rt.has_llm_key ? "ok" : "warn");
    l.textContent = rt.has_llm_key ? `Reasoner — ${rt.llm_model}` : "Reasoner — deterministic";

    const presets = $("presets");
    presets.innerHTML = "";
    (cfg.presets || []).forEach((p) => {
      const b = document.createElement("button");
      b.className = "chip";
      b.textContent = p.label;
      b.title = p.goal;
      b.onclick = () => {
        $("goal").value = p.goal;
        document.querySelectorAll(".presets .chip").forEach((c) => c.classList.remove("active"));
        b.classList.add("active");
        $("goal").focus();
      };
      presets.appendChild(b);
    });
  } catch (e) {
    toast("Could not load config — is the server running?");
  }
}

/* ------------------------------------------------------------------- run */
async function startRun() {
  const goal = $("goal").value.trim();
  if (goal.length < 3) { toast("Give ARGUS a goal first."); return; }
  if (state.running) { toast("A run is already in flight."); return; }

  resetUI();
  state.running = true;
  $("run").disabled = true;
  $("run").textContent = "Running…";

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { run_id } = await res.json();
    state.runId = run_id;
    openStream(run_id);
  } catch (e) {
    toast("Failed to start the run: " + e.message);
    finishRun();
  }
}

function openStream(runId) {
  if (state.es) state.es.close();
  const es = new EventSource(`/api/stream/${runId}`);
  state.es = es;

  es.onmessage = (m) => {
    if (!m.data) return;
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    handleEvent(ev);
  };

  es.addEventListener("end", () => {
    es.close();
    finishRun();
    refreshFinal(runId);
  });

  es.onerror = () => {
    /* EventSource auto-reconnects; the server replays history on reconnect. */
  };
}

function finishRun() {
  state.running = false;
  $("run").disabled = false;
  $("run").textContent = "Run agent";
  setPhaseActive(null);
}

async function refreshFinal(runId) {
  try {
    const s = await (await fetch(`/api/state/${runId}`)).json();
    if (s.decision) renderDecision(s.decision);
    if (s.action) renderAction(s.action);
    if (s.dossier && s.dossier.output) renderDossier(s.dossier.output);
    if (s.credits) renderCredits(s.credits);
    if (s.error) toast("Run ended with an error: " + s.error, 6000);
  } catch { /* non-fatal */ }
}

/* --------------------------------------------------------------- events */
function handleEvent(ev) {
  state.events++;
  $("trace-count").textContent = `${state.events} event${state.events === 1 ? "" : "s"}`;

  if (ev.kind === "phase") setPhaseActive(ev.phase);
  if (ev.kind === "decision") renderDecision(ev.data);
  if (ev.kind === "evidence") pushEvidence(ev.data);
  if (ev.kind === "approval") renderApproval(ev.data);
  if (ev.kind === "action" && ev.data && ev.data.action) renderAction(ev.data.action);
  if (ev.kind === "artifact") renderDossier(ev.data);
  if (ev.kind === "credits") renderCredits(ev.data);
  if (ev.kind === "run_end") setPhaseActive(null);
  if (ev.kind === "tool_result" || ev.kind === "tool_call") recordSurface(ev);

  appendTrace(ev);
}

function appendTrace(ev) {
  const trace = $("trace");
  const emptyEl = $("trace-empty");
  if (emptyEl) emptyEl.remove();

  const el = document.createElement("div");
  el.className = "ev";
  el.dataset.kind = ev.kind;
  el.dataset.phase = ev.phase || "read";
  el.dataset.level = ev.level || "info";

  let extra = "";
  const d = ev.data || {};
  const payload =
    d.result ? { endpoint: d.result.endpoint, ...d.result }
    : (d.actions || d.hits || d.preview || d.criteria || d.adjustments || d.ranked)
      ? d
      : null;
  if (payload) {
    extra = `<details class="ev-extra"><summary>payload</summary><pre>${esc(JSON.stringify(payload, null, 2))}</pre></details>`;
  }

  el.innerHTML = `
    <div class="ev-meta">
      <span class="ev-clock">${esc(ev.clock || "")}</span>
      <span class="ev-kind">${esc(ev.kind)}</span>
    </div>
    <div class="ev-body">
      <div class="ev-title">${esc(ev.title || "")}</div>
      ${ev.detail ? `<div class="ev-detail">${esc(ev.detail)}</div>` : ""}
      ${extra}
    </div>`;

  trace.appendChild(el);
  if (state.autoScroll) trace.scrollTop = trace.scrollHeight;
}

function setPhaseActive(phase) {
  document.querySelectorAll(".phase").forEach((p) => {
    const name = p.dataset.phase;
    p.classList.remove("active");
    if (phase === null) { p.classList.add("done"); return; }
    const order = ["read", "reason", "act"];
    if (order.indexOf(name) < order.indexOf(phase)) { p.classList.add("done"); p.classList.remove("active"); }
    else if (name === phase) { p.classList.add("active"); p.classList.remove("done"); }
    else { p.classList.remove("done", "active"); }
  });
}

/* -------------------------------------------------------------- decision */
// A live run can score 40-50 candidates. Rendering every one of them buries the
// decision under a wall of cards, so the panel shows the ones that matter and
// offers the rest on demand. Nothing is hidden from the audit trail — the
// dossier and the JSON still carry every candidate.
const TOP_QUALIFIED = 5;
const TOP_EXCLUDED = 3;

// Flags that mean "this breaks a constraint you set", not "this scored lower".
const EXCLUDING_FLAGS = ["over budget", "wrong panel type", "wrong size", "wrong resolution"];
const isExcluded = (o) =>
  (o.flags || []).some((f) => EXCLUDING_FLAGS.some((x) => f.includes(x)));

function renderDecision(d) {
  if (!d) return;
  state.lastDecision = d;
  renderDecisionBody();
}

function renderDecisionBody() {
  const d = state.lastDecision;
  if (!d) return;
  $("decision-state").textContent = d.recommendation_title ? "ready" : "no result";
  const conf = Math.round((d.confidence || 0) * 100);
  const cur = d.currency || "USD";

  const crit = (d.criteria || []).map((c) => `
    <div class="crit">
      <span class="crit-name">${esc(c.label || c.name)}</span>
      <span class="crit-weight">${Math.round((c.weight || 0) * 100)}%</span>
      <div class="crit-bar"><i style="width:${Math.round((c.weight || 0) * 100)}%"></i></div>
    </div>`).join("");

  const rankCard = (o, excluded) => {
    const price = (o.price_local !== null && o.price_local !== undefined &&
                   o.currency_local && o.currency_local.toUpperCase() !== cur.toUpperCase())
      ? `${money(o.price, cur)} <span class="muted">(was ${money(o.price_local, o.currency_local)})</span>`
      : money(o.price, cur);
    const flags = (o.flags || []).map((f) => `<span class="flag">${esc(f)}</span>`).join("");
    return `
      <div class="rank ${o.rank === 1 && !excluded ? "win" : ""} ${excluded ? "excluded" : ""}">
        <div class="rank-top">
          <span class="rank-n">${excluded ? "✕" : "#" + o.rank}</span>
          <span class="rank-title">${esc(o.title)}</span>
          <span class="rank-price">${price}</span>
        </div>
        <div class="rank-score">
          <span class="rank-score-bar"><i style="width:${Math.round((o.total_score || 0) * 100)}%"></i></span>
          <span class="rank-score-num">${(o.total_score || 0).toFixed(3)}</span>
        </div>
        ${o.rating ? `<div class="rank-score-num muted" style="margin-top:4px">★ ${o.rating} · ${o.reviews || 0} reviews · ${esc(o.source || "")}</div>` : ""}
        ${flags ? `<div class="flags">${flags}</div>` : ""}
        ${o.rationale ? `<details class="ev-extra"><summary>why this rank</summary><div class="why">${esc(o.rationale)}</div></details>` : ""}
      </div>`;
  };

  const ranked = d.ranked || [];
  const qualified = ranked.filter((o) => !isExcluded(o));
  const excluded = ranked.filter(isExcluded);

  const shownQ = state.expanded ? qualified : qualified.slice(0, TOP_QUALIFIED);
  const shownX = state.expanded ? excluded : excluded.slice(0, TOP_EXCLUDED);
  const hidden = (qualified.length - shownQ.length) + (excluded.length - shownX.length);

  const toggle = (hidden > 0 || state.expanded)
    ? `<button class="btn-ghost ranked-toggle" id="ranked-toggle">
         ${state.expanded ? "Show fewer" : `Show all ${ranked.length} candidates`}
       </button>`
    : "";

  const rankedHtml = ranked.length ? `
    <div class="section-label">
      Ranked candidates
      <span class="tally">${qualified.length} clear your constraints${
        excluded.length ? ` · ${excluded.length} excluded` : ""}</span>
    </div>
    <div class="ranked">${shownQ.map((o) => rankCard(o, false)).join("")}</div>
    ${excluded.length ? `
      <div class="section-label excluded-label">Excluded — breaks a constraint you set</div>
      <p class="excluded-note">These scored well on merit but break a constraint you set —
        a budget, or a stated requirement like panel type or screen size — so they rank below
        every option that respects it rather than winning on merit. Still shown, never
        silently dropped.</p>
      <div class="ranked excluded-list">${shownX.map((o) => rankCard(o, true)).join("")}</div>` : ""}
    ${toggle}` : "";

  const critique = (d.critique_adjustments || []).length || d.critique ? `
    <div class="critique">
      <h4>Self-critique</h4>
      ${d.critique ? `<div class="why" style="margin-bottom:7px">${esc(d.critique)}</div>` : ""}
      <ul>${(d.critique_adjustments || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
    </div>` : "";

  $("decision-body").innerHTML = `
    <div class="rec">
      <div class="rec-headline">${esc(d.headline || d.recommendation_title || "—")}</div>
      <div class="rec-meta">
        <div class="gauge">
          <span class="gauge-track"><span class="gauge-fill" style="width:${conf}%"></span></span>
          <span class="gauge-label">${conf}%</span>
        </div>
        <span class="muted">reasoner: ${esc(d.reasoner || "—")}</span>
        ${ranked.length ? `<span class="muted">· ${ranked.length} candidates scored</span>` : ""}
      </div>
    </div>
    ${crit ? `<div class="section-label">Weighted criteria</div><div class="criteria">${crit}</div>` : ""}
    ${rankedHtml}
    ${critique}`;

  const t = $("ranked-toggle");
  if (t) {
    t.onclick = () => { state.expanded = !state.expanded; renderDecisionBody(); };
  }
}

/* ---------------------------------------------------------------- action */
function renderApproval(d) {
  $("action-state").textContent = "awaiting you";
  $("action-body").innerHTML = `
    <div class="act-box pending">
      <div class="act-title">Approval required</div>
      <div class="act-detail">
        ARGUS wants to prepare this ${(state.hasAnakinKey)
          ? "in Anakin's stealth cloud browser"
          : "in a local headless browser (no key needed)"}:
        <br /><span class="muted" style="font-family:var(--mono);font-size:11px;word-break:break-all">${esc(d.target || "—")}</span>
        <br /><br />Nothing is purchased. It locates the order fields and <b>stops one click before submit</b>.
      </div>
      <div class="act-btns">
        <button class="btn-approve" id="btn-approve">Approve — prepare it</button>
        <button class="btn-decline" id="btn-decline">Decline — just give me the dossier</button>
      </div>
      <div class="act-note">This gate exists because an agent that can spend your money without asking is not shippable.</div>
    </div>`;
  $("btn-approve").onclick = () => sendApproval(true);
  $("btn-decline").onclick = () => sendApproval(false);
}

async function sendApproval(approved) {
  if (!state.runId) return;
  $("action-body").querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    await fetch(`/api/approve/${state.runId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    toast(approved ? "Approved — preparing the action." : "Declined — writing the dossier instead.");
  } catch (e) {
    toast("Approval failed: " + e.message);
  }
}

function renderAction(a) {
  if (!a) return;
  const cls = a.status === "executed" ? "done"
    : a.status === "failed" ? "failed"
    : (a.status === "proposed" || a.status === "awaiting_approval") ? "pending" : "";
  $("action-state").textContent = a.status || "—";

  const steps = (a.output && a.output.transcript) || [];
  const transcript = steps.length ? `
    <div class="transcript">
      ${steps.map((s) => `<div class="tstep"><b>${esc(s.step)}</b><span>${esc(s.detail)}</span></div>`).join("")}
    </div>` : "";

  const mode = a.output && a.output.mode ? a.output.mode : "—";
  const note = a.output && a.output.how_to_go_live
    ? `<div class="act-note">To run this live: ${esc(a.output.how_to_go_live)}</div>` : "";

  $("action-body").innerHTML = `
    <div class="act-box ${cls}">
      <div class="act-title">${esc(a.title)}</div>
      <div class="act-detail">${esc(a.detail || "")}</div>
      <div class="act-note">status <b>${esc(a.status)}</b> · mode <b>${esc(mode)}</b>${a.reversible === false ? " · <b>irreversible</b>" : ""}</div>
      ${transcript}
      ${note}
    </div>`;
}

function renderDossier(out) {
  if (!out) return;
  const card = $("action-body");
  const link = `<a class="dossier-link" href="#" id="dossier-link">📄 Open decision dossier</a>`;
  if (!card.querySelector("#dossier-link")) {
    const box = card.querySelector(".act-box") || card;
    box.insertAdjacentHTML("beforeend", link);
  }
  const el = $("dossier-link");
  if (el) {
    el.onclick = async (e) => {
      e.preventDefault();
      try {
        const res = await (await fetch(`/api/artifact/${state.runId}`)).json();
        if (res.markdown) showDossier(res);
      } catch { toast("Dossier not ready yet."); }
    };
  }
}

function showDossier(res) {
  const w = window.open("", "_blank");
  if (!w) { toast("Allow pop-ups to view the dossier."); return; }
  w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8">
    <title>ARGUS decision dossier</title>
    <style>body{font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
      max-width:860px;margin:44px auto;padding:0 24px;color:#0f172a}
      h1{font-size:27px;border-bottom:1px solid #e3e6ec;padding-bottom:12px}
      h2{font-size:19px;margin-top:32px;color:#344054}
      h3{font-size:16px;color:#047857}
      table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px}
      th,td{border:1px solid #e3e6ec;padding:8px 10px;text-align:left;vertical-align:top}
      th{background:#fafbfc;font-weight:600}
      code{background:#f5f6f8;padding:2px 5px;border-radius:4px;font-size:13px}
      a{color:#4f46e5}hr{border:0;border-top:1px solid #e3e6ec;margin:32px 0}
      em{color:#667085}</style></head><body>
    ${markdownToHtml(res.markdown)}
    <hr><p><em>Generated by ARGUS · ${esc(res.dir || "")}</em></p>
    </body></html>`);
  w.document.close();
}

function markdownToHtml(md) {
  const lines = esc(md).split("\n");
  let html = "", inTable = false, inList = false;
  const closeAll = () => {
    if (inTable) { html += "</tbody></table>"; inTable = false; }
    if (inList) { html += "</ul>"; inList = false; }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\|/.test(line)) {
      const cells = line.split("|").slice(1, -1).map((c) => c.trim());
      if (cells.every((c) => /^-+$/.test(c.replace(/:/g, "")))) continue;
      if (!inTable) {
        if (inList) { html += "</ul>"; inList = false; }
        html += "<table><thead><tr>" + cells.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
        inTable = true;
      } else {
        html += "<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>";
      }
      continue;
    }
    closeAll();
    if (/^###\s/.test(line)) html += `<h3>${line.replace(/^###\s/, "")}</h3>`;
    else if (/^##\s/.test(line)) html += `<h2>${line.replace(/^##\s/, "")}</h2>`;
    else if (/^#\s/.test(line)) html += `<h1>${line.replace(/^#\s/, "")}</h1>`;
    else if (/^[-*]\s/.test(line)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${line.replace(/^[-*]\s/, "")}</li>`;
    }
    else if (/^---+$/.test(line)) html += "<hr>";
    else if (line === "") html += "";
    else html += `<p>${line}</p>`;
  }
  closeAll();
  return html
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

/* --------------------------------------------------------------- surface */
function recordSurface(ev) {
  const d = ev.data || {};
  if (ev.kind === "tool_call") {
    const key = `call:${ev.title}`;
    if (state.surfaceSeen.has(key)) return;
    state.surfaceSeen.add(key);
    state.surface.push({ endpoint: ev.title, ms: null, credits: null, pending: true });
    renderSurface();
    return;
  }
  const r = d.result;
  if (!r || !r.endpoint) return;
  const key = `${r.endpoint}:${state.surface.length}`;
  state.surface.push({
    endpoint: r.endpoint,
    ms: r.elapsed_ms,
    credits: r.credits,
    free: !r.credits,
    cached: r.cached,
    ok: r.ok,
    source: r.source,
  });
  renderSurface();
}

function renderSurface() {
  const calls = state.surface.filter((s) => !s.pending);
  $("surface-count").textContent = `${calls.length} call${calls.length === 1 ? "" : "s"}`;
  if (!calls.length) return;
  const total = calls.reduce((a, s) => a + (s.credits || 0), 0);
  $("surface-body").innerHTML =
    calls.slice(-40).reverse().map((s) => `
      <div class="surface-row">
        <span class="surface-ep">${esc(s.endpoint)}</span>
        <span class="surface-ms">${s.ms != null ? s.ms + "ms" : ""}${s.cached ? " · cached" : ""}</span>
        <span class="surface-cr ${s.free ? "free" : ""}">${s.free ? "free" : s.credits + " cr"}</span>
      </div>`).join("")
    + `<div class="act-note">Total metered: <b>${total}</b> credits · ${calls.length - calls.filter(s => !s.free).length} free (Zero Touch)</div>`;
}

/* -------------------------------------------------------------- evidence */
function pushEvidence(ev) {
  if (!ev) return;
  state.evidence.push(ev);
  $("evidence-count").textContent = `${state.evidence.length} item${state.evidence.length === 1 ? "" : "s"}`;
  renderEvidence();
}

function renderEvidence() {
  const list = $("evidence-list");
  const items = state.evidenceFilter === "all"
    ? state.evidence
    : state.evidence.filter((e) => e.kind === state.evidenceFilter);
  if (!items.length) {
    list.innerHTML = `<p class="placeholder">No ${state.evidenceFilter === "all" ? "" : state.evidenceFilter + " "}evidence yet.</p>`;
    return;
  }
  list.innerHTML = items.slice(-60).reverse().map((e) => {
    const snippet = (e.content || "").replace(/\s+/g, " ").slice(0, 240);
    return `
      <div class="evcard">
        <div class="evcard-top">
          <span class="evtag ${esc(e.kind)}">${esc(e.kind)}</span>
          <span class="evtime">${esc(e.retrieved_at || "")}</span>
        </div>
        <div class="evtitle">${esc(e.title || "")}</div>
        ${e.url ? `<div class="evurl">${esc(e.url)}</div>` : ""}
        ${snippet ? `<div class="evsnippet">${esc(snippet)}</div>` : ""}
      </div>`;
  }).join("");
}

/* --------------------------------------------------------------- credits */
function renderCredits(c) {
  if (!c) return;
  const pill = $("pill-credit");
  const trial = c.trial_credits_remaining;
  pill.textContent = trial != null
    ? `${c.spent} cr used · ${trial} free left`
    : `${c.spent} cr used · ${c.free_calls} free`;
  pill.title = `budget ${c.budget} · spent ${c.spent} · remaining ${c.remaining} · ${c.calls} calls`;
  pill.className = "pill pill-credit ok";
}

/* -------------------------------------------------------------- controls */
function resetUI() {
  state.events = 0; state.phase = null;
  state.evidence = []; state.surface = []; state.surfaceSeen = new Set();
  state.expanded = false; state.lastDecision = null;
  $("trace-count").textContent = "0 events";
  $("trace").innerHTML = `<div class="empty" id="trace-empty">
      <div class="empty-mark">◉</div>
      <p>Running. ARGUS narrates every step it takes.</p></div>`;
  $("decision-body").innerHTML = `<p class="placeholder">Working…</p>`;
  $("decision-state").textContent = "working";
  $("action-body").innerHTML = `<p class="placeholder">Waiting for the decision.</p>`;
  $("action-state").textContent = "—";
  $("surface-body").innerHTML = `<p class="placeholder">Talking to Anakin…</p>`;
  $("surface-count").textContent = "0 calls";
  $("evidence-list").innerHTML = `<p class="placeholder">Gathering evidence…</p>`;
  $("evidence-count").textContent = "0 items";
}

/* ------------------------------------------------------------------ boot */
$("run").onclick = startRun;
$("goal").addEventListener("keydown", (e) => { if (e.key === "Enter") startRun(); });
$("autoscroll").onclick = (e) => {
  state.autoScroll = !state.autoScroll;
  e.target.setAttribute("aria-pressed", String(state.autoScroll));
  e.target.textContent = state.autoScroll ? "Auto-scroll on" : "Auto-scroll off";
};
$("clear-trace").onclick = () => {
  state.events = 0;
  $("trace-count").textContent = "0 events";
  $("trace").innerHTML = `<div class="empty"><div class="empty-mark">◉</div><p>Trace cleared.</p></div>`;
};
$("evidence-filters").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  document.querySelectorAll("#evidence-filters .chip").forEach((c) => c.classList.remove("active"));
  b.classList.add("active");
  state.evidenceFilter = b.dataset.kind;
  renderEvidence();
});

loadConfig();
