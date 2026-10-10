/* Autopilot tab: what it prepared, the apply session, this week (goal, drafts, events), history, and its settings. */

const SOURCE_LABEL = { autopilot: "Autopilot", claude_code: "Claude Code", codex: "ChatGPT", mcp: "your AI", chat: "chat", card: "you", app: "you", undo: "undo" };
const FILL_LABEL = { chrome: "Fill in Chrome", claude_code: "Claude Code", codex: "ChatGPT (Codex)", atlas: "ChatGPT Atlas", manual: "By hand" };
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const AP = { data: null };

async function renderAutopilot() {
  const main = $("#main");
  if (renderAutopilot.pid !== S.pid) main.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  renderAutopilot.pid = S.pid;
  let d;
  try { d = await api(`/api/autopilot?profile_id=${S.pid}`); } catch (e) { main.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (S.tab !== "autopilot") return;
  AP.data = d;
  const st = d.settings, run = d.state, last = d.last_run, w = d.week;
  const ready = d.queue.filter((q) => q.status === "ready" || q.status === "opened");
  const done = d.queue.filter((q) => !(q.status === "ready" || q.status === "opened"));
  setReadyCount(ready.length);
  const status = !st.on ? '<span class="tag">Off</span>' : d.paused ? `<span class="tag warn">Paused until ${esc(st.paused_until)}</span>`
    : `<span class="tag good">On</span> <span class="muted small">next ${d.next_run ? new Date(d.next_run * 1000).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }) : "—"}</span>`;
  const goalPct = w.goal ? Math.min(100, Math.round((100 * w.applied) / w.goal)) : 0;
  main.innerHTML = `
    <div class="card card-pad stack ap-hero">
      <div class="ap-hero-row">
        <div style="min-width:0"><div class="row" style="gap:8px"><h2>Autopilot</h2>${status}</div>
          <div class="muted small">Prepares your best matches every ${st.days.length === 7 ? "day" : st.days.map((x) => DAY_NAMES[x]).join(", ")} at ${esc(st.time)}${st.instant ? " · ⚡ instant for 88+" : ""} · <a href="#" id="ap-engine">${esc(S.app.ai_label || "built-in rules")}</a></div></div>
        <div class="ap-ctrls">
          <label class="switch" title="Run on its schedule"><input type="checkbox" id="ap-on" ${st.on ? "checked" : ""}><span></span>${st.on ? "On" : "Off"}</label>
          <button class="btn btn-ghost btn-sm" id="ap-settings">Settings</button>
          <button class="btn btn-ghost btn-sm" id="ap-preview">Preview</button>
          <button class="btn btn-sm" id="ap-run" ${run.running ? "disabled" : ""}>${run.running ? '<span class="spinner"></span> Running…' : "Run now"}</button>
        </div>
      </div>
      <div class="muted small" id="ap-progress">${run.running ? `<span class="spinner"></span> ${esc(run.step || "Working…")}` : last ? `Last run ${ago(last.finished_at || last.started_at)} · ${{ claude_code: "your Claude Code", codex: "ChatGPT", rules: "built-in rules", groq: "Groq" }[last.engine] || last.engine} · prepared ${last.queued}${last.error ? ` · <span style="color:var(--warn)">${esc(last.error)}</span>` : ""}` : "Hasn't run yet."}</div>
      ${last && last.brief ? briefBlock(last.brief) : ""}
    </div>

    <div class="section-title"><h2>Ready to apply <span class="muted">(${ready.length})</span></h2><span class="spacer"></span>
      ${ready.length ? `<button class="btn btn-sm" id="ap-session">Start apply session</button>` : ""}</div>
    <div class="card joblist">${ready.length ? ready.map(queueRow).join("") : `<div class="empty">Nothing waiting. Press Run now, or ask the chat to “prepare my top 3”.</div>`}</div>
    ${done.length ? `<details class="small" style="margin-top:8px"><summary class="muted" style="cursor:pointer">Done, skipped or closed this week (${done.length})</summary><div class="card joblist" style="margin-top:8px">${done.map(queueRow).join("")}</div></details>` : ""}

    <div class="section-title"><h2>This week</h2></div>
    <div class="ap-week">
      <div class="card card-pad stack">
        <h3>Goal</h3>
        ${w.goal ? `<div class="row" style="gap:14px">${goalRing(goalPct)}<div><b>${w.applied} of ${w.goal}</b> applied<div class="muted small">${w.ready} ready · ${w.days_left} Autopilot day${w.days_left === 1 ? "" : "s"} left</div></div></div>`
          : `<div class="muted small">Set a weekly goal and Autopilot paces itself to hit it.</div><button class="btn btn-ghost btn-sm" id="ap-set-goal">Set a goal</button>`}
      </div>
      <div class="card card-pad stack">
        <h3>Follow-ups & prep</h3>
        ${d.drafts.length ? d.drafts.slice(0, 6).map((x) => `<div class="draft-row"><span class="tag ${x.kind === "prep" ? "good" : ""}">${{ followup: "Follow-up", thankyou: "Thank-you", prep: "Prep", research: "Research", message: "Message" }[x.kind]}</span>
          <span class="small" style="flex:1;min-width:0">${esc(x.title)}</span>
          <button class="icon-btn" data-draft-view="${x.id}" title="Open">↗</button><button class="icon-btn" data-draft-copy="${x.id}" title="Copy">⧉</button><button class="icon-btn" data-draft-done="${x.id}" title="Done">✓</button></div>`).join("")
          : '<div class="muted small">Drafts for quiet applications, thank-yous after interviews and interview prep sheets show up here.</div>'}
      </div>
      <div class="card card-pad stack">
        <h3>Events you saved</h3>
        ${d.events.length ? d.events.map((e) => `<div class="small"><b>${new Date(e.start_ts * 1000).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</b> · ${e.url ? `<a href="${safeUrl(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}</div>`).join("")
          : '<div class="muted small">Nothing saved for the next 7 days.</div>'}
        <a href="#" class="small" id="ap-events">All events →</a>
      </div>
    </div>

    <details class="card card-pad" style="margin-top:16px"><summary style="cursor:pointer"><b>History & activity</b> <span class="muted small">every run, and everything the AI changed (with Undo)</span></summary>
      <div class="grid-2" style="margin-top:12px">
        <div class="stack">${d.history.map((h) => `<details class="small"><summary style="cursor:pointer">${new Date(h.started_at * 1000).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · ${esc(h.trigger)} · prepared ${h.queued || 0}${h.error ? " · ⚠️" : ""}</summary><div class="ap-brief" style="margin:6px 0 0 12px">${esc(h.brief || h.error || "").replace(/\n/g, "<br>")}</div></details>`).join("") || '<div class="muted small">No runs yet.</div>'}</div>
        <div>${d.activity.length ? `<table class="ap-activity"><tbody>${d.activity.map((a) => `<tr>
            <td class="muted small" style="white-space:nowrap">${esc(ago(a.ts))}</td><td class="small" style="white-space:nowrap">${esc(SOURCE_LABEL[a.source] || a.source)}</td>
            <td class="small">${a.ok ? "" : "⚠️ "}${esc(a.summary || a.tool)}</td>
            <td style="text-align:right">${a.can_undo ? `<button class="btn btn-ghost btn-sm" data-undo-act="${a.id}">Undo</button>` : a.undone ? '<span class="muted small">undone</span>' : ""}</td></tr>`).join("")}</tbody></table>`
          : `<div class="muted small">Nothing yet.</div>`}</div>
      </div></details>`;

  $("#ap-on").addEventListener("change", (e) => saveAP({ on: e.target.checked }).then(renderAutopilot));
  $("#ap-settings").addEventListener("click", openAPSettings);
  $("#ap-set-goal")?.addEventListener("click", openAPSettings);
  $("#ap-engine").addEventListener("click", (e) => { e.preventDefault(); openSettings("ai"); });
  $("#ap-events").addEventListener("click", (e) => { e.preventDefault(); switchTab("events"); });
  $("#ap-preview").addEventListener("click", openPreview);
  $("#ap-session")?.addEventListener("click", () => applySession(ready));
  $("#ap-run").addEventListener("click", async () => {
    await api("/api/autopilot/run", { method: "POST", json: { profile_id: S.pid } }).catch((e) => toast(e.message, 5000));
    $("#ap-run").disabled = true; $("#ap-run").innerHTML = '<span class="spinner"></span> Running…';
    pollAutopilot();
  });
  main.querySelectorAll("[data-undo-act]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/activity/${b.dataset.undoAct}/undo`, { method: "POST", json: { profile_id: S.pid } }); toast("Undone"); renderAutopilot(); }
    catch (e) { toast(e.message, 5000); }
  }));
  const draft = (id) => d.drafts.find((x) => String(x.id) === String(id));
  main.querySelectorAll("[data-draft-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(draft(b.dataset.draftCopy).content); toast("Copied. Paste it into your email"); } catch { toast("Couldn't copy"); }
  }));
  main.querySelectorAll("[data-draft-view]").forEach((b) => b.addEventListener("click", () => viewDraft(draft(b.dataset.draftView))));
  main.querySelectorAll("[data-draft-done]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/drafts/${b.dataset.draftDone}?profile_id=${S.pid}`, { method: "DELETE" }); renderAutopilot();
  }));
  bindQueueRows(main);
  if (run.running) pollAutopilot();
}

