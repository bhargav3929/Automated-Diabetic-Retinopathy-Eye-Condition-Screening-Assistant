/* RetinaGuard studio — vanilla JS, no build step */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const NODES = [
  "Ocular Gatekeeper",
  "Biomarker Detector",
  "ICDR Risk Grader",
  "Referral Timeline",
  "Triage Memo & Save",
];
let SAMPLES = [];
const state = { file: null, report: null, history: [], keys: {}, rate: null, rateAt: 0, modality: "auto", running: false, zoom: 1, pan: [0, 0] };

// ---------- helpers ----------
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null; try { body = await r.json(); } catch {}
  if (!r.ok) { const e = new Error(body?.detail || `Request failed (${r.status})`); e.status = r.status; throw e; }
  return body;
}
const riskCls = (r) => ({ Low: "low", Moderate: "moderate", Severe: "severe" }[r] || "moderate");
const riskBadge = (r) => ({ Low: "teal", Moderate: "amber", Severe: "red" }[r] || "");
const fmtTime = (ts) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

// ---------- status / rate pill ----------
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    state.rate = s.rate; state.rateAt = Date.now(); state.keys = s.keys;
    renderKeyDot(); renderRate();
  } catch {}
}
function renderKeyDot() {
  const any = state.keys.gemini?.set || state.keys.groq?.set;
  $("#keyDot").className = "dot " + (any ? "green" : "red");
}
function renderRate() {
  const r = state.rate; if (!r) return;
  const elapsed = Math.floor((Date.now() - state.rateAt) / 1000);
  const left = Math.max(0, r.reset_in - elapsed);
  let remaining = r.remaining;
  if (r.used > 0 && r.reset_in > 0 && left === 0 && elapsed > 0) { refreshStatus(); }
  $("#rateText").textContent = `${remaining}/${r.limit} Free Demo Screenings Left This Hour`;
  $("#rateTimer").textContent = r.used > 0 && left > 0 ? `next slot ${mmss(left)}` : "";
  $("#ratePill").className = "rate-pill" + (remaining === 0 ? " out" : remaining <= 1 ? " warn" : "");
}
setInterval(renderRate, 1000);

// ---------- view tabs ----------
function setView(v) {
  $$("#viewTabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach((x) => x.classList.toggle("active", x.id === "view-" + v));
  if (v === "compare") renderCompare();
  if (v === "referral") renderReferral();
}
$("#viewTabs").addEventListener("click", (e) => { const b = e.target.closest(".tab"); if (b) setView(b.dataset.view); });

// ---------- drawers / modal ----------
function openDrawer(id) { $("#" + id).classList.add("open"); $("#scrim").hidden = false; }
function closeAll() { $$(".drawer").forEach((d) => d.classList.remove("open")); $("#modalKeys").hidden = true; $("#scrim").hidden = true; }
$("#scrim").onclick = closeAll;
document.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeAll(); if (e.target === $("#modalKeys")) closeAll(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAll(); });
$("#btnHistory").onclick = async () => { await loadHistory(); openDrawer("drawerHistory"); };
$("#btnKeys").onclick = async () => { await refreshStatus(); renderKeyStates(); $("#modalKeys").hidden = false; $("#scrim").hidden = true; };

