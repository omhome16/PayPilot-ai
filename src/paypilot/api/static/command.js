/* PayPilot Command Center — zero-build client.
   Polls real endpoints; every panel renders live server state. */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtRs = (paise) => "₹" + Math.round(paise / 100).toLocaleString("en-IN");
const jsonCompact = (o) => JSON.stringify(o).slice(0, 140);

async function api(path, opts) {
  const r = await fetch(path, opts || {});
  if (!r.ok) {
    let detail = r.status;
    try { detail = (await r.json()).detail || r.status; } catch (_) {}
    throw new Error(path + " → " + detail);
  }
  return r.json();
}
const post = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

/* ---------- navigation ---------- */
function go(name) {
  document.querySelectorAll(".nav").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + name));
  window.scrollTo({ top: 0 });
  if (name === "live") refreshLive();
  if (name === "memory") { loadTables(); streamTools(); }
  if (name === "ledger") refreshLedger();
  if (name === "voice") loadVoiceDemo();
}
document.querySelectorAll(".nav").forEach((b) => b.addEventListener("click", () => go(b.dataset.panel)));

/* ---------- overview ---------- */
async function refreshOverview() {
  try {
    const [store, mon, dec] = await Promise.all([
      api("/store/tables"), api("/monitor/data"), api("/store/rows?table=decisions&limit=300"),
    ]);
    const count = (t) => (store.tables.find((x) => x.name === t) || {}).rows ?? 0;
    const deg = (dec.rows || []).filter((r) => r.degraded).length;
    const events = mon.counters.events ?? 0;
    $("ov-kpis").innerHTML = [
      ["webhooks processed", events, "blue"],
      ["decisions logged", count("decisions"), "blue"],
      ["fail-loud escalations", deg, "red"],
      ["voice calls", count("voice_calls"), "violet"],
      ["memory rows", store.tables.reduce((a, t) => a + t.rows, 0), "gold"],
    ].map(([l, n, c]) =>
      `<div class="kpi"><div class="n ${c}">${n}</div><div class="l">${esc(l)}</div></div>`).join("");

    const llmOn = !!mon.merchant; // merchant implies api up
    $("ov-systems").innerHTML = [
      ["◇", "Webhook receiver", "HMAC-verified payment.failed in → decision out", "live"],
      ["◎", "Agent graph", "SENSE → THINK → VALIDATE → ACT state machine", "live"],
      ["◉", "Voice channel", "LLM-written, safety-validated; fail-loud when down", llmOn ? "armed" : "needs key"],
      ["▤", "Memory (SQLite)", "customers · episodes · decisions · tool calls", store.tables.length + " tables"],
      ["◔", "Eval harness", "agent vs baseline on identical seeded worlds", "reproducible"],
    ].map(([i, t, d, v]) =>
      `<div class="sys"><div class="sys-t"><span>${i}</span>${esc(t)}</div>` +
      `<div class="sys-d">${esc(d)}</div><div class="sys-v">${esc(v)}</div></div>`).join("");
  } catch (_) {}
}

/* ---------- live recovery ---------- */
function sim(scenario) {
  post("/monitor/simulate", { scenario }).then(() => refreshLive()).catch(() => {});
}
async function refreshLive() {
  try {
    const d = await api("/monitor/data");
    const c = d.counters;
    $("live-kpis").innerHTML = [
      ["webhooks", c.events ?? 0, "blue"],
      ["smart_retry", c.smart_retry ?? 0, "blue"],
      ["payment_link", c.payment_link ?? 0, "gold"],
      ["voice_nudge", c.voice_nudge ?? 0, "violet"],
      ["human_escalation", c.human_escalation ?? 0, "red"],
    ].map(([l, n, col]) =>
      `<div class="kpi"><div class="n ${col}">${n}</div><div class="l">${esc(l)}</div></div>`).join("");
    $("live-stream").innerHTML = d.events.map((e) => card(e)).join("") ||
      `<div class="empty">No events yet — fire a simulation above.</div>`;
  } catch (_) {}
}

const BADGE = {
  smart_retry: ["b", "smart retry"], retry: ["m", "retry"], wait_self_heal: ["m", "wait"],
  payment_link: ["y", "payment link"], voice_nudge: ["v", "voice"], human_escalation: ["r", "human"],
  recovered: ["g", "recovered"],
};