function briefBlock(brief) {  // the first paragraph; the rest (it repeats the cards below) folds away
  const fmt = (t) => linkify(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
  const [head, ...rest] = brief.trim().split(/\n\s*\n/);
  return `<div class="ap-brief">${fmt(head)}${rest.length ? `<details><summary class="muted small">Full brief</summary><div style="margin-top:6px">${fmt(rest.join("\n\n"))}</div></details>` : ""}</div>`;
}

function goalRing(pct) {
  const r = 22, c = 2 * Math.PI * r;
  return `<svg viewBox="0 0 54 54" width="54" height="54"><circle cx="27" cy="27" r="${r}" fill="none" stroke="var(--border)" stroke-width="5"/>
    <circle cx="27" cy="27" r="${r}" fill="none" stroke="var(--good)" stroke-width="5" stroke-linecap="round" stroke-dasharray="${(c * pct / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 27 27)"/>
    <text x="27" y="31" text-anchor="middle" font-size="12" fill="var(--text)" font-weight="700">${pct}%</text></svg>`;
}

function saveAP(change) {
  return api("/api/autopilot", { method: "PUT", json: { profile_id: S.pid, ...change } }).then((r) => { toast("Saved"); return r; }).catch((e) => toast(e.message, 5000));
}

function queueRow(q) {
  const live = q.status === "ready" || q.status === "opened";
  const fw = AP.data?.settings.fill_with || "chrome";
  return `<div class="job ap-row" data-q="${esc(q.job_id)}">
    ${scoreRing(q.score)}
    <div class="job-main" style="min-width:0;flex:1">
      <div class="job-title"><a href="${safeUrl(q.url)}" target="_blank" rel="noopener">${esc(q.title)}</a></div>
      <div class="muted small">${esc(q.company)} · ${esc(q.where || "")} · prepared ${esc(ago(q.created_at))} by ${esc(SOURCE_LABEL[q.source] || q.source || "you")}${q.status === "opened" ? ' · <span class="tag warn">opened</span>' : ""}${q.status === "applied" ? ' · <span class="tag good">applied</span>' : ""}${q.status === "skipped" ? ` · <span class="tag">${esc(q.note || "skipped")}</span>` : ""}</div>
      ${q.why ? `<div class="small" style="margin-top:2px">${esc(q.why)}</div>` : ""}
    </div>
    <div class="row ap-btns">${live ? `
      <button class="btn btn-sm" data-qa="kit">Review</button>
      <button class="btn btn-ghost btn-sm" data-qa="open" title="Open it with ${esc(FILL_LABEL[fw])}. You press Submit.">${esc(FILL_LABEL[fw])}</button>
      <details class="cal"><summary class="btn btn-ghost btn-sm" title="More">⋯</summary><div class="cal-menu">
        <a href="#" data-qa="applied">✓ I submitted it</a>
        ${Object.entries(FILL_LABEL).filter(([k]) => k !== fw).map(([k, l]) => `<a href="#" data-qa="open" data-via="${k}">Open with ${esc(l)}</a>`).join("")}
        <a href="#" data-qa="skip">Not for me</a></div></details>` : `<button class="btn btn-ghost btn-sm" data-qa="restore">Put back</button>`}</div>
  </div>`;
}

async function openWith(id, via) {
  try {
    const r = await api(`/api/apply/${encodeURIComponent(id)}/open`, { method: "POST", json: { profile_id: S.pid, via } });
    if (r.url) window.open(r.url, "_blank", "noopener");
    toast(r.message || "Opened", 7000);
  } catch (e) { toast(e.message, 6000); }
}

function askSkipReason(jobId, after) {
  const root = $("#modal-root");
  const reasons = AP.data?.skip_reasons || {};
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack" style="max-width:420px">
    <h3>Not for me, because…</h3><div class="muted small">Autopilot learns from this.</div>
    <div class="chips">${Object.entries(reasons).map(([k, l]) => `<button class="chip" data-reason="${k}">${esc(l)}</button>`).join("")}</div>
    <div class="row"><span class="spacer"></span><button class="btn btn-ghost btn-sm" data-reason="">Just skip</button></div></div></div>`;
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) root.innerHTML = ""; });
  root.querySelectorAll("[data-reason]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/queue/${encodeURIComponent(jobId)}/status`, { method: "POST", json: { profile_id: S.pid, status: "skipped", reason: b.dataset.reason } });
    root.innerHTML = "";
    if (b.dataset.reason === "company") toast("Got it: Autopilot won't pick that company again");
    after?.();
  }));
}