function renderKeyStates() {
  for (const [p, el] of [["gemini", "#kGeminiState"], ["groq", "#kGroqState"]]) {
    const k = state.keys[p];
    $(el).innerHTML = k?.set
      ? `✓ Saved (${esc(k.source)}) <span class="mono">${esc(k.masked)}</span> · <a href="#" data-clear="${p}">remove</a>`
      : "Not set";
  }
}
$("#modalKeys").addEventListener("click", async (e) => {
  const c = e.target.closest("[data-clear]"); if (!c) return;
  e.preventDefault();
  state.keys = await api("/api/keys/" + c.dataset.clear, { method: "DELETE" });
  renderKeyStates(); renderKeyDot();
});
$("#btnSaveKeys").onclick = async () => {
  const body = { gemini: $("#kGemini").value.trim(), groq: $("#kGroq").value.trim() };
  if (!body.gemini && !body.groq) return toast("Enter at least one key");
  state.keys = await api("/api/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  $("#kGemini").value = ""; $("#kGroq").value = "";
  renderKeyDot(); renderKeyStates(); toast("API keys saved"); closeAll();
};

// ---------- intake: modality, samples, dropzone ----------
$("#modalitySeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.modality = b.dataset.v; $$("#modalitySeg button").forEach((x) => x.classList.toggle("active", x === b));
});
async function loadSamples() {
  try { SAMPLES = await api("/api/samples"); } catch { SAMPLES = []; }
  $("#samples").innerHTML = SAMPLES.map((s) => `<button class="sample" data-id="${esc(s.id)}"><img src="${esc(s.url)}" alt="" loading="lazy">${esc(s.name)}</button>`).join("");
}
$("#samples").addEventListener("click", async (e) => {
  const b = e.target.closest(".sample"); if (!b) return;
  const s = SAMPLES.find((x) => x.id === b.dataset.id); if (!s) return;
  try {
    const blob = await (await fetch(s.url)).blob();
    setFile(new File([blob], `${s.id}.jpg`, { type: "image/jpeg" }));
    $("#fLabel").value = s.ctx.label; $("#fAge").value = s.ctx.age; $("#fDur").value = s.ctx.dur; $("#fHba").value = s.ctx.hba; $("#fSym").value = s.ctx.sym;
  } catch { toast("Could not load sample"); }
});

const dz = $("#dropzone"), fi = $("#fileInput");
dz.onclick = (e) => { if (!state.file || !e.target.closest(".viewport")) fi.click(); };
fi.onchange = () => fi.files[0] && setFile(fi.files[0]);
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) setFile(f); });

function setFile(f) {
  if (!/^image\/(jpeg|png|webp)$/.test(f.type)) return toast("Please use a JPG, PNG or WEBP image");
  state.file = f;
  const img = $("#preview");
  if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
  img.src = URL.createObjectURL(f);
  $("#dropEmpty").hidden = true; $("#viewport").hidden = false;
  $("#fileName").textContent = `${f.name} · ${(f.size / 1024).toFixed(0)} KB`;
  resetView(); hideAlert();
}

// lens toolbar
function applyView() {
  $("#preview").style.transform = `translate(${state.pan[0]}px, ${state.pan[1]}px) scale(${state.zoom})`;
  $("#zoomLabel").textContent = Math.round(state.zoom * 100) + "%";
}
function resetView() { state.zoom = 1; state.pan = [0, 0]; applyView(); }
$("#zIn").onclick = () => { state.zoom = Math.min(6, state.zoom * 1.25); applyView(); };
$("#zOut").onclick = () => { state.zoom = Math.max(0.5, state.zoom / 1.25); applyView(); };
$("#zReset").onclick = resetView;
$("#tRedFree").onclick = (e) => { e.currentTarget.classList.toggle("on"); $("#preview").classList.toggle("redfree"); };
$("#tContrast").onclick = (e) => { e.currentTarget.classList.toggle("on"); $("#preview").classList.toggle("hicontrast"); };
$("#viewport").addEventListener("wheel", (e) => { e.preventDefault(); state.zoom = Math.min(6, Math.max(0.5, state.zoom * (e.deltaY < 0 ? 1.1 : 0.9))); applyView(); }, { passive: false });
(() => {
  let drag = null; const vp = $("#viewport");
  vp.addEventListener("pointerdown", (e) => { drag = { x: e.clientX - state.pan[0], y: e.clientY - state.pan[1] }; vp.setPointerCapture(e.pointerId); });
  vp.addEventListener("pointermove", (e) => { if (drag) { state.pan = [e.clientX - drag.x, e.clientY - drag.y]; applyView(); } });
  vp.addEventListener("pointerup", () => (drag = null));
})();