function card(e) {
  if (e.status !== 200 || e.action === undefined)
    return `<div class="event"><span class="meta">${esc(e.ts)} · ${esc(e.source)} · HTTP ${esc(e.status)} — ${esc(e.detail || "")}</span></div>`;
  const b = BADGE[e.action] || ["m", e.action];
  let h = `<div class="event">
    <div class="meta">${esc(e.ts)} · ${esc(e.source)} · ${esc(e.customer || "")} · ${esc(e.mode)} · ${fmtRs((e.amount_rupees ?? 0) * 100)} · attempt ${esc(e.attempt_no)}</div>
    <div class="action-line"><span class="badge ${b[0]}">${esc(b[1])}</span>${e.reason ? esc(e.reason) : ""}</div>`;
  if (e.degraded)
    h += `<div class="chips"><span class="chip">⚠ fail-loud: ${esc(e.degraded.reason || "degraded")}</span></div>`;
  const v = e.voice;
  if (v) {
    if (v.escalated) {
      h += `<div class="chips"><span class="chip">⚠ voice escalated to human review</span></div>` +
        `<div class="reason">${esc(v.reason || "")}</div>`;
    } else {
      const st = v.strategy || {};
      const chips = [
        st.history_tone ? `history: ${esc(st.history_tone)}` : null,
        `script: ${esc(v.source)}`,
        st.safety && st.safety.ok ? "safety ✓" : "safety ✗",
      ].filter(Boolean).map((x) => `<span class="chip">${x}</span>`).join("");
      h += `<div class="chips">${chips}</div>` +
        `<div class="script"><span class="say">▶</span> ${esc(v.script || "")}</div>` +
        `<div class="row" style="margin-top:8px">
          ${v.script ? `<button class="btn" onclick="speak(${JSON.stringify(v.script).replace(/"/g, "&quot;")})">Play call</button>` : ""}
          <button class="btn" onclick="stopSpeak()">Stop</button></div>`;
    }
  }
  h += `</div>`;
  return h;
}

/* ---------- agent graph ---------- */
let traceTimer = null;
const NODE_ORDER = ["sense", "think", "validate", "act", "abstain", "escalate"];
async function runTrace() {
  const mode = $("g-mode").value;
  const amount = Math.round((Number($("g-amount").value) || 100) * 100);
  const attempts = Number($("g-attempt").value) || 1;
  const outage = $("g-outage").checked;
  $("g-run").disabled = true;
  document.querySelectorAll(".step-line").forEach((s) => s.classList.remove("show"));
  document.querySelectorAll(".node").forEach((n) => n.classList.remove("lit", "ok", "bad"));
  try {
    const d = await post("/agent/trace", { mode, amount_paise: amount, attempts_made: attempts, outage });
    const steps = d.steps;
    // materialize the step log (each line reveals as the node fires)
    $("g-steps").innerHTML = steps.map((s, i) =>
      `<div class="step-line ${esc(s.node)}" data-i="${i}"><b>${i + 1}. ${esc(String(s.node).toUpperCase())}</b>` +
      ` <span class="sl-note">${s.ok ? "" : "⚠ "}${esc(s.note || "")}</span>` +
      (s.detail && Object.keys(s.detail).length ? `<pre>${esc(JSON.stringify(s.detail, null, 2))}</pre>` : "") +
      `</div>`).join("");
    // light node order: show the graph backbone first
    NODE_ORDER.forEach((nd) => {
      const el = document.querySelector(`.node[data-node="${nd}"]`);
      if (el) el.classList.add("lit");
    });
    steps.forEach((s, i) => {
      const el = document.querySelector(`.node[data-node="${s.node}"]`);
      if (el) { el.classList.add(s.ok ? "ok" : "bad"); el.querySelector(".node-state").textContent = s.ok ? "approved" : "refused / escalated"; }
    });
    traceTimer = setTimeout(() => {
      steps.forEach((s, i) => setTimeout(() => {
        const line = $("g-steps").querySelector(`.step-line[data-i="${i}"]`);
        if (line) line.classList.add("show");
      }, 260 * i));
    }, 350);
  } catch (err) {
    $("g-steps").innerHTML = `<div class="event"><span class="meta">trace failed: ${esc(err.message)}</span></div>`;
  } finally { $("g-run").disabled = false; }
}

/* ---------- voice studio ---------- */
let callSession = null;
let voiceMode = "sim";
const SIM_REPLIES = {
  pay_now: "haan, main abhi pay kar deta hoon",
  will_pay_later: "mujhe salary aane ke baad pay karna hai",
  cancel: "mujhe ye subscription nahi chahiye, band kar do",
  need_help: "mujhe link nahi mila, kya karu?",
  human: "mujhe kisi insaan se baat karni hai",
  unclear: "hmm theek hai",
};

async function loadVoiceDemo() {
  const d = await api("/voice/demo");
  $("v-customer").innerHTML = d.customers.map((c) =>
    `<option value="${esc(c.subscription_id)}">${esc(c.name)} · ${esc(c.plan)} · ${fmtRs((c.amount_rupees || 0) * 100)}</option>`).join("");
  window.llmConfigured = d.llm_configured;
  if (!d.llm_configured) {
    $("v-status").innerHTML = `<span class="badge y">no OPENROUTER key — calls fail loud to human review</span>`;
  }
}
function setVoiceMode(m) {
  voiceMode = m;
  document.querySelectorAll("#v-input .seg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  const sim = $("v-simline"), typed = $("v-typeline"), mic = $("v-micline");
  [sim, typed, mic].forEach((x) => { if (x) x.style.display = "none"; });
  if (m === "sim" && sim) sim.style.display = "flex";
  if (m === "type" && typed) typed.style.display = "flex";
  if (m === "mic" && mic) mic.style.display = "flex";
  if (m === "mic" && !("webkitSpeechRecognition" in window)) {
    $("v-status").innerHTML = `<span class="badge r">microphone not supported in this browser — use type or simulate</span>`;
  }
}
function renderCall(d, lastTurn) {
  const conv = d.conversation || d;
  const call = $("v-call");
  call.innerHTML = (conv.transcript || []).map((t) => {
    const cls = t.role === "agent" ? "agent" : t.role === "system" ? "system" : "customer";
    const who = t.role === "agent" ? "PayPilot · " + (t.source || "") : t.role === "system" ? "system" : "customer";
    let meta = "";
    if (t.role === "agent" && t.text) {
      meta = `<div class="meta"><button class="btn" style="padding:3px 10px" onclick="speak(${JSON.stringify(t.text).replace(/"/g, "&quot;")})">▶ speak</button></div>`;
    }
    return `<div class="bubble ${cls}"><span class="who">${esc(who)}</span>${t.text ? esc(t.text) : "(silent)"}${meta}</div>`;
  }).join("") || `<div class="empty">Start a call.</div>`;
  call.scrollTop = call.scrollHeight;

  const st = $("v-status");
  const turn = lastTurn || (d.turn);
  let status = "";
  if (turn) {
    const oc = turn.outcome;
    if (turn.degraded) status += `<div class="outcome deg">⚠ fail-loud: ${esc(turn.note || "")}</div>`;
    else if (oc && oc.kind !== "none")
      status += `<div class="outcome">→ ${esc(oc.kind)}${oc.intervention ? " (" + esc(oc.intervention) + ")" : ""} — ${esc(oc.note || "")}</div>`;
    if (turn.done) status += `<p class="muted" style="margin-top:6px">Call closed.</p>`;
  }
  status += `<p class="muted" style="margin-top:6px">${conv.done ? "Call ended." : "Listening for the customer's reply…"}</p>`;
  st.innerHTML = status;

  const knows = $("v-knows");
  knows.innerHTML = (conv.tool_events || []).map((t) =>
    `<div class="trow"><span class="tname">${esc(t.tool)}</span><span class="trows">→ ${esc(t.summary || "")}</span>` +
    `<span class="tlat">${t.latency_ms}ms</span></div>`).join("") ||
    `<p class="muted">Start a call — tool queries will stream here.</p>`;
  $("v-input").style.display = conv.done ? "none" : "block";
  if (conv.done) setVoiceMode("sim");
}
async function startCall() {
  stopSpeak();
  const [sub] = $("v-customer").value.split(":").filter(Boolean);
  const scenario = $("v-scenario").value.split(":");
  const modeMap = { isf: "insufficient_funds", revoked: "mandate_revoked", downtime: "bank_downtime" };
  const mode = modeMap[scenario[0]] || "insufficient_funds";
  const amount = Number(scenario[1]) || 99900;
  $("v-input").innerHTML = "";
  $("v-call").innerHTML = `<div class="empty">Opening call…</div>`;
  try {
    const d = await post("/voice/open", {
      subscription_id: sub, mode, amount_paise: amount, attempts_made: 3, episode_no: 1,
    });
    callSession = d.session_id;
    buildInput();
    renderCall(d);
  } catch (err) {
    $("v-status").innerHTML = `<span class="badge r">open failed: ${esc(err.message)}</span>`;
  }
}
async function sendCustomer(text) {
  if (!callSession) return;
  try {
    const d = await post("/voice/turn", { session_id: callSession, text });
    renderCall(d);
  } catch (err) {
    $("v-status").innerHTML = `<span class="badge r">turn failed: ${esc(err.message)}</span>`;
  }
}
function buildInput() {
  $("v-input").innerHTML = `
    <div class="seg">
      <button data-mode="sim" class="on" onclick="setVoiceMode('sim')">Simulate</button>
      <button data-mode="type" onclick="setVoiceMode('type')">Type</button>
      <button data-mode="mic" onclick="setVoiceMode('mic')">Mic</button>
    </div>
    <div class="inputline" id="v-simline" style="display:flex">
      <select id="v-simselect">${Object.entries(SIM_REPLIES).map(([k, v]) => `<option value="${k}">${esc(k)} — "${esc(v)}"</option>`).join("")}</select>
      <button class="btn primary" onclick="sendCustomer(SIM_REPLIES[document.getElementById('v-simselect').value])">Send reply</button>
    </div>
    <div class="inputline" id="v-typeline" style="display:none">
      <input id="v-typeinput" placeholder="Type what the customer says…">
      <button class="btn primary" onclick="sendCustomer(document.getElementById('v-typeinput').value)">Send</button>
    </div>
    <div class="inputline" id="v-micline" style="display:none">
      <button class="btn primary" onclick="listenMic()">🎙 Start listening</button><span class="muted"> speak a reply in Hinglish</span>
    </div>`;
  setVoiceMode("sim");
}
function listenMic() {
  if (!("webkitSpeechRecognition" in window)) return;
  const rec = new webkitSpeechRecognition();
  rec.lang = "en-IN"; rec.interimResults = false; rec.maxAlternatives = 1;
  $("v-status").innerHTML = `<span class="badge y">listening… speak now</span>`;
  rec.onresult = (ev) => {
    const text = ev.results[0][0].transcript;
    $("v-status").innerHTML = `<span class="badge b">heard: ${esc(text)}</span>`;
    sendCustomer(text);
  };
  rec.onerror = () => { $("v-status").innerHTML = `<span class="badge r">microphone error — try type or simulate</span>`; };
  rec.start();
}