function bindQueueRows(root) {
  root.querySelectorAll(".ap-row [data-qa]").forEach((b) => b.addEventListener("click", async (e) => {
    e.preventDefault();
    const id = b.closest("[data-q]").dataset.q, act = b.dataset.qa;
    try {
      if (act === "kit") return openKit(id);
      if (act === "open") { b.closest("details")?.removeAttribute("open"); return openWith(id, b.dataset.via); }
      if (act === "skip") return askSkipReason(id, renderAutopilot);
      if (act === "applied") { await api(`/api/queue/${encodeURIComponent(id)}/status`, { method: "POST", json: { profile_id: S.pid, status: "applied" } }); toast("Marked applied. Good luck!"); loadState(); }
      if (act === "restore") await api(`/api/queue/${encodeURIComponent(id)}/status`, { method: "POST", json: { profile_id: S.pid, status: "ready" } });
      renderAutopilot();
    } catch (err) { toast(err.message, 6000); }
  }));
}

// ---------- apply session: one prepared application after another ----------
function applySession(list) {
  const items = [...list].sort((a, b) => b.score - a.score);
  let i = 0;
  const root = $("#modal-root");
  const draw = () => {
    if (i >= items.length) { root.innerHTML = ""; toast("Session done. Nice work!"); return renderAutopilot(); }
    const q = items[i], fw = AP.data.settings.fill_with;
    root.innerHTML = `<div class="modal-bg"><div class="modal card stack" style="max-width:560px">
      <div class="row"><span class="muted small">Apply session · ${i + 1} of ${items.length}</span><span class="spacer"></span><button class="icon-btn" id="as-end" title="End session">✕</button></div>
      <div class="row" style="gap:12px">${scoreRing(q.score)}<div style="min-width:0"><h2 style="margin:0">${esc(q.company)}</h2><div>${esc(q.title)}</div><div class="muted small">${esc(q.where || "")}</div></div></div>
      ${q.why ? `<div class="small">${esc(q.why)}</div>` : ""}
      <div class="row" style="flex-wrap:wrap;gap:8px">
        <button class="btn" id="as-open">${esc(FILL_LABEL[fw])}</button>
        <button class="btn btn-ghost" id="as-kit">Review kit</button>
      </div>
      <div class="muted small">Check every field, then press Submit on the form yourself.</div>
      <div class="row"><button class="btn btn-ghost btn-sm" id="as-skip">Not for me</button><button class="btn btn-ghost btn-sm" id="as-next">Later</button><span class="spacer"></span><button class="btn" id="as-done">✓ Submitted, next</button></div>
    </div></div>`;
    $("#as-end").addEventListener("click", () => { root.innerHTML = ""; renderAutopilot(); });
    $("#as-open").addEventListener("click", () => openWith(q.job_id));
    $("#as-kit").addEventListener("click", () => { openKit(q.job_id); });
    $("#as-next").addEventListener("click", () => { i++; draw(); });
    $("#as-skip").addEventListener("click", () => askSkipReason(q.job_id, () => { i++; draw(); }));
    $("#as-done").addEventListener("click", async () => {
      await api(`/api/queue/${encodeURIComponent(q.job_id)}/status`, { method: "POST", json: { profile_id: S.pid, status: "applied" } }).catch((e) => toast(e.message));
      loadState(); i++; draw();
    });
  };
  draw();
}