// ---------- tracker ----------
function resetTracker() {
  $("#tracker").innerHTML = NODES.map((n, i) => `<li class="node" id="node${i + 1}"><span class="n">${i + 1}</span><div><div class="t">${esc(n)}</div><div class="r">Waiting…</div></div><span class="badge" hidden></span></li>`).join("");
}
function nodeSummary(n, d) {
  if (!d) return "";
  if (n === 1) return `${d.modality} · ${d.gradability}${d.reason ? " — " + d.reason : ""}`;
  if (n === 2) { const c = d.items.filter((i) => i.present).length; return `${c} of ${d.items.length} markers present`; }
  if (n === 3) return `${d.headline} · ${d.confidence}% confidence`;
  if (n === 4) return d.urgency_tier;
  if (n === 5) return `Report saved to history (#${d.id})`;
  return "";
}
function setNode(n, status, ev = {}) {
  const el = $("#node" + n); if (!el) return;
  el.className = "node " + status;
  const r = $(".r", el);
  r.textContent = status === "running" ? "Analyzing…" : ev.message || nodeSummary(n, ev.data);
  const b = $(".badge", el);
  if (ev.latency_ms != null) { b.hidden = false; b.textContent = ev.latency_ms >= 1000 ? (ev.latency_ms / 1000).toFixed(1) + "s" : ev.latency_ms + "ms"; if (ev.provider) b.title = ev.provider; }
}
function showAlert(msg, info) { const a = $("#runAlert"); a.textContent = msg; a.className = "alert" + (info ? " info" : ""); a.hidden = false; }
function hideAlert() { $("#runAlert").hidden = true; }

// ---------- run pipeline ----------
// Pre-compress to max 768px JPEG (q=0.82) so uploads stay small and the round-trip is fast.
function compressImage(file, max = 768, quality = 0.82) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file), img = new Image();
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      c.toBlob((b) => resolve(b || file), "image/jpeg", quality);
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
    img.src = url;
  });
}
const timer = { t0: 0, id: null };
function startTimer() {
  timer.t0 = performance.now(); $("#runTimer").hidden = false; $("#runTimer").classList.remove("done"); $("#timerNote").textContent = "elapsed";
  clearInterval(timer.id);
  const tick = () => { $("#timerVal").textContent = ((performance.now() - timer.t0) / 1000).toFixed(1) + "s"; };
  tick(); timer.id = setInterval(tick, 100);
}
function stopTimer(ok) {
  clearInterval(timer.id); const s = (performance.now() - timer.t0) / 1000;
  $("#timerVal").textContent = s.toFixed(1) + "s"; $("#runTimer").classList.toggle("done", !!ok); $("#timerNote").textContent = ok ? "total · done" : "stopped";
}
$("#btnRun").onclick = async () => {
  if (state.running) return;
  if (!state.file) return showAlert("Load an eye or retinal photograph, or click a sample first.", true);
  hideAlert(); resetTracker(); state.running = true;
  const btn = $("#btnRun"); btn.disabled = true; btn.textContent = "Running pipeline…";
  startTimer(); let ok = false;
  try {
    const blob = await compressImage(state.file);
    const fd = new FormData();
    fd.append("file", blob, "scan.jpg"); fd.append("modality", state.modality);
    fd.append("label", $("#fLabel").value); fd.append("age", $("#fAge").value);
    fd.append("diabetes_years", $("#fDur").value); fd.append("hba1c", $("#fHba").value); fd.append("symptoms", $("#fSym").value);
    const res = await fetch("/api/screen", { method: "POST", body: fd });
    if (!res.ok) { let b = {}; try { b = await res.json(); } catch {} throw Object.assign(new Error(b.detail || "Request failed"), { status: res.status }); }
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (line && handleEvent(JSON.parse(line))) ok = true; }
    }
  } catch (e) {
    showAlert(e.message, e.status === 429);
  } finally {
    stopTimer(ok); state.running = false; btn.disabled = false; btn.textContent = "Run Ocular Triage Pipeline"; refreshStatus();
  }
};
function handleEvent(ev) {
  if (ev.event === "node") setNode(ev.node, ev.status, ev);
  else if (ev.event === "rejected") showAlert(ev.message, true);
  else if (ev.event === "error") { showAlert(ev.message); $$(".node.running").forEach((n) => { n.className = "node failed"; $(".r", n).textContent = "Stopped"; }); }
  else if (ev.event === "result") { state.report = ev.report; renderWorkbench(); renderReferral(); setView("workbench"); toast("Triage report saved"); return true; }
  return false;
}