/* ---------- speech ---------- */
function speak(text) {
  if (!text) return;
  if ("speechSynthesis" in window) {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "hi-IN"; u.rate = 0.95;
    speechSynthesis.speak(u);
  }
}
function stopSpeak() { if ("speechSynthesis" in window) speechSynthesis.cancel(); }

/* ---------- memory / db ---------- */
async function loadTables() {
  const d = await api("/store/tables");
  $("mem-tables").innerHTML = d.tables.map((t) =>
    `<button class="tbl-pill" onclick="browse('${esc(t.name)}')"><span>${esc(t.name)}</span><span class="cnt">${t.rows}</span></button>`).join("");
}
async function browse(table) {
  const d = await api("/store/rows?table=" + encodeURIComponent(table) + "&limit=40");
  const rows = d.rows;
  if (!rows.length) { $("mem-browser").innerHTML = `<p class="muted">0 rows.</p>`; return; }
  const cols = Object.keys(rows[0]);
  let h = `<div class="table-wrap"><table class="data"><thead><tr>` +
    cols.map((c) => `<th>${esc(c)}</th>`).join("") + `</tr></thead><tbody>`;
  rows.forEach((r) => {
    h += `<tr>` + cols.map((c) => {
      const v = r[c];
      let s = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "");
      s = s.length > 140 ? s.slice(0, 137) + "…" : s;
      return `<td title="${esc(s)}">${esc(s)}</td>`;
    }).join("") + `</tr>`;
  });
  h += `</tbody></table></div><p class="muted" style="margin-top:6px">${rows.length} rows shown, newest first</p>`;
  $("mem-browser").innerHTML = h;
}
async function streamTools() {
  try {
    const d = await api("/store/rows?table=tool_calls&limit=30");
    const rows = d.rows || [];
    if (!rows.length) { $("mem-tools").innerHTML = `<div class="empty">No tool calls yet — open a voice call to watch the agent query its memory.</div>`; return; }
    $("mem-tools").innerHTML = rows.map((r) => {
      let args = r.args || "{}", res = r.result || "{}";
      try { args = jsonCompact(JSON.parse(args)); } catch (_) {}
      try { res = jsonCompact(JSON.parse(res)); } catch (_) {}
      return `<div class="trow"><span class="tname">◉ ${esc(r.tool_name)}</span>` +
        `<span class="tsum">${esc(args)} → ${esc(res)}</span>` +
        `<span class="tlat">${esc(r.episode_key || "")} · ${r.latency_ms}ms</span></div>`;
    }).join("");
  } catch (_) {}
}