// ---------- preview, drafts ----------
async function openPreview() {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide"><div class="empty"><span class="spinner"></span> Looking at your matches…</div></div></div>`;
  const p = await api(`/api/autopilot/preview?profile_id=${S.pid}`);
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide stack">
    <div class="row"><h2>If Autopilot ran now</h2><span class="spacer"></span><button class="icon-btn" id="pv-close">✕</button></div>
    <div class="muted small">It would prepare <b>${p.would_prepare}</b>${p.week.goal ? ` (weekly goal: ${p.week.applied}/${p.week.goal} applied, ${p.week.ready} ready)` : ""}. Nothing is prepared until it runs.</div>
    <div class="card joblist">${p.picks.map((x) => `<div class="job" style="grid-template-columns:56px 1fr">${scoreRing(x.score)}<div>
      <div class="row" style="gap:6px"><b>${esc(x.company)}</b>${x.chosen ? '<span class="tag good">would prepare</span>' : '<span class="tag">next in line</span>'}</div>
      <div>${esc(x.title)}</div><div class="muted small">${esc(x.where)} · ${esc(x.why.join(" · "))}</div></div></div>`).join("") || '<div class="empty">Nothing fits your Autopilot settings right now. Try a lower minimum score or fewer filters.</div>'}</div>
    <div class="row"><span class="spacer"></span><button class="btn btn-ghost" id="pv-settings">Change settings</button></div>
  </div></div>`;
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) root.innerHTML = ""; });
  $("#pv-close").addEventListener("click", () => (root.innerHTML = ""));
  $("#pv-settings").addEventListener("click", openAPSettings);
}

function viewDraft(x) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide stack">
    <div class="row"><h2>${esc(x.title)}</h2><span class="spacer"></span><button class="icon-btn" id="dv-close">✕</button></div>
    <pre class="kit-pre" style="max-height:60vh">${esc(x.content)}</pre>
    <div class="row"><span class="muted small">Drafted by ${esc(byLabel(x.generated_by))}. Edit it before you send it. Co-op Coach never sends emails.</span><span class="spacer"></span><button class="btn" id="dv-copy">Copy</button></div>
  </div></div>`;
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) root.innerHTML = ""; });
  $("#dv-close").addEventListener("click", () => (root.innerHTML = ""));
  $("#dv-copy").addEventListener("click", async () => { try { await navigator.clipboard.writeText(x.content); toast("Copied"); } catch { toast("Couldn't copy"); } });
}