// ---------- workbench ----------
function renderWorkbench() {
  const r = state.report, el = $("#view-workbench");
  if (!r) {
    el.innerHTML = `<div class="empty"><svg width="54" height="54" viewBox="0 0 32 32"><circle cx="16" cy="16" r="14" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="16" cy="16" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="16" cy="16" r="2" fill="currentColor"/></svg><h3>No active screening</h3><div>Load a scan (or pick a 1-click sample) and run the pipeline.<br>Past results are in <b>History</b>.</div></div>`;
    return;
  }
  const g = r.grading, t = r.triage, gate = r.gatekeeper, c = riskCls(g.risk_level);
  const present = (i) => i.present;
  el.innerHTML = `
    <div class="hero ${c}">
      ${r.thumb ? `<img src="${esc(r.thumb)}" alt="Screened scan">` : ""}
      <div>
        <div class="risk">${esc(g.risk_level)} risk · ${esc(gate.modality)}</div>
        <h2>${esc(g.headline)}</h2>
        <div class="badges">
          <span class="badge ${riskBadge(g.risk_level)}">${g.kind === "fundus" ? "ICDR " + g.stage : esc(g.severity)}</span>
          <span class="badge">CONF ${g.confidence}%</span>
          <span class="badge ${g.macular_edema === "Suspected" ? "amber" : "teal"}">Macular edema: ${esc(g.macular_edema)}</span>
          <span class="badge">Gradability: ${esc(gate.gradability)}</span>
          <span class="badge" title="Total pipeline latency">${((r.timings?.total || 0) / 1000).toFixed(1)}s</span>
        </div>
        ${g.primary_finding ? `<div class="rationale"><b>${esc(g.primary_finding)}.</b> ${esc(g.rationale)}</div>` : `<div class="rationale">${esc(g.rationale)}</div>`}
      </div>
    </div>
    <div class="banner t${t.urgency_index}"><div><div class="lbl">Referral urgency${t.escalated_by_rules ? " · escalated by safety rules" : ""}</div><div class="tier">${esc(t.urgency_tier)}</div></div></div>
    <div class="rationale">${esc(t.rationale)}</div>
    <div class="sec"><h3>Anatomical Biomarker Grid</h3><div class="mgrid">
      ${r.biomarkers.items.map((i) => `<div class="mcard ${present(i) ? "present " + esc(i.severity) : ""}">
        <div class="top"><span><span class="ind"></span>${esc(i.name)}</span><span class="badge ${present(i) ? (i.severity === "Severe" ? "red" : "amber") : "teal"}">${present(i) ? "PRESENT · " + esc(i.severity) : "ABSENT"}</span></div>
        <div class="q">${present(i) ? "Quadrant: " + esc(i.quadrants) : "Not detected"}${i.detail ? " — " + esc(i.detail) : ""}</div></div>`).join("")}
    </div>${r.biomarkers.summary ? `<div class="rationale">${esc(r.biomarkers.summary)}</div>` : ""}</div>
    <div class="sec lists">
      <div><h3>Recommended Clinical Next Steps</h3><ul>${t.clinical_actions.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>None specified</li>"}</ul>
        ${t.follow_up ? `<div class="rationale"><b>Follow-up:</b> ${esc(t.follow_up)}</div>` : ""}</div>
      <div><h3>Systemic Care Actions</h3><ul>${t.systemic_actions.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>None specified</li>"}</ul></div>
    </div>
    <div class="disclaimer"><b>⚠ Medical decision-support disclaimer.</b> ${esc(r.disclaimer)}</div>
    <div class="sec muted small mono">${Object.entries(r.providers || {}).map(([k, v]) => `${k}: ${esc(v)}`).join(" · ")}</div>`;
}