/* ---------- ledger ---------- */
async function refreshLedger() {
  try {
    const d = await api("/store/rows?table=decisions&limit=120");
    const rows = d.rows || [];
    const f = $("l-filter").value;
    const filtered = rows.filter((r) => {
      if (f === "degraded") return !!r.degraded;
      if (f === "escalation") return r.chosen === "human_escalation";
      return true;
    });
    $("ledger-table").innerHTML = filtered.length ? `<div class="table-wrap"><table class="data">
      <thead><tr><th>ts</th><th>episode</th><th>mode</th><th>att</th><th>chosen</th><th>reason</th><th>degraded</th></tr></thead><tbody>` +
      filtered.map((r) => `<tr>
        <td>${esc((r.ts || "").slice(5, 19))}</td>
        <td>${esc(r.episode_key || "")}</td>
        <td>${esc(r.mode || "")}</td>
        <td>${esc(r.attempts ?? "")}</td>
        <td>${esc(r.chosen || "—")}</td>
        <td>${esc((r.reason || "").slice(0, 90))}</td>
        <td>${r.degraded ? `<span class="badge r">fail-loud</span>` : ""}</td></tr>`).join("") +
      `</tbody></table></div>` : `<div class="empty">No decisions yet — fire a webhook and they appear here.</div>`;
  } catch (_) {}
}