// ---------- settings drawer ----------
async function openAPSettings() {
  const st = AP.data.settings, tiers = AP.data.tiers || {};
  const fmts = await api(`/api/letters/formats?profile_id=${S.pid}`).catch(() => ({ formats: [] }));
  const root = $("#modal-root");
  const chk = (k, l, hint = "") => `<label class="toggle" ${hint ? `title="${esc(hint)}"` : ""}><input type="checkbox" data-k="${k}" ${st[k] ? "checked" : ""}> ${l}</label>`;
  const sel = (k, opts) => `<select data-k="${k}" data-num="1">${opts.map(([v, l]) => `<option value="${v}" ${String(st[k]) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  root.innerHTML = `<div class="modal-bg"><form class="modal card wide stack ap-drawer" id="aps">
    <div class="row"><h2>Autopilot settings</h2><span class="spacer"></span><button type="button" class="icon-btn" id="aps-close">✕</button></div>

    <section><h3>When</h3>
      <div class="row"><div class="chips">${DAY_NAMES.map((n, i) => `<button type="button" class="chip ${st.days.includes(i) ? "on" : ""}" data-day="${i}">${n}</button>`).join("")}</div>
        <span class="muted small">at</span><input type="time" data-k="time" value="${esc(st.time)}" style="width:auto"></div>
      <div class="row">${chk("instant", "⚡ Also prepare a just-posted match right after each sync when it scores ≥")} ${sel("instant_score", [80, 85, 88, 90, 93, 95].map((n) => [n, n]))}</div>
      <div class="row"><span class="small">Pause until</span><input type="date" data-k="paused_until" value="${esc(st.paused_until || "")}" style="width:auto"><span class="muted small">(exams, travel). Empty = not paused.</span></div>
    </section>

    <section><h3>What to pick</h3>
      <div class="row"><span class="small">Prepare up to</span>${sel("per_day", [1, 2, 3, 4, 5, 6, 8, 10].map((n) => [n, n]))}<span class="small">per run, scoring at least</span>${sel("min_score", [50, 60, 65, 70, 75, 80, 85].map((n) => [n, n]))}</div>
      <div class="row"><span class="small">Weekly goal</span><input type="number" min="0" max="50" data-k="weekly_goal" data-num="1" value="${st.weekly_goal || 0}" style="width:80px"><span class="muted small">applications (0 = off). Autopilot paces itself to hit it.</span></div>
      <div class="row"><span class="small">Who it's open to</span><div class="seg">${[["fit", "Clearly open to me"], ["open", "Open or not stated"], ["any", "Also “check eligibility”"]].map(([v, l]) => `<button type="button" data-elig="${v}" class="${st.eligibility === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div class="row"><span class="small">Posted within</span>${sel("posted_within", [[0, "any time"], [3, "3 days"], [7, "a week"], [14, "2 weeks"], [30, "a month"]])}${chk("favourites_first", "Companies I follow first")}</div>
      <div><span class="filter-label">Roles</span> <span class="muted small">(none = your profile's)</span><div class="chips">${S.app.categories.map((c) => `<button type="button" class="chip ${st.categories.includes(c) ? "on" : ""}" data-cat="${esc(c)}">${esc(c)}</button>`).join("")}</div></div>
      <div><span class="filter-label">Terms</span><div class="chips">${S.app.terms.map((t) => `<button type="button" class="chip ${st.terms.includes(t) ? "on" : ""}" data-term="${esc(t)}">${esc(t)}</button>`).join("")}</div></div>
      <label class="field">Places <span class="hint">comma separated; empty = anywhere you'd go</span><input type="text" data-list="locations" value="${esc(st.locations.join(", "))}" placeholder="Toronto, Waterloo, Remote"></label>
      <div><span class="filter-label">Company mix per run</span> <span class="muted small">e.g. 1 big tech + 1 startup; the rest by score</span>
        <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:6px">${Object.entries(tiers).filter(([k]) => k !== "other").map(([k, l]) => `<label class="small">${esc(l)} <input type="number" min="0" max="10" data-mix="${k}" value="${st.mix[k] || 0}" style="width:56px"></label>`).join("")}</div></div>
      <label class="field">Never pick <span class="hint">companies, comma separated (“Not for me → company” adds here)</span><input type="text" data-list="blocklist" value="${esc(st.blocklist.join(", "))}"></label>
    </section>

    <section><h3>What to prepare</h3>
      <div class="row">${chk("letter", "Cover letter")} <span class="small">style</span><select data-k="letter_style"><option value="">My default</option>${fmts.formats.map((f) => `<option value="${esc(f.id)}" ${st.letter_style === f.id ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select></div>
      <div class="row">${chk("tailor", "Tailored resume for each job")} ${chk("ai_answers", "AI drafts for written questions")} ${chk("prerender", "Make the PDFs ahead", "So filling the form is instant")}</div>
    </section>

    <section><h3>How you apply</h3>
      <div class="seg">${Object.entries(FILL_LABEL).map(([k, l]) => `<button type="button" data-fill="${k}" class="${st.fill_with === k ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="muted small">Used by the main button and the apply session. Nothing ever presses Submit for you.</div>
    </section>

    <section><h3>Follow-ups, prep & events</h3>
      <div class="row">${chk("followups", "Draft a follow-up when an application is quiet for")} <input type="number" min="7" max="60" data-k="followup_days" data-num="1" value="${st.followup_days}" style="width:64px"> <span class="small">days</span></div>
      <div class="row">${chk("thankyou", "Draft a thank-you after interviews")} ${chk("prep", "Write interview / OA prep sheets")}</div>
      <div class="row">${chk("auto_events", "Save events from companies I follow and hackathons near me")}</div>
      <div class="row"><span class="small">Drop prepared applications I haven't opened after</span><input type="number" min="3" max="30" data-k="expire_days" data-num="1" value="${st.expire_days}" style="width:64px"><span class="small">days</span></div>
    </section>

    <section><h3>Alerts & brain</h3>
      <div class="row">${chk("notify", "Alert me when it's done")} <span class="small">Quiet hours</span><input type="time" data-k="quiet_from" value="${esc(st.quiet_from || "")}" style="width:auto"><span class="small">to</span><input type="time" data-k="quiet_to" value="${esc(st.quiet_to || "")}" style="width:auto"></div>
      <div class="row"><span class="small">Brain</span><div class="seg">${[["auto", "Same as the app"], ["claude_code", "Claude Code"], ["codex", "ChatGPT"], ["rules", "Built-in rules"]].map(([v, l]) => `<button type="button" data-eng="${v}" class="${st.engine === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
    </section>

    <div class="row"><span class="spacer"></span><button type="button" class="btn btn-ghost" id="aps-cancel">Cancel</button><button class="btn">Save</button></div>
  </form></div>`;
  const form = $("#aps");
  const close = () => (root.innerHTML = "");
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#aps-close").addEventListener("click", close);
  $("#aps-cancel").addEventListener("click", close);
  form.querySelectorAll("[data-day],[data-cat],[data-term]").forEach((b) => b.addEventListener("click", () => b.classList.toggle("on")));
  const one = (attr) => form.querySelectorAll(`[${attr}]`).forEach((b) => b.addEventListener("click", () => { form.querySelectorAll(`[${attr}]`).forEach((x) => x.classList.remove("on")); b.classList.add("on"); }));
  ["data-elig", "data-fill", "data-eng"].forEach(one);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = {};
    form.querySelectorAll("[data-k]").forEach((el) => { out[el.dataset.k] = el.type === "checkbox" ? el.checked : el.dataset.num ? Number(el.value) : el.value; });
    form.querySelectorAll("[data-list]").forEach((el) => { out[el.dataset.list] = el.value.split(",").map((x) => x.trim()).filter(Boolean); });
    out.days = [...form.querySelectorAll("[data-day].on")].map((b) => Number(b.dataset.day));
    out.categories = [...form.querySelectorAll("[data-cat].on")].map((b) => b.dataset.cat);
    out.terms = [...form.querySelectorAll("[data-term].on")].map((b) => b.dataset.term);
    out.mix = Object.fromEntries([...form.querySelectorAll("[data-mix]")].map((el) => [el.dataset.mix, Number(el.value) || 0]).filter(([, n]) => n > 0));
    out.eligibility = form.querySelector("[data-elig].on")?.dataset.elig || "any";
    out.fill_with = form.querySelector("[data-fill].on")?.dataset.fill || "chrome";
    out.engine = form.querySelector("[data-eng].on")?.dataset.eng || "auto";
    await saveAP(out);
    close(); renderAutopilot();
  });
}

function setReadyCount(n) {
  const el = $("#ready-count");
  if (el) el.textContent = n ? String(n) : "";
}

function pollAutopilot() {
  clearTimeout(pollAutopilot._t);
  pollAutopilot._t = setTimeout(async () => {
    if (S.tab !== "autopilot") return;
    const d = await api(`/api/autopilot?profile_id=${S.pid}`).catch(() => null);
    if (!d) return;
    if (d.state.running) { const p = $("#ap-progress"); if (p) p.innerHTML = `<span class="spinner"></span> ${esc(d.state.step || "Working…")}`; pollAutopilot(); }
    else renderAutopilot();
  }, 3000);
}