// ---------- history ----------
async function loadHistory() {
  try { state.history = await api("/api/history"); } catch { state.history = []; }
  $("#historyList").innerHTML = state.history.length
    ? state.history.map((h) => `<div class="hitem" data-id="${h.id}"><img src="${esc(h.thumb)}" alt=""><div><div class="l">${esc(h.label)}</div><div class="s">${esc(h.stage_text)}</div><div class="s">${fmtTime(h.created_at)}</div></div><div><span class="badge ${riskBadge(h.risk_level)}">${esc(h.risk_level)}</span><div><a href="#" data-del="${h.id}" class="small">delete</a></div></div></div>`).join("")
    : `<div class="empty">No saved screenings yet.</div>`;
}
$("#historyList").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) { e.preventDefault(); e.stopPropagation(); await api("/api/history/" + del.dataset.del, { method: "DELETE" }); await loadHistory(); return; }
  const it = e.target.closest(".hitem"); if (!it) return;
  state.report = await api("/api/history/" + it.dataset.id);
  renderWorkbench(); renderReferral(); setView("workbench"); closeAll();
});

// ---------- compare ----------
async function renderCompare() {
  await loadHistory().catch(() => {});
  const el = $("#view-compare");
  if (state.history.length < 2) { el.innerHTML = `<div class="empty"><h3>Need two saved screenings</h3><div>Run at least two screenings (e.g. the Healthy and Diabetic Retinopathy samples), then compare progression here.</div></div>`; return; }
  const keep = [$("#cmpA")?.value, $("#cmpB")?.value];
  const opts = state.history.map((h) => `<option value="${h.id}">#${h.id} · ${esc(h.label)} · ${esc(h.stage_text)} · ${fmtTime(h.created_at)}</option>`).join("");
  el.innerHTML = `<div class="toolbar"><select id="cmpA">${opts}</select><span class="muted">vs</span><select id="cmpB">${opts}</select><button class="btn primary" id="cmpGo">Compare</button></div><div id="cmpOut" class="muted">Select any two saved screenings.</div>`;
  $("#cmpA").value = keep[0] || state.history[1].id; $("#cmpB").value = keep[1] || state.history[0].id;
  $("#cmpGo").onclick = async () => {
    const a = $("#cmpA").value, b = $("#cmpB").value;
    if (a === b) return toast("Pick two different screenings");
    try { drawCompare(await api(`/api/compare?a=${a}&b=${b}`)); } catch (e) { toast(e.message); }
  };
}
function drawCompare(d) {
  const col = (title, r) => `<div class="col"><div class="badge">${title}</div><img src="${esc(r.thumb)}" alt=""><h3 style="margin:8px 0 4px">${esc(r.grading.headline)}</h3>
    <div class="badges"><span class="badge ${riskBadge(r.grading.risk_level)}">${esc(r.grading.risk_level)}</span><span class="badge">CONF ${r.grading.confidence}%</span><span class="badge">${esc(r.gatekeeper.modality)}</span></div>
    <div class="small muted" style="margin-top:6px">${fmtTime(r.created_at)} · ${esc(r.patient.label || "Unlabeled")}</div><div style="margin-top:6px"><b>${esc(r.triage.urgency_tier)}</b></div></div>`;
  const x = d.delta;
  const trendCls = x.trend === "progression" ? "red" : x.trend === "improvement" ? "teal" : "";
  const sh = x.urgency_change.shift;
  $("#cmpOut").className = "";
  $("#cmpOut").innerHTML = `<div class="cmp">${col("BASELINE", d.baseline)}${col("FOLLOW-UP", d.followup)}</div>
    <div class="delta"><div class="badges"><span class="badge ${trendCls}">${esc(x.trend).toUpperCase()}${x.stage_delta ? ` (${x.stage_delta > 0 ? "+" : ""}${x.stage_delta} stage)` : ""}</span>
      <span class="badge">Interval ${x.interval_days} d</span><span class="badge">Confidence ${x.confidence_delta >= 0 ? "+" : ""}${x.confidence_delta}%</span>
      <span class="badge">Edema ${esc(x.edema.before)} → ${esc(x.edema.after)}</span></div>
      <h3 style="margin:14px 0 4px">Lesion delta</h3>
      ${x.lesions.length ? `<table><tr><th>Biomarker</th><th>Before</th><th>After</th><th>Change</th></tr>${x.lesions.map((l) => `<tr><td>${esc(l.name)}</td><td>${esc(l.before)}</td><td>${esc(l.after)}</td><td><span class="badge ${l.change === "new" || l.change === "worsened" ? "red" : "teal"}">${esc(l.change)}</span></td></tr>`).join("")}</table>` : `<div class="muted">No change in detected biomarkers.</div>`}
      <h3 style="margin:14px 0 4px">Follow-up timeline</h3>
      <div>${esc(x.urgency_change.before)} → <b>${esc(x.urgency_change.after)}</b> ${sh > 0 ? '<span class="badge red">urgency ↑</span>' : sh < 0 ? '<span class="badge teal">urgency ↓</span>' : '<span class="badge">unchanged</span>'}</div>
    </div>
    <div class="disclaimer"><b>⚠</b> ${esc(d.followup.disclaimer)}</div>`;
}