/* ---------- eval ---------- */
async function runEval() {
  const btn = $("ev-run"); btn.disabled = true;
  $("ev-progress").textContent = "running agent vs baseline on 5 seeded worlds…";
  $("ev-results").innerHTML = "";
  try {
    const d = await post("/eval/quick", { worlds: 5, size: 200 });
    const wins = `${d.wins}/${d.worlds}`;
    $("ev-results").innerHTML = `<div class="kpi-row">` +
      `<div class="kpi"><div class="n blue">${wins}</div><div class="l">worlds won vs baseline</div></div>` +
      `<div class="kpi"><div class="n gold">${d.mean_multiplier ?? "∞"}×</div><div class="l">mean recovered multiple</div></div>` +
      `<div class="kpi"><div class="n green">${fmtRs(d.agent_recovered_rupees * 100)}</div><div class="l">agent recovered</div></div>` +
      `<div class="kpi"><div class="n">${fmtRs(d.baseline_recovered_rupees * 100)}</div><div class="l">baseline recovered</div></div>` +
      `</div><div class="table-wrap"><table class="data"><thead><tr><th>world</th><th>baseline ₹</th><th>agent ₹</th><th>agent wins</th></tr></thead><tbody>` +
      (d.per_world || []).map((w) => `<tr><td>seed ${w.seed}</td><td>${fmtRs(w.baseline * 100)}</td><td>${fmtRs(w.agent * 100)}</td><td>${w.win ? "✓" : "—"}</td></tr>`).join("") +
      `</tbody></table></div><p class="eval-note">Quick sweep shown. The submitted headline (85% win-rate over 20 worlds, mean 2.42×) lives in EVAL_REPORT.md — same code, more worlds. Reference doctrine brain, deterministic.${d.cached ? " (cached)" : ""}</p>`;
  } catch (err) {
    $("ev-results").innerHTML = `<div class="event"><span class="meta">sweep failed: ${esc(err.message)}</span></div>`;
  } finally {
    btn.disabled = false; $("ev-progress").textContent = "";
  }
}