// ---------- referral ----------
function md(src) {
  const lines = esc(src).split("\n"); let out = "", inList = false;
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<i>$2</i>").replace(/(^|\W)_(.+?)_(?=\W|$)/g, "$1<i>$2</i>");
  for (const l of lines) {
    const li = l.match(/^- (.*)/);
    if (inList && !li) { out += "</ul>"; inList = false; }
    if (li) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(li[1])}</li>`; }
    else if (l.startsWith("# ")) out += `<h1>${inline(l.slice(2))}</h1>`;
    else if (l.startsWith("## ")) out += `<h2>${inline(l.slice(3))}</h2>`;
    else if (l.startsWith("&gt; ")) out += `<blockquote>${inline(l.slice(5))}</blockquote>`;
    else if (l.trim() === "---") out += "<hr>";
    else if (l.trim()) out += `<p style="margin:4px 0">${inline(l)}</p>`;
  }
  return out + (inList ? "</ul>" : "");
}
function renderReferral() {
  const r = state.report, el = $("#view-referral");
  if (!r) { el.innerHTML = `<div class="empty"><h3>No referral brief yet</h3><div>Run or open a screening to generate the referral letter.</div></div>`; return; }
  el.innerHTML = `<div class="toolbar"><button class="btn primary" id="btnCopy">Copy Referral Note</button><button class="btn" id="btnDl">Download Triage Report (.md)</button></div><div class="letter">${md(r.referral_letter_md)}</div>`;
  $("#btnCopy").onclick = async () => {
    try { await navigator.clipboard.writeText(r.referral_letter_md); toast("Referral note copied"); }
    catch { const ta = document.createElement("textarea"); ta.value = r.referral_letter_md; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Referral note copied"); }
  };
  $("#btnDl").onclick = () => {
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([r.report_md], { type: "text/markdown" }));
    a.download = `retinaguard-triage-${r.id || "report"}.md`; a.click(); URL.revokeObjectURL(a.href);
  };
}

// ---------- boot ----------
resetTracker(); renderWorkbench(); renderReferral(); refreshStatus(); loadSamples();