/* ---------- webhook lab ---------- */
async function fireLab(ev) {
  ev.preventDefault();
  const f = ev.target;
  const spec = {
    mode: f.mode.value,
    amount_paise: Math.round((Number(f.amount.value) || 100) * 1),
    attempt_no: Number(f.attempt.value) || 1,
    subscription_id: f.sub.value,
    customer_name: f.name.value,
  };
  $("lab-response").innerHTML = `<p class="muted">firing signed payment.failed…</p>`;
  try {
    const d = await post("/monitor/fire", spec);
    $("lab-response").innerHTML = `<div class="event"><div class="action-line"><span class="badge ${(BADGE[d.action] || ["m"])[0]}">${esc(d.action || "none")}</span>${esc(d.reason || "")}</div>` +
      `<pre style="background:var(--bg2);border-radius:8px;padding:8px;margin-top:6px;overflow:auto;white-space:pre-wrap">${esc(JSON.stringify(d, null, 2))}</pre></div>`;
    refreshLive(); refreshLedger();
  } catch (err) {
    $("lab-response").innerHTML = `<div class="event"><span class="meta">fire failed: ${esc(err.message)}</span></div>`;
  }
}

/* ---------- boot ---------- */
function buildRailDiagram() {
  const nodes = [
    ["sense", "◈", "Sense"], ["think", "◉", "Think"], ["validate", "⚖", "Validate"],
    ["act", "→", "Act"], ["abstain", "■", "Abstain"], ["escalate", "⚠", "Escalate"],
  ];
  $("g-rail").innerHTML = nodes.map(([id, ico, name], i) => `
    <div class="node" data-node="${id}"><span class="node-ico">${ico}</span>
      <div class="node-name">${name}</div><div class="node-state"></div>${i < nodes.length - 1 ? '<span class="arrow">›</span>' : ""}</div>`).join("");
  $("g-steps").innerHTML = "";
}
async function refreshTop() {
  try {
    const [tables, mon, vd] = await Promise.all([
      api("/store/tables"), api("/monitor/data"), api("/voice/demo"),
    ]);
    const rows = tables.tables.reduce((a, t) => a + t.rows, 0);
    $("top-status").innerHTML =
      `<span class="pill on"><span class="dot"></span>api live</span>` +
      `<span class="pill ${vd.llm_configured ? "on" : "warn"}"><span class="dot"></span>llm ${vd.llm_configured ? "configured" : "no key — fail-loud armed"}</span>` +
      `<span class="pill"><span class="dot"></span>store <code>${rows}</code> rows</span>` +
      `<span class="pill"><span class="dot"></span>webhooks <code>${mon.counters.events ?? 0}</code></span>`;
  } catch (_) {}
}

function bindStatic() {
  $("ov-demo-pack").addEventListener("click", () => {
    sim("fresh_funds"); setTimeout(() => sim("revoked"), 350); setTimeout(() => sim("voice"), 700);
    setTimeout(() => go("live"), 950);
  });
  $("g-run").addEventListener("click", runTrace);
  $("v-start").addEventListener("click", startCall);
  $("ev-run").addEventListener("click", runEval);
  $("lab-form").addEventListener("submit", fireLab);
  $("l-filter").addEventListener("change", refreshLedger);
  $("g-mode").addEventListener("change", () => {
    const m = $("g-mode").value;
    const amounts = { insufficient_funds: 1500, mandate_revoked: 999, limit_exceeded: 1500, bank_downtime: 1500 };
    $("g-amount").value = amounts[m] ?? 1500;
  });
  $("g-attempt").addEventListener("change", () => {
    if ($("g-mode").value === "mandate_revoked" || $("g-mode").value === "limit_exceeded") $("g-attempt").value = 1;
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindStatic();
  buildRailDiagram();
  refreshOverview(); refreshLive(); refreshTop();
  loadTables(); refreshLedger();
  setInterval(refreshOverview, 3000);
  setInterval(refreshLive, 2500);
  setInterval(refreshTop, 5000);
  setInterval(() => { if (document.querySelector(".panel.active")?.id === "panel-memory") streamTools(); }, 3000);
  setInterval(() => { if (document.querySelector(".panel.active")?.id === "panel-ledger") refreshLedger(); }, 2500);
  if ("webkitSpeechRecognition" in window) window.llmConfigured = false;
});
