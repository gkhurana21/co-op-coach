/* Co-op Coach front end: plain JS, no build step. */

const S = {
  app: null,                 // /api/state payload
  pid: null,                 // active profile id
  tab: "matches",
  filters: { q: "", tiers: [], categories: [], region: "", term: "", since_days: 0, min_score: 0, sort: "match", hide_applied: false, show_ineligible: false, starred: false },
  jobs: { jobs: [], pinned: [], total: 0, facets: { tiers: {}, categories: {} }, offset: 0 },
  loading: false,
};

const LS = {
  get(k, d) { try { const v = localStorage.getItem("cc." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("cc." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
const TIER_LABEL = () => (S.app ? S.app.tiers : {});
const STATUS_LABEL = { saved: "Saved", applied: "Applied", oa: "Online assessment", interviewing: "Interviewing", offer: "Offer 🎉", rejected: "Rejected" };
const KIND_LABEL = { coffee_chat: "Coffee chat", info_session: "Info session", career_fair: "Career fair", networking: "Networking", workshop: "Workshop", hackathon: "Hackathon", talk: "Talk / panel", other: "Event" };
const AVATAR_COLORS = ["#4f46e5", "#0f766e", "#c2410c", "#be185d", "#2563eb", "#7c3aed"];

async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (opts.json !== undefined) { init.body = JSON.stringify(opts.json); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, init);
  if (r.status === 423) {  // that person is locked: show the passcode screen
    const b = await r.json().catch(() => ({}));
    const p = S.app?.profiles.find((x) => x.id === b.locked);
    if (p) p.locked = true;
    if (b.locked === S.pid) render();
    else if (p) promptUnlock(p.id, p.name);
    throw new Error(`${b.name || "That profile"} is locked`);
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { const b = await r.json(); msg = b.detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

function ago(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 0) return "soon";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : d < 30 ? `${d}d ago` : `${Math.round(d / 30)}mo ago`;
}
function until(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return "now";
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}
const profile = () => (S.app?.profiles || []).find((p) => p.id === S.pid);
const plural = (n, w) => (n === 1 ? w : w + "s");
const AI_NAMES = { claude: "Claude", groq: "Groq AI", claude_code: "your Claude Code", codex: "your ChatGPT" };
const byLabel = (by) => AI_NAMES[by] || (by === "you" ? "You" : by === "rules" ? "built-in rules" : "Template");
const aiName = () => AI_NAMES[S.app?.ai_provider] || "AI";
const initials = (n) => (n || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function scoreRing(score) {
  const r = 21, c = 2 * Math.PI * r, pct = Math.max(0, Math.min(100, score)) / 100;
  const color = score >= 75 ? "var(--good)" : score >= 55 ? "var(--accent)" : score >= 35 ? "var(--warn)" : "var(--muted)";
  return `<div class="score" title="Match score">
    <svg viewBox="0 0 50 50" width="100%" height="100%"><circle cx="25" cy="25" r="${r}" fill="none" stroke="var(--border)" stroke-width="4"/>
    <circle cx="25" cy="25" r="${r}" fill="none" stroke="${color}" stroke-width="4" stroke-linecap="round" stroke-dasharray="${(c * pct).toFixed(1)} ${c.toFixed(1)}"/></svg>
    <div class="num">${score}</div></div>`;
}
const starIcon = (on) => `<svg viewBox="0 0 24 24" width="16" height="16" fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></svg>`;
const heartIcon = (on) => `<svg viewBox="0 0 24 24" width="16" height="16" fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/></svg>`;
const extIcon = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 4h6v6"/><path d="M10 14 20 4"/><path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5"/></svg>`;

// ---------- boot ----------
async function loadState() {
  const wasLocked = profile()?.locked;
  S.app = await api("/api/state");
  const ids = S.app.profiles.map((p) => p.id);
  if (!ids.includes(S.pid)) S.pid = ids.includes(LS.get("pid")) ? LS.get("pid") : ids[0] ?? null;
  renderProfiles();
  renderSync();
  renderAppPill();
  renderLockButton();
  if (S.booted && wasLocked !== undefined && profile()?.locked !== wasLocked) render();  // auto-lock kicked in
}

function renderLockButton() {
  const p = profile();
  $("#lock-btn").classList.toggle("hidden", !(p && p.has_passcode && !p.locked));
}

async function lockNow() {
  await api("/api/auth/lock", { method: "POST" });
  $("#chat-panel").classList.add("hidden");
  await loadState();
  render();
}

async function boot() {
  S.tab = OLD_TABS[LS.get("tab", "autopilot")] || LS.get("tab", "autopilot");
  const saved = LS.get("filters", null);
  if (saved) S.filters = { ...S.filters, ...saved };
  await loadState();
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("#sync-btn").addEventListener("click", triggerSync);
  $("#lock-btn").addEventListener("click", lockNow);
  $("#theme-btn").addEventListener("click", toggleTheme);
  paintThemeButton();
  S.booted = true;
  if (!S.app.profiles.length) return onboarding();
  initChat();
  switchTab(S.tab);
  setInterval(() => { if (!document.hidden) pollSync(); }, 60000);  // the data can live in the cloud: poll gently
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollSync(); });
}

function toggleTheme() {
  const light = document.documentElement.dataset.theme !== "light";
  if (light) document.documentElement.dataset.theme = "light"; else delete document.documentElement.dataset.theme;
  LS.set("theme", light ? "light" : "black");
  paintThemeButton();
}
function paintThemeButton() {
  const light = document.documentElement.dataset.theme === "light";
  $("#theme-btn").textContent = light ? "☾" : "☀︎";
  $("#theme-btn").title = light ? "Switch to black" : "Switch to light";
}

const OLD_TABS = { coach: "settings", sources: "settings", favourites: "matches" };

function switchTab(tab) {
  if (tab === "favourites") S.filters.starred = true;
  tab = OLD_TABS[tab] || tab;
  S.tab = tab;
  LS.set("tab", tab);
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  render();
}

function render() {
  if (!S.pid) return onboarding();
  const locked = !!profile()?.locked;
  $("#chat-fab").classList.toggle("hidden", locked || !initChat.done);
  if (locked) { $("#chat-panel").classList.add("hidden"); $("#modal-root").innerHTML = ""; return renderLockScreen(); }
  ({ autopilot: renderAutopilot, matches: renderMatches, tracker: renderTracker, documents: renderDocuments, events: renderEvents, settings: renderSettings }[S.tab] || renderMatches)();
}

// ---------- header ----------
function renderProfiles() {
  const el = $("#profiles");
  if (S.app.demo && !$("#demo-tag")) $(".brand").insertAdjacentHTML("beforeend", '<span class="tag warn" id="demo-tag" title="A fictional student and a snapshot of real postings: python -m coopcoach demo">Demo</span>');
  el.innerHTML = S.app.profiles.map((p, i) => `
    <button class="profile-pill ${p.id === S.pid ? "active" : ""}" data-id="${p.id}" title="${p.locked ? "Locked: needs passcode" : ""}">
      <span class="avatar" style="background:${AVATAR_COLORS[i % AVATAR_COLORS.length]}">${esc(initials(p.name))}</span>${esc(p.name)}${p.locked ? " 🔒" : ""}
    </button>`).join("") + `<button class="profile-pill add" id="add-profile" title="Add a person (their own resume, matches and passcode)" aria-label="Add a person">+</button>`;
  el.querySelectorAll("[data-id]").forEach((b) => b.addEventListener("click", () => {
    S.pid = Number(b.dataset.id); LS.set("pid", S.pid); S.jobs.offset = 0; renderProfiles(); renderAppPill(); renderLockButton(); render();
  }));
  $("#add-profile").addEventListener("click", () => askPerson("Add a person", "Each person gets their own resume, matches, favourites, alerts and passcode.", async (name, passcode) => {
    const p = await api("/api/profiles", { method: "POST", json: { name, passcode } });
    S.pid = p.id; LS.set("pid", p.id); await loadState(); openSettings("profile");
  }));
}

function renderAppPill() {  // the Applications tab carries the count; the stats live on that tab
  const p = profile();
  if (typeof setReadyCount === "function") setReadyCount(p && !p.locked ? p.ready_count || 0 : 0);
  const st = p?.app_stats;
  $("#app-pill").classList.add("hidden");
  $("#app-count").textContent = st?.applied_total || "";
}

function renderSync() {
  const s = S.app.sync;
  const el = $("#sync-status");
  const btn = $("#sync-btn");
  if (s.running) {
    el.innerHTML = `<span class="spinner"></span> ${esc(s.progress || "syncing")}…`;
    btn.disabled = true;
  } else {
    btn.disabled = false;
    el.textContent = "";  // quiet when idle: the button says when it last ran
    btn.title = s.last_finished ? `Synced ${ago(s.last_finished)}${s.next_run ? ` · next in ${until(s.next_run)}` : ""}. Click to check now.` : "Never synced. Click to check now.";
  }
}

async function triggerSync() {
  await api("/api/sync", { method: "POST" });
  toast("Sync started: checking every source for new postings");
  S.app.sync.running = true;
  renderSync();
  setTimeout(pollSync, 3000);
}

async function pollSync() {
  const wasRunning = S.app?.sync?.running;
  try { await loadState(); } catch { return; }
  if (S.app.sync.running) setTimeout(pollSync, 3000);
  else if (wasRunning) {
    const r = S.app.sync.last_report || {};
    toast(`Sync done: ${r.new_jobs ?? 0} new postings, ${r.new_events ?? 0} new events`);
    if (["matches", "events", "settings", "autopilot"].includes(S.tab)) render();
  }
}

// ---------- onboarding / modals ----------
function askPerson(title, sub, onSubmit) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><form class="modal card">
      <div><h2>${esc(title)}</h2><p class="muted" style="margin:6px 0 0">${esc(sub)}</p></div>
      <input type="text" name="name" placeholder="Name, e.g. Gk" required maxlength="40" autocomplete="off">
      <input type="password" name="pc" placeholder="Passcode (4+ characters)" required minlength="4" autocomplete="new-password">
      <input type="password" name="pc2" placeholder="Confirm passcode" required minlength="4" autocomplete="new-password">
      <p class="muted small" style="margin:0">The passcode locks this person's resume, tracker and saved logins, so friends sharing this laptop can't open each other's. It auto-locks after ${S.app?.auto_lock_minutes ?? 30} idle minutes.</p>
      <div class="row"><span class="spacer"></span>${S.app.profiles.length ? '<button type="button" class="btn btn-ghost" data-close>Cancel</button>' : ""}<button class="btn">Continue</button></div>
    </form></div>`;
  const form = root.querySelector("form");
  root.querySelector("[data-close]")?.addEventListener("click", () => (root.innerHTML = ""));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = form.name.value.trim();
    if (!name) return;
    if (form.pc.value !== form.pc2.value) return toast("Passcodes don't match");
    try { await onSubmit(name, form.pc.value); root.innerHTML = ""; } catch (err) { toast(err.message); }
  });
  form.name.focus();
}

function askPasscode(title, sub) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-bg"><form class="modal card">
        <div><h2>${esc(title)}</h2><p class="muted" style="margin:6px 0 0">${esc(sub)}</p></div>
        <input type="password" name="pc" placeholder="Passcode" required autocomplete="current-password">
        <div class="row"><span class="spacer"></span><button type="button" class="btn btn-ghost" data-close>Cancel</button><button class="btn">Continue</button></div>
      </form></div>`;
    const form = root.querySelector("form");
    root.querySelector("[data-close]").addEventListener("click", () => { root.innerHTML = ""; resolve(null); });
    form.addEventListener("submit", (e) => { e.preventDefault(); const v = form.pc.value; root.innerHTML = ""; resolve(v); });
    form.pc.focus();
  });
}

async function unlockProfile(pid, passcode) {
  await api("/api/auth/unlock", { method: "POST", json: { profile_id: pid, passcode } });
  S.pid = pid; LS.set("pid", pid);
  await loadState();
  initChat();
  render();
}

function promptUnlock(pid, name) {
  askPasscode(`Unlock ${name}`, "Enter this person's passcode.").then(async (code) => {
    if (code === null) return;
    try { await unlockProfile(pid, code); } catch (e) { toast(e.message, 4000); }
  });
}

function renderLockScreen() {
  const p = profile();
  const i = S.app.profiles.indexOf(p);
  $("#main").innerHTML = `<form class="card card-pad stack" id="unlock-form" style="max-width:380px;margin:60px auto;text-align:center">
      <div style="display:grid;place-items:center"><span class="avatar" style="width:56px;height:56px;border-radius:50%;display:grid;place-items:center;color:white;font-weight:700;font-size:20px;background:${AVATAR_COLORS[i % AVATAR_COLORS.length]}">${esc(initials(p.name))}</span></div>
      <h2>🔒 ${esc(p.name)} is locked</h2>
      <input type="password" id="unlock-pc" placeholder="Passcode" autocomplete="current-password" required style="text-align:center">
      <button class="btn" style="justify-content:center">Unlock</button>
      <div class="muted small" id="unlock-err"></div>
      <details class="small muted"><summary style="cursor:pointer">Forgot it?</summary><p style="margin:6px 0 0">On this computer, run<br><code>.venv/bin/python -m coopcoach reset-passcode "${esc(p.name)}"</code><br>in the coop-coach folder, then set a new one in Settings.</p></details>
    </form>`;
  $("#unlock-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await unlockProfile(p.id, $("#unlock-pc").value); }
    catch (err) { $("#unlock-err").textContent = err.message; $("#unlock-pc").value = ""; $("#unlock-pc").focus(); }
  });
  $("#unlock-pc").focus();
}

function onboarding() {
  $("#main").innerHTML = `<div class="card card-pad" style="max-width:620px;margin:40px auto;text-align:center">
    <h2 style="font-size:22px">Never miss an internship drop again</h2>
    <p class="muted">Co-op Coach pulls internships and co-ops from SimplifyJobs, company career sites (Greenhouse, Lever, Ashby, Workday),
    LinkedIn and Indeed, ranks them against your resume, and pings you when good ones open.</p></div>`;
  askPerson("Who's job hunting?", "Start with your name and a passcode. You can add your friend after.", async (name, passcode) => {
    const p = await api("/api/profiles", { method: "POST", json: { name, passcode } });
    S.pid = p.id; LS.set("pid", p.id); await loadState(); openSettings("profile");
    initChat();
    setInterval(() => { if (!document.hidden) pollSync(); }, 60000);
  });
}

// ---------- Matches ----------
function jobQuery(extra = {}) {
  const f = { ...S.filters, ...extra };
  const p = new URLSearchParams({ profile_id: S.pid, q: f.q, tiers: f.tiers.join(","), categories: f.categories.join(","), region: f.region, term: f.term, since_days: f.since_days, min_score: f.min_score, sort: f.sort, hide_applied: f.hide_applied, show_ineligible: f.show_ineligible, starred: !!f.starred, limit: 50, offset: extra.offset ?? 0 });
  return "/api/jobs?" + p.toString();
}

async function renderMatches() {
  const main = $("#main");
  const p = profile();
  const st = S.app;
  const needsResume = p && !p.resume_filename;
  main.innerHTML = `
    ${needsResume ? `<div class="banner"><b>Add ${esc(p.name)}'s resume</b><span class="muted">Matches are generic until then.</span><span class="spacer"></span><button class="btn btn-sm" id="go-coach">Add resume</button></div>` : ""}
    ${st.counts.active === 0 && st.sync.running ? `<div class="banner"><span class="spinner"></span> First sync in progress: about a minute.</div>` : ""}
    <div class="summary-line"><b>${st.counts.active.toLocaleString()}</b> open · <b>${st.counts.new_24h.toLocaleString()}</b> new today · <b id="stat-strong">–</b> strong (75+)</div>
    <div class="card filters" id="filters"></div>
    <div id="pinned"></div>
    <div class="section-title"><h2 id="list-title">All matches</h2><span class="muted" id="total"></span></div>
    <div class="card joblist" id="joblist"><div class="empty"><span class="spinner"></span></div></div>
    <div class="load-more" id="more"></div>`;
  $("#go-coach")?.addEventListener("click", () => openSettings("profile"));
  renderFilters();
  await refreshJobs();
}

function moreCount(f) {
  return f.tiers.length + f.categories.length + (f.min_score ? 1 : 0) + (f.hide_applied ? 1 : 0) + (f.show_ineligible ? 1 : 0);
}

function renderFilters() {
  const f = S.filters;
  const fac = S.jobs.facets;
  const tiers = Object.entries(TIER_LABEL());
  const cats = S.app.categories;
  $("#filters").innerHTML = `
    <div class="row">
      <input type="search" id="q" placeholder="Search company, title or city…" value="${esc(f.q)}">
      <select id="sort" title="Sort">
        <option value="match" ${f.sort === "match" ? "selected" : ""}>Best match</option>
        <option value="newest" ${f.sort === "newest" ? "selected" : ""}>Newest first</option>
        <option value="company" ${f.sort === "company" ? "selected" : ""}>Company A–Z</option>
      </select>
      <select id="since" title="Posted within">
        ${[[0, "Any time"], [1, "Last 24h"], [3, "Last 3 days"], [7, "Last week"], [30, "Last month"]].map(([v, l]) => `<option value="${v}" ${Number(f.since_days) === v ? "selected" : ""}>${l}</option>`).join("")}
      </select>
      <select id="region" title="Region">
        ${[["", "Anywhere"], ["Canada", "Canada"], ["US-ok", "🇺🇸 US · open to international students"], ["US", "United States (all)"], ["Remote", "Remote"], ["Other", "International"]].map(([v, l]) => `<option value="${v}" ${f.region === v ? "selected" : ""}>${l}</option>`).join("")}
      </select>
      <select id="term" title="Term">
        <option value="">Any term</option>
        ${S.app.terms.map((t) => `<option ${f.term === t ? "selected" : ""}>${esc(t)}</option>`).join("")}
      </select>
      <button class="chip ${f.starred ? "on" : ""}" id="starred" title="Only postings you starred and roles at companies you follow">⭐ Starred</button>
      <button class="btn btn-ghost btn-sm" id="more-f">Filters${moreCount(f) ? ` (${moreCount(f)})` : ""}</button>
    </div>
    <div id="more-filters" class="stack ${LS.get("morefilters", false) ? "" : "hidden"}" style="gap:10px">
    <div class="row"><span class="filter-label">Company</span><div class="chips" id="tier-chips">
      ${tiers.map(([k, l]) => `<button class="chip ${f.tiers.includes(k) ? "on" : ""}" data-tier="${k}">${esc(l)} <span class="n">${fac.tiers[k] ?? ""}</span></button>`).join("")}
    </div></div>
    <div class="row"><span class="filter-label">Role</span><div class="chips" id="cat-chips">
      ${cats.map((c) => `<button class="chip ${f.categories.includes(c) ? "on" : ""}" data-cat="${esc(c)}">${esc(c)} <span class="n">${fac.categories[c] ?? ""}</span></button>`).join("")}
    </div></div>
    <div class="row">
      <label class="range">Min match <input type="range" id="min" min="0" max="90" step="5" value="${f.min_score}"> <b id="min-v">${f.min_score}</b></label>
      <label class="toggle"><input type="checkbox" id="hide-applied" ${f.hide_applied ? "checked" : ""}> Hide ones I've applied to</label>
      <label class="toggle" title="Roles you can't apply to: US-residents only, citizens only, PhD only..."><input type="checkbox" id="show-inel" ${f.show_ineligible ? "checked" : ""}> Show ones I can't apply to <span class="muted" id="inel-count"></span></label>
      <span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" id="follow-mgr">Companies I follow</button>
      <button class="btn btn-ghost btn-sm" id="reset">Reset</button>
    </div></div>`;
  const upd = (patch) => { Object.assign(S.filters, patch); LS.set("filters", S.filters); refreshJobs(); };
  let qt;
  $("#q").addEventListener("input", (e) => { clearTimeout(qt); qt = setTimeout(() => upd({ q: e.target.value }), 250); });
  $("#sort").addEventListener("change", (e) => upd({ sort: e.target.value }));
  $("#since").addEventListener("change", (e) => upd({ since_days: Number(e.target.value) }));
  $("#region").addEventListener("change", (e) => upd({ region: e.target.value }));
  $("#term").addEventListener("change", (e) => upd({ term: e.target.value }));
  $("#min").addEventListener("input", (e) => ($("#min-v").textContent = e.target.value));
  $("#min").addEventListener("change", (e) => upd({ min_score: Number(e.target.value) }));
  $("#hide-applied").addEventListener("change", (e) => upd({ hide_applied: e.target.checked }));
  $("#show-inel").addEventListener("change", (e) => upd({ show_ineligible: e.target.checked }));
  $("#reset").addEventListener("click", () => { S.filters = { q: "", tiers: [], categories: [], region: "", term: "", since_days: 0, min_score: 0, sort: "match", hide_applied: false, show_ineligible: false, starred: false }; LS.set("filters", S.filters); renderFilters(); refreshJobs(); });
  $("#starred").addEventListener("click", (e) => { e.currentTarget.classList.toggle("on"); upd({ starred: !S.filters.starred }); });
  $("#more-f").addEventListener("click", () => { const h = $("#more-filters").classList.toggle("hidden"); LS.set("morefilters", !h); });
  $("#follow-mgr").addEventListener("click", openFollowing);
  document.querySelectorAll("[data-tier]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.tier; const t = new Set(S.filters.tiers); t.has(k) ? t.delete(k) : t.add(k); b.classList.toggle("on"); upd({ tiers: [...t] });
  }));
  document.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.cat; const t = new Set(S.filters.categories); t.has(k) ? t.delete(k) : t.add(k); b.classList.toggle("on"); upd({ categories: [...t] });
  }));
}

async function refreshJobs(append = false) {
  const offset = append ? S.jobs.jobs.length : 0;
  let data;
  try { data = await api(jobQuery({ offset })); } catch (e) { $("#joblist").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (S.tab !== "matches") return;
  S.jobs = append ? { ...data, jobs: S.jobs.jobs.concat(data.jobs) } : data;
  // facet counts in chips
  document.querySelectorAll("[data-tier] .n").forEach((n) => (n.textContent = data.facets.tiers[n.parentElement.dataset.tier] ?? ""));
  document.querySelectorAll("[data-cat] .n").forEach((n) => (n.textContent = data.facets.categories[n.parentElement.dataset.cat] ?? ""));
  $("#total").textContent = `${data.total.toLocaleString()} postings`;
  $("#list-title").textContent = S.filters.starred ? "Starred & followed" : "All matches";
  if ($("#inel-count")) $("#inel-count").textContent = data.ineligible ? `(${data.ineligible.toLocaleString()})` : "";
  if (!append) {
    api(jobQuery({ min_score: 75, offset: 0 }).replace("limit=50", "limit=1")).then((d) => { const el = $("#stat-strong"); if (el) el.textContent = d.total.toLocaleString(); }).catch(() => {});
  }
  const pinned = $("#pinned");
  const PIN_MAX = 6;
  pinned.innerHTML = data.pinned.length && !S.filters.starred ? `
    <div class="section-title"><h2>Starred & followed</h2>
      ${data.pinned.length > PIN_MAX ? `<span class="spacer"></span><a href="#" id="all-favs">See all ${data.pinned.length}${data.pinned.length >= 24 ? "+" : ""} →</a>` : ""}</div>
    <div class="card joblist">${data.pinned.slice(0, PIN_MAX).map(jobRow).join("")}</div>` : "";
  $("#all-favs")?.addEventListener("click", (e) => { e.preventDefault(); S.filters.starred = true; LS.set("filters", S.filters); renderFilters(); refreshJobs(); });
  const list = $("#joblist");
  list.innerHTML = S.jobs.jobs.length ? S.jobs.jobs.map(jobRow).join("") : `<div class="empty">No postings match these filters.${S.app.counts.active === 0 ? " The first sync may still be running." : ""}</div>`;
  $("#more").innerHTML = S.jobs.jobs.length < data.total ? `<button class="btn btn-ghost" id="more-btn">Show more (${(data.total - S.jobs.jobs.length).toLocaleString()} left)</button>` : "";
  $("#more-btn")?.addEventListener("click", () => refreshJobs(true));
  bindJobActions($("#main"));
}

function scoreWhy(j) {
  const p = j.parts || {}, max = { Role: 35, Skills: 20, Location: 15, Term: 15, Freshness: 10 };
  const lines = Object.entries(max).filter(([k]) => k in p).map(([k, m]) => `${k} ${p[k]}/${m}`);
  if (p.Adjustments) lines.push(`Adjustments ${p.Adjustments > 0 ? "+" : ""}${p.Adjustments} (aim, visa, eligibility…)`);
  if ("AI fit" in p) lines.push(`AI fit ${p["AI fit"]} (counts 65%)`);
  if (j.eligibility === "no") lines.push("Capped: you can't apply to this one");
  return `Why ${j.score}:\n` + lines.join("\n");
}

function jobRow(j) {
  const opened = j.posted_at || (j.fresh ? j.first_seen : null);
  const isNew = opened && Date.now() / 1000 - opened < 86400 * 1.5;
  const posted = j.posted_at ? ago(j.posted_at) : j.fresh ? ago(j.first_seen) : "";
  const loc = (j.locations[0] || "") + (j.locations.length + (j.more_locations || 0) > 1 ? ` +${j.locations.length - 1 + (j.more_locations || 0)}` : "");
  const reasons = (j.reasons || []).map((r) => `<span class="tag ${r.k === "good" ? "good" : r.k === "bad" ? "bad" : ""}">${r.k === "good" ? "✓ " : r.k === "bad" ? "✕ " : ""}${esc(r.t)}</span>`).join("");
  const elig = { no: '<span class="tag bad" title="Read from the posting and its application form">Can\'t apply</span>', check: '<span class="tag warn">Check eligibility</span>' }[j.eligibility] || "";
  const notes = (j.notes || []).map((n) => `<span class="tag">ℹ️ ${esc(n)}</span>`).join("");
  const twin = j.twin ? `<a class="tag twin" href="${safeUrl(j.twin.url)}" target="_blank" rel="noopener">Better fit: ${esc(j.company)} in ${esc(j.twin.where || "another location")} (${j.twin.score}) →</a>` : "";
  const appId = j.app_id || j.id;
  return `<div class="job" data-id="${esc(j.id)}" data-app="${esc(appId)}" data-company="${esc(j.company_key)}" data-company-name="${esc(j.company)}">
    <div title="${esc(scoreWhy(j))}">${scoreRing(j.score)}</div>
    <div class="job-body">
      <div class="job-head"><a class="job-title" href="${safeUrl(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>
        ${isNew ? `<span class="tag new">NEW</span>` : ""}${j.app_status ? `<span class="tag good">${esc(STATUS_LABEL[j.app_status])}</span>` : ""}${elig}</div>
      <div class="meta"><b class="company">${esc(j.company)}</b>${j.yc_batch ? ` <span class="muted">(YC)</span>` : ""}${loc ? ` · ${esc(loc)}` : ""}${j.terms.length ? ` · ${esc(j.terms[0])}` : ""}${posted ? ` · ${posted}` : ""}</div>
      ${j.why ? `<div class="job-why">${esc(j.why)}</div>` : ""}
      <details class="job-more"><summary>Details</summary>
        <div class="reasons">${twin}${reasons}${notes}<span class="tag">${esc(TIER_LABEL()[j.tier] || j.tier)}</span><span class="tag">${esc(j.category)}</span><span class="tag">via ${esc(j.source_labels.join(", "))}</span></div>
        ${j.tip ? `<div class="small"><b>Stand out:</b> ${esc(j.tip)}</div>` : ""}
      </details>
    </div>
    <div class="job-actions">
      <button class="icon-btn ${j.fav_job ? "on" : ""}" data-act="fav-job" title="${j.fav_job ? "Unstar" : "Star"}">${starIcon(j.fav_job)}</button>
      <a class="btn btn-sm" href="${safeUrl(j.url)}" target="_blank" rel="noopener">Apply ${extIcon}</a>
      <details class="cal"><summary class="btn btn-ghost btn-sm" title="More">⋯</summary><div class="cal-menu">
        <a href="#" data-act="kit">Application kit (answers + help filling)</a>
        <a href="#" data-act="package">Cover letter + tailored resume</a>
        ${S.app.statuses.map((s) => `<a href="#" data-status="${s}">${j.app_status === s ? "✓ " : ""}Mark ${esc(STATUS_LABEL[s].toLowerCase())}</a>`).join("")}
        ${j.app_status ? '<a href="#" data-status="__remove">Remove from tracker</a>' : ""}
        <a href="#" data-act="fav-company" class="${j.fav_company ? "on" : ""}">${j.fav_company ? "Unfollow" : "Follow"} ${esc(j.company)}</a>
      </div></details>
    </div>
  </div>`;
}

function bindJobActions(root) {
  root.querySelectorAll(".job").forEach((row) => {
    const id = row.dataset.id, company = row.dataset.company;
    row.querySelector('[data-act="fav-company"]')?.addEventListener("click", async (e) => {
      e.preventDefault();
      const on = e.currentTarget.classList.contains("on");
      await toggleFav("company", company, on, row.dataset.companyName);
      toast(on ? `Unfollowed ${row.dataset.companyName}` : `Following ${row.dataset.companyName}: its roles are pinned and you'll be pinged when new ones open`);
      refreshJobs();
    });
    row.querySelector('[data-act="fav-job"]')?.addEventListener("click", async (e) => {
      const on = e.currentTarget.classList.contains("on");
      await toggleFav("job", id, on);
      refreshJobs();
    });
    row.querySelector('[data-act="package"]')?.addEventListener("click", (e) => { e.preventDefault(); e.target.closest("details").open = false; openPackage(row.dataset.app || id); });
    row.querySelector('[data-act="kit"]')?.addEventListener("click", (e) => { e.preventDefault(); e.target.closest("details").open = false; openKit(id); });
    row.querySelectorAll("[data-status]").forEach((a) => a.addEventListener("click", async (e) => {
      e.preventDefault();
      const v = a.dataset.status;
      await api("/api/applications", { method: "PUT", json: { profile_id: S.pid, job_id: row.dataset.app, status: v === "__remove" ? null : v } });
      toast(v === "__remove" ? "Removed from tracker" : `Marked as ${STATUS_LABEL[v]}`);
      loadState();
      if (S.tab === "matches") refreshJobs();
    }));
  });
}

async function toggleFav(kind, key, on, label) {
  if (on) await api(`/api/favourites?profile_id=${S.pid}&kind=${kind}&key=${encodeURIComponent(key)}`, { method: "DELETE" });
  else await api("/api/favourites", { method: "POST", json: { profile_id: S.pid, kind, key, label } });
}

async function openFollowing() {
  const root = $("#modal-root");
  const [favs, jobs] = await Promise.all([api(`/api/favourites?profile_id=${S.pid}`), api(`/api/jobs?profile_id=${S.pid}&limit=200`)]);
  const companies = favs.filter((f) => f.kind === "company");
  const known = new Map();
  jobs.jobs.forEach((j) => known.set(j.company_key, j.company));
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack">
    <div class="row"><h2>Companies you follow</h2><span class="spacer"></span><button class="icon-btn" id="fl-close">✕</button></div>
    <p class="muted small" style="margin:0">Their roles show under Starred, and new postings always alert you.</p>
    <form class="row" id="follow-form">
      <input type="text" list="company-list" id="follow-name" placeholder="Follow a company, e.g. Wealthsimple" style="flex:1">
      <datalist id="company-list">${[...known.values()].sort().map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
      <button class="btn">Follow</button>
    </form>
    ${companies.length ? `<table><tbody>${companies.map((c) => `<tr><td><b>${esc(c.name)}</b></td><td class="muted small">${c.open_roles ? `${c.open_roles} open` : "none open"}</td>
      <td style="text-align:right"><button class="btn btn-ghost btn-sm" data-unfollow="${esc(c.key)}">Unfollow</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="muted small">Not following anyone yet. Tap ♥ on any posting.</div>`}
  </div></div>`;
  const close = () => { root.innerHTML = ""; if (S.tab === "matches") refreshJobs(); };
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#fl-close").addEventListener("click", close);
  $("#follow-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#follow-name").value.trim();
    if (!name) return;
    const key = [...known.entries()].find(([, v]) => v.toLowerCase() === name.toLowerCase())?.[0] || companyKey(name);
    await toggleFav("company", key, false, name);
    toast(`Following ${name}`); openFollowing();
  });
  root.querySelectorAll("[data-unfollow]").forEach((b) => b.addEventListener("click", async () => { await toggleFav("company", b.dataset.unfollow, true); openFollowing(); }));
}

function companyKey(name) {
  return name.toLowerCase().replace(/&/g, " and ").replace(/\(.*?\)/g, " ")
    .replace(/\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|technologies|technology|holdings|group)\b\.?/g, " ")
    .replace(/[^a-z0-9]+/g, " ").trim();
}

// ---------- Applications ----------
const APP_VIEWS = [["active", "In progress", ["applied", "oa", "interviewing", "offer"]], ["saved", "Saved", ["saved"]],
  ["closed", "Rejected", ["rejected"]], ["all", "All", null]];
const APP_ORDER = { interviewing: 0, oa: 1, offer: 2, applied: 3, saved: 4, rejected: 5 };

function appRow(a, cols, now) {  // one application: what it is, where it stands, and what's next
  const since = a.applied_at ? Math.floor((now - a.applied_at) / 86400) : null;
  const links = (a.notes || "").split("\n").map((ln) => ln.match(/^(Book a time|Assessment|Call link): (https?:\/\/\S+)$/)).filter(Boolean);
  const when = a.status === "oa" ? "oa_due_at" : a.status === "interviewing" ? "interview_at" : "";
  const meta = [(a.locations || []).slice(0, 2).join(" · "),
    since !== null && a.status !== "saved" ? `applied ${since === 0 ? "today" : since + "d ago"}` : `updated ${ago(a.updated_at)}`,
    a.email_count ? `${a.email_count} email${a.email_count > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · ");
  return `<div class="app-row app-${esc(a.status)}">
    <div class="app-main">
      <div class="app-title"><b>${esc(a.company)}</b>${a.source === "email" ? ' <span title="Found in your email">📬</span>' : ""} · ${a.url ? `<a href="${safeUrl(a.url)}" target="_blank" rel="noopener">${esc(a.title || "Role")}</a>` : esc(a.title || "Role")}
        ${a.active === 0 ? '<span class="tag bad">posting closed</span>' : ""}${a.status === "applied" && since >= 21 ? '<span class="tag warn">follow up?</span>' : ""}</div>
      <div class="muted small">${esc(meta)}
        · ${a.req_id ? `<span class="req" data-req="${esc(a.job_id)}" data-val="${esc(a.req_id)}" title="Requisition ID: emails quoting it link here. Click to edit">#${esc(a.req_id)}</span>` : `<span class="req" data-req="${esc(a.job_id)}" data-val="" title="Add the requisition ID from the posting or confirmation email">+ req ID</span>`}</div>
      ${a.last_email_subject ? `<div class="small app-email" title="${esc(a.last_email_subject)}">✉️ ${esc(a.last_email_subject)} <span class="muted">· ${ago(a.last_email_at)}</span></div>` : ""}
      ${links.length ? `<div class="row small" style="gap:12px;margin-top:4px">${links.map((m) => `<a href="${safeUrl(m[2])}" target="_blank" rel="noopener">${{ "Book a time": "📅", Assessment: "🧪", "Call link": "🎥" }[m[1]]} ${esc(m[1])} ${extIcon}</a>`).join("")}</div>` : ""}
    </div>
    <div class="app-side">
      ${when ? `<label class="small muted app-date" title="Goes on your calendar link with a reminder">${a.status === "oa" ? "Due" : "Interview"}
        <input type="datetime-local" data-when="${esc(a.job_id)}" data-kind="${when}" value="${localInput(a[when])}"></label>` : ""}
      <select data-job="${esc(a.job_id)}" aria-label="Status">${cols.map((s2) => `<option value="${s2}" ${s2 === a.status ? "selected" : ""}>${STATUS_LABEL[s2]}</option>`).join("")}<option value="__remove">Remove</option></select>
      <button class="btn btn-ghost btn-sm" data-pkg="${esc(a.job_id)}" title="Cover letter, tailored resume and answers">📦 Package</button>
    </div>
  </div>`;
}
const STATUS_SHORT = { saved: "Saved", applied: "Applied", oa: "OA", interviewing: "Interview", offer: "Offer", rejected: "Rejected" };

async function renderTracker() {
  const main = $("#main");
  const [data, accts, inbox] = await Promise.all([
    api(`/api/applications?profile_id=${S.pid}`),
    api(`/api/email/accounts?profile_id=${S.pid}`),
    api(`/api/email/events?profile_id=${S.pid}&limit=60`),
  ]);
  if (S.tab !== "tracker") return;
  const apps = data.applications, st = data.stats;
  const tracking = (profile()?.prefs || {}).track_email !== false;
  const cols = S.app.statuses;
  const now = Date.now() / 1000;
  const view = LS.get("apps.view", "active");
  const appOptions = apps.map((a) => `<option value="${esc(a.job_id)}">${esc(a.company)} · ${esc((a.title || "").slice(0, 50))}</option>`).join("");
  const tile = (cls, v, l) => `<div class="card stat ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`;

  const unlinked = inbox.events.filter((e) => !e.job_id && !e.ignored).length;
  main.innerHTML = `
    <div class="summary-line"><b>${st.applied_total}</b> applied · <b>${st.awaiting}</b> waiting · <b>${st.oa}</b> OA · <b>${st.interviewing}</b> interviewing · <b>${st.offer}</b> ${plural(st.offer, "offer")} · <b>${st.rejected}</b> rejected · <b>${st.response_rate}%</b> response rate</div>
    ${st.stale ? `<div class="banner">⏳ <b>${st.stale} application${st.stale > 1 ? "s" : ""} quiet for 3+ weeks.</b><span class="muted">Autopilot can draft follow-ups.</span></div>` : ""}
    <div class="row" style="margin:4px 0 14px;gap:8px">
      <span class="muted small">📬 ${accts.length ? `${accts.filter((x) => x.enabled).length} inbox${accts.length > 1 ? "es" : ""} ${tracking ? "tracked" : "paused"}` : "No inbox connected"}</span>
      ${accts.length && tracking ? `<button class="btn btn-ghost btn-sm" id="mail-sync">${inbox.syncing ? '<span class="spinner"></span> Checking…' : "Check now"}</button>` : ""}
      <a href="#" class="small" id="inbox-settings">${accts.length ? "Inbox settings" : "Connect your inbox"}</a>
      <span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" id="paste-email" title="Paste a recruiter email: it's matched to the application (or added), the status moves, and links and times are kept">✉️ Paste an email</button>
      <button class="btn btn-ghost btn-sm" id="add-manual">＋ Add by hand</button>
    </div>
    <form class="card card-pad stack hidden" id="email-form" style="margin-bottom:16px">
      <textarea id="e-text" rows="7" placeholder="Paste the whole email here (an interview invite, assessment, rejection, offer or confirmation). Co-op Coach finds the application, moves its status, keeps the booking or assessment link and puts any stated time on your calendar."></textarea>
      <div class="row"><span class="muted small">Read on this Mac by rules; nothing is replied to or booked for you.</span><span class="spacer"></span><button class="btn">Track it</button></div>
      <div id="e-out"></div>
    </form>
    <form class="card card-pad stack hidden" id="manual-form" style="margin-bottom:16px">
      <div class="row"><input type="text" id="m-company" placeholder="Company" required style="flex:1"><input type="text" id="m-title" placeholder="Role, e.g. Software Developer Co-op" style="flex:2"></div>
      <div class="row"><input type="url" id="m-url" placeholder="Posting link (optional)" style="flex:1"><input type="text" id="m-req" placeholder="Req ID (optional)" style="flex:0 1 150px">
        <select id="m-status">${cols.map((c) => `<option value="${c}" ${c === "applied" ? "selected" : ""}>${STATUS_LABEL[c]}</option>`).join("")}</select>
        <button class="btn">Add</button></div>
    </form>
    <div class="app-toolbar">
      <div class="chips" id="app-view">${APP_VIEWS.map(([k, l, sts]) => { const n = apps.filter((a) => !sts || sts.includes(a.status)).length; return `<button class="chip ${view === k ? "on" : ""}" data-view="${k}">${l} <span class="n">${n}</span></button>`; }).join("")}</div>
      <input type="search" id="app-q" placeholder="Search company or role" value="${esc(LS.get("apps.q", ""))}">
    </div>
    <div class="card app-list" id="app-list"></div>
    <details class="inbox-activity" ${unlinked ? "open" : ""}><summary class="section-title"><h2>Inbox activity</h2><span class="muted">${inbox.events.length} recruiting emails${unlinked ? ` · <span class="tag warn">${unlinked} need a job picked</span>` : ""}</span></summary>
    <div class="card">${inbox.events.length ? inbox.events.map((e) => `<div class="inbox-row ${e.ignored ? "ignored" : ""}" data-ev="${esc(e.id)}">
        <div><span class="tag st-${esc(e.status)}">${esc(STATUS_SHORT[e.status] || e.status)}</span><div class="muted small" style="margin-top:4px">${new Date(e.received_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</div></div>
        <div style="min-width:0">
          <div><b>${esc(e.subject || "(no subject)")}</b></div>
          <div class="muted small">${esc(e.from_name || "")} &lt;${esc(e.from_addr || "")}&gt;${e.req_ids ? ` · <span class="req" title="Requisition ID in this email">#${esc(e.req_ids)}</span>` : ""}${e.changed_status ? " · <b>updated your tracker</b>" : ""}${e.classified_by === "you" ? " · fixed by you" : ""}</div>
          <div class="small">→ ${e.job_id ? `${esc(e.app_company || e.company)} · ${esc(e.app_title || e.role || "")}` : '<span class="tag warn">couldn\'t tell which job</span>'}</div>
        </div>
        <div class="row" style="gap:6px;justify-content:flex-end">
          ${e.web_link ? `<a class="btn btn-ghost btn-sm" href="${safeUrl(e.web_link)}" target="_blank" rel="noopener">Open ${extIcon}</a>` : ""}
          <select data-ev-status title="Correct the status">${cols.filter((c) => c !== "saved").map((c) => `<option value="${c}" ${c === e.status ? "selected" : ""}>${STATUS_SHORT[c]}</option>`).join("")}</select>
          <select data-ev-job title="Link to a different application"><option value="">${e.job_id ? "Linked ✓" : "Link to…"}</option>${appOptions}</select>
          <button class="btn btn-ghost btn-sm" data-ev-ignore="${e.ignored ? 0 : 1}">${e.ignored ? "Restore" : "Not a job email"}</button>
        </div></div>`).join("")
      : `<div class="empty">${accts.length ? "No recruiting emails found yet. The first scan reads the last few months and takes a minute." : "Connect your inbox in Settings and application updates appear here automatically."}</div>`}</div></details>`;
  $("#inbox-settings").addEventListener("click", (e) => { e.preventDefault(); openSettings("inbox"); });
  const drawApps = () => {
    const v = APP_VIEWS.find(([k]) => k === LS.get("apps.view", "active")) || APP_VIEWS[0];
    const q = LS.get("apps.q", "").toLowerCase().trim();
    const list = apps.filter((a) => (!v[2] || v[2].includes(a.status)) && (!q || `${a.company} ${a.title}`.toLowerCase().includes(q)))
      .sort((a, b) => (APP_ORDER[a.status] - APP_ORDER[b.status]) || ((b.updated_at || 0) - (a.updated_at || 0)));
    $("#app-list").innerHTML = list.map((a) => appRow(a, cols, now)).join("") || `<div class="empty">${apps.length ? "Nothing here." : "No applications yet. Mark a posting as applied, paste a recruiter email, or add one by hand."}</div>`;
    bindAppRows();
  };
  const bindAppRows = () => {
    const box = $("#app-list");
    box.querySelectorAll("[data-pkg]").forEach((b) => b.addEventListener("click", () => openPackage(b.dataset.pkg)));
    box.querySelectorAll("[data-req]").forEach((b) => b.addEventListener("click", async () => {
      const v2 = prompt("Requisition ID (on the posting or in the confirmation email, e.g. R-0000181145). Emails that quote it link to this application automatically.", b.dataset.val);
      if (v2 === null || v2.trim() === b.dataset.val) return;
      try { await api("/api/applications/req", { method: "PUT", json: { profile_id: S.pid, job_id: b.dataset.req, req_id: v2 } }); toast(v2.trim() ? "Requisition ID saved" : "Requisition ID removed"); renderTracker(); }
      catch (err) { toast(err.message, 5000); }
    }));
    box.querySelectorAll("input[data-when]").forEach((inp) => inp.addEventListener("change", async () => {
      const ts = inp.value ? Math.floor(new Date(inp.value).getTime() / 1000) : null;
      await api("/api/applications/dates", { method: "PUT", json: { profile_id: S.pid, job_id: inp.dataset.when, [inp.dataset.kind]: ts } });
      toast(ts ? "On your calendar, with a reminder" : "Removed from your calendar");
    }));
    box.querySelectorAll("select[data-job]").forEach((sel) => sel.addEventListener("change", async () => {
      await api("/api/applications", { method: "PUT", json: { profile_id: S.pid, job_id: sel.dataset.job, status: sel.value === "__remove" ? null : sel.value } });
      await loadState(); renderTracker();
    }));
  };
  main.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    LS.set("apps.view", b.dataset.view); main.querySelectorAll("[data-view]").forEach((x) => x.classList.toggle("on", x === b)); drawApps();
  }));
  $("#app-q").addEventListener("input", (e) => { LS.set("apps.q", e.target.value); drawApps(); });
  drawApps();
  $("#add-manual").addEventListener("click", () => $("#manual-form").classList.toggle("hidden"));
  $("#paste-email").addEventListener("click", () => { $("#email-form").classList.toggle("hidden"); $("#e-text").focus(); });
  $("#email-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#e-text").value.trim();
    if (!text) return;
    try {
      const r = await api("/api/applications/from-email", { method: "POST", json: { profile_id: S.pid, text } });
      await loadState(); await renderTracker();
      $("#email-form").classList.remove("hidden");
      $("#e-out").innerHTML = `<div class="small"><b>${esc(r.application)}</b> → ${esc(STATUS_LABEL[r.status] || r.status)}${r.status_changed ? ` (was ${esc(STATUS_LABEL[r.was] || r.was)})` : r.already_logged ? " (you pasted this one before)" : ""}</div>
        ${(r.next_steps || []).map((x) => `<div class="small muted">• ${esc(x)}</div>`).join("")}${chatCard(r._card)}`;
      bindCardDates($("#e-out"));
      $("#e-out").querySelector('[data-cc="apps"]')?.remove();
      $("#e-out").querySelector('[data-cc="drafts"]')?.addEventListener("click", () => switchTab("autopilot"));
    } catch (err) { toast(err.message, 6000); }
  });
  // inbox corrections
  main.querySelectorAll("[data-ev]").forEach((row) => {
    const id = row.dataset.ev;
    const put = async (body) => { await api(`/api/email/events/${id}`, { method: "PUT", json: body }); await loadState(); renderTracker(); };
    row.querySelector("[data-ev-status]").addEventListener("change", (e) => put({ status: e.target.value }));
    row.querySelector("[data-ev-job]").addEventListener("change", (e) => e.target.value && put({ job_id: e.target.value }));
    row.querySelector("[data-ev-ignore]").addEventListener("click", (e) => put({ ignored: e.currentTarget.dataset.evIgnore === "1" }));
  });
  $("#mail-sync")?.addEventListener("click", async () => {
    await api("/api/email/sync", { method: "POST", json: { profile_id: S.pid } });
    toast("Checking your inbox for application updates…");
    pollInbox();
  });
  $("#manual-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/applications/manual", { method: "POST", json: { profile_id: S.pid, company: $("#m-company").value, title: $("#m-title").value, url: $("#m-url").value, req_id: $("#m-req").value, status: $("#m-status").value } });
    toast("Added to your pipeline"); await loadState(); renderTracker();
  });
}

// ---------- connecting an inbox (any provider) ----------
function initConnect() {
  const addr = $("#conn-address"), body = $("#conn-body");
  if (!addr) return;
  let timer = null, last = "";
  const detect = async (address) => {
    if (address === last) return;
    last = address;
    body.className = "muted small"; body.innerHTML = '<span class="spinner"></span> Looking up the steps for that provider…';
    try { renderConnect(await api(`/api/email/provider?address=${encodeURIComponent(address)}`)); }
    catch (err) { body.textContent = err.message; }
  };
  addr.addEventListener("input", () => {
    clearTimeout(timer);
    const v = addr.value.trim();
    if (/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(v)) timer = setTimeout(() => detect(v), 450);
  });
  $("#conn").querySelectorAll("[data-prov]").forEach((b) => b.addEventListener("click", () => {
    $("#conn").querySelectorAll("[data-prov]").forEach((x) => x.classList.toggle("on", x === b));
    const v = addr.value.trim(), local = v.includes("@") ? v.split("@")[0] : "you";
    if (!b.dataset.prov) { last = ""; renderConnect({ id: "other", auth: "app_password", name: "Other", host: "", steps: ["Create an app password in your email account's security settings if it offers one (otherwise use your normal password), and enter its IMAP server below."] }); addr.focus(); return; }
    addr.value = `${local}@${b.dataset.prov}`;
    last = "";
    detect(addr.value);
  }));
}

function renderConnect(p) {
  const body = $("#conn-body");
  body.classList.remove("muted", "small");
  const steps = (p.steps || []).length ? `<ol class="steps">${p.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : "";
  if (p.auth === "unsupported") { body.innerHTML = `<div class="banner">${esc(p.name)}: ${esc((p.steps || [])[0] || "not supported")}</div>`; return; }
  if (p.auth === "microsoft") {
    const school = p.id === "school";
    body.innerHTML = `<div class="stack">
      <p class="muted small" style="margin:0">${school ? "School and work accounts on Microsoft 365" : "Outlook.com, Hotmail, Live and MSN"} use Microsoft's own sign-in (Microsoft switched off passwords for other apps in 2024). You'll get a short code to enter on Microsoft's page. Co-op Coach only asks to <b>read</b> mail.${school ? " Some schools require IT approval first. If Microsoft says “Need admin approval”, use forwarding instead (below)." : ""}</p>
      ${S.app.outlook_configured ? `<button class="btn" id="ms-btn">Sign in with Microsoft</button><div id="ms-flow"></div>` : msSetupHtml()}
      <details><summary class="small">Or forward to your applications email (no sign-in needed)</summary>
        <p class="small muted">In Outlook on the web: Settings → Mail → Forwarding → turn on forwarding to your applications Gmail and tick “Keep a copy of forwarded messages”. ${school ? "Your school may block forwarding outside the university." : ""} Then connect that Gmail here instead.</p></details>
    </div>`;
    $("#ms-btn")?.addEventListener("click", startMicrosoft);
    $("#ms-cid-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = e.submitter; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Checking with Microsoft…';
      try {
        await api("/api/email/outlook/client-id", { method: "PUT", json: { client_id: $("#ms-cid").value } });
        toast("Microsoft sign-in is set up"); await loadState(); renderConnect(p);
      } catch (err) { toast(err.message, 9000); btn.disabled = false; btn.textContent = "Save app ID"; }
    });
    return;
  }
  const isOther = p.id === "other";
  body.innerHTML = `<form class="stack" id="imap-form">
      <div class="row"><b>${esc(p.name)}</b>${p.link ? `<a class="btn btn-ghost btn-sm" href="${safeUrl(p.link)}" target="_blank" rel="noopener">Open ${esc(p.name)} settings ${extIcon}</a>` : ""}</div>
      ${steps}
      <input type="password" id="imap-pass" placeholder="${isOther ? "App password (or your password)" : "App password"}" required autocomplete="off">
      ${isOther || !p.host ? `<input type="text" id="imap-host" placeholder="IMAP server, e.g. imap.${esc(p.domain || "example.com")}" value="${esc(p.host || "")}" required>` : ""}
      <button class="btn" id="imap-btn">Connect</button>
    </form>`;
  $("#imap-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#imap-btn"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Signing in…';
    try {
      await api("/api/email/imap", { method: "POST", json: { profile_id: S.pid, address: $("#conn-address").value, password: $("#imap-pass").value, host: $("#imap-host")?.value || "", port: p.port || 993 } });
      toast("Inbox connected: scanning the last few months for application emails");
      render(); pollInbox();
    } catch (err) { toast(err.message, 9000); btn.disabled = false; btn.textContent = "Connect"; }
  });
}

function msSetupHtml() {
  return `<div class="tipbox"><b>One-time setup (about 5 minutes, free).</b> Microsoft only lets registered apps sign in, so whoever runs Co-op Coach registers it once. Put the ID in <code>config.yaml</code> and your friend gets it too.
    <ol class="steps" style="margin-top:8px">
      <li>Sign in at <a href="https://entra.microsoft.com" target="_blank" rel="noopener">entra.microsoft.com</a>. You need a Microsoft Entra directory: a free Azure account gives you one, or use a work/school account that's allowed to register apps.</li>
      <li>App registrations → New registration. Name it “Co-op Coach”. Supported account types: <b>Accounts in any organizational directory and personal Microsoft accounts</b>. Leave Redirect URI empty → Register.</li>
      <li>Authentication → Advanced settings → <b>Allow public client flows: Yes</b> → Save.</li>
      <li>API permissions → Add a permission → Microsoft Graph → Delegated → <b>Mail.Read</b> → Add.</li>
      <li>Copy the <b>Application (client) ID</b> from Overview and paste it here:</li>
    </ol>
    <form class="row" id="ms-cid-form"><input type="text" id="ms-cid" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required style="flex:1;font-family:ui-monospace,Menlo,monospace"><button class="btn">Save app ID</button></form>
    <span class="muted">It isn't a secret, so it's fine in your private repo: <code>email:</code> → <code>microsoft_client_id: …</code></span></div>`;
}

async function startMicrosoft() {
  try {
    const f = await api("/api/email/outlook/start", { method: "POST", json: { profile_id: S.pid } });
    $("#ms-flow").innerHTML = `<div class="stack"><div class="muted small">Open <a href="${safeUrl(f.verification_uri)}" target="_blank" rel="noopener">${esc(f.verification_uri)}</a> and enter:</div><div class="code-box">${esc(f.user_code)}</div><div class="muted small"><span class="spinner"></span> Waiting for you to finish signing in…</div></div>`;
    const poll = async () => {
      const st = await api(`/api/email/outlook/${f.flow_id}`);
      if (st.status === "pending") return setTimeout(poll, 3000);
      if (st.status === "done") { toast(`Connected ${st.address}`); render(); pollInbox(); }
      else { toast(st.error || "Sign-in failed", 9000); $("#ms-flow").innerHTML = ""; }
    };
    setTimeout(poll, 3000);
  } catch (err) { toast(err.message, 9000); }
}

function pollInbox() {
  clearTimeout(pollInbox._t);
  pollInbox._t = setTimeout(async () => {
    if (!["tracker", "settings"].includes(S.tab)) return;
    const r = await api(`/api/email/events?profile_id=${S.pid}&limit=1`).catch(() => null);
    await loadState();
    render();
    if (r?.syncing) pollInbox();
  }, 4000);
}

// ---------- Events ----------
const REGION_ORDER = ["Waterloo Region", "Toronto / GTA", "Hamilton", "Online", "London ON", "Kingston", "Ottawa", "Montréal",
  "Québec City", "Vancouver", "Victoria", "Calgary / Edmonton", "Halifax", "Winnipeg", "Saskatoon", "Other Canada",
  "SF Bay Area", "New York", "Boston", "Seattle", "Los Angeles", "San Diego", "Philadelphia", "Pittsburgh", "Princeton / NJ",
  "New Haven", "Ithaca", "Providence", "Amherst", "Washington DC", "Atlanta", "Research Triangle", "Austin", "Houston",
  "Dallas", "College Station", "Chicago", "Champaign", "Ann Arbor", "Madison", "West Lafayette", "Gainesville", "Other US",
  "Elsewhere", "Unknown"];
const REGION_ICON = {};

const WHEN = [["", "Any time"], ["today", "Today"], ["week", "This week"], ["2w", "Next 2 weeks"], ["month", "This month"]];

function localInput(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function openCalendar(data) {
  const root = $("#modal-root");
  const draw = (feed) => {
    const url = data.calendar_public || (feed ? `${location.origin}${feed}` : "");
    root.innerHTML = `<div class="modal-bg"><div class="modal card stack">
      <div class="row"><h2>Add your events to a calendar</h2><span class="spacer"></span><button class="icon-btn" id="cal-close">✕</button></div>
      <p class="muted small" style="margin:0">One private link with every event you save (⭐ or ✅) plus interview times and assessment deadlines you add in Applications, each with a reminder. Calendars that subscribe stay up to date on their own.</p>
      ${feed ? `<div class="row" style="flex-wrap:wrap;gap:8px">
          <a class="btn" href="${esc(url.replace(/^https?:/, "webcal:"))}">Apple Calendar</a>
          <a class="btn btn-ghost" target="_blank" rel="noopener" href="https://calendar.google.com/calendar/r?cid=${encodeURIComponent(url.replace(/^https?:/, "webcal:"))}">Google Calendar</a>
          <a class="btn btn-ghost" target="_blank" rel="noopener" href="https://outlook.live.com/calendar/0/addfromweb?url=${encodeURIComponent(url)}&name=Co-op%20Coach">Outlook</a>
          <button class="btn btn-ghost" id="cal-copy">Copy link</button></div>
        <div class="code-box small" style="word-break:break-all">${esc(url)}</div>
        <div class="muted small">${data.calendar_public ? "Works anywhere, even when your Mac is off. Google refreshes subscribed calendars every few hours; Apple and Outlook more often." : "Works on this Mac while Co-op Coach is running. Turn on Supabase (Settings → Data & sync) for a link that works everywhere."}
          <a href="#" id="cal-new">Make a new link</a> (the old one stops working).</div>`
        : `<button class="btn" id="cal-make">Create my calendar link</button>`}
    </div></div>`;
    root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) root.innerHTML = ""; });
    $("#cal-close").addEventListener("click", () => (root.innerHTML = ""));
    $("#cal-copy")?.addEventListener("click", async () => { try { await navigator.clipboard.writeText(url); toast("Copied"); } catch { toast("Couldn't copy"); } });
    const make = async (fresh) => {
      const r = await api("/api/events/calendar-feed", { method: "POST", json: { profile_id: S.pid, new: !!fresh } });
      data.calendar_feed = r.calendar_feed || r.url; data.calendar_public = r.calendar_public || "";
      draw(data.calendar_feed);
    };
    $("#cal-make")?.addEventListener("click", () => make(false));
    $("#cal-new")?.addEventListener("click", (e) => { e.preventDefault(); if (confirm("Make a new link? Calendars using the old one stop updating.")) make(true); });
  };
  draw(data.calendar_feed);
}

function gcalLink(e) {
  const fmt = (ts) => new Date(ts * 1000).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const day = (ts) => new Date(ts * 1000).toISOString().slice(0, 10).replace(/-/g, "");
  const end = e.end_ts || e.start_ts + (e.all_day ? 86400 : 3600);
  const dates = e.all_day ? `${day(e.start_ts)}/${day(end)}` : `${fmt(e.start_ts)}/${fmt(end)}`;
  return "https://calendar.google.com/calendar/render?" + new URLSearchParams({ action: "TEMPLATE", text: e.title, dates, location: e.location || "", details: `${e.url || ""}\n\n${(e.description || "").slice(0, 600)}` });
}

function eventRow(e) {
  return `<div class="event ${e.company ? "hot" : ""}" data-ev-id="${esc(e.id)}">
    <div class="time">${e.all_day ? "All day" : new Date(e.start_ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</div>
    <div style="min-width:0">
      <div class="row" style="gap:6px;flex-wrap:wrap"><span class="tag ${["coffee_chat", "info_session", "career_fair", "hackathon"].includes(e.kind) ? "good" : ""}">${esc(e.kind === "other" ? "Event" : KIND_LABEL[e.kind] || "Event")}</span>
        ${e.major ? `<span class="tag major" title="One of the big student hackathons">★ Major</span>` : ""}
        ${e.company ? `<span class="tag warn" title="A company you follow or applied to">${esc(e.company)}</span>` : ""}<b>${esc(e.title)}</b></div>
      <div class="muted small">${esc(e.region)}${e.location && e.location !== e.region ? ` · ${esc(e.location)}` : ""} · ${esc(e.feed_label || "")}${e.also.length ? ` <span title="Also listed on">(also ${esc(e.also.join(", "))})</span>` : ""}</div>
      ${e.description ? `<details class="small"><summary class="muted" style="cursor:pointer">More</summary><div style="margin-top:4px">${esc(e.description)}</div></details>` : ""}
    </div>
    <div class="ev-actions">
      <button class="icon-btn ${e.mark ? "on" : ""}" data-mark title="${e.mark ? "Saved: on your calendar link. Click to remove" : "Save (adds it to your calendar link)"}">${starIcon(!!e.mark)}</button>
      <details class="cal"><summary class="btn btn-ghost btn-sm" title="Add just this one to a calendar">📅</summary><div class="cal-menu">
        <a href="${esc(gcalLink(e))}" target="_blank" rel="noopener">Google Calendar</a><a href="/api/events/${esc(e.id)}/ics">Apple / Outlook (.ics)</a></div></details>
      ${e.url ? `<a class="btn btn-ghost btn-sm" href="${safeUrl(e.url)}" target="_blank" rel="noopener">Details ${extIcon}</a>` : ""}
    </div>
  </div>`;
}

async function renderEvents() {
  const main = $("#main");
  const data = await api(`/api/events?profile_id=${S.pid}&days=90`);
  const all = data.events.map((e) => ({ ...e, region: e.region || "Unknown" }));
  const key = `events.${S.pid}`;
  const NEAR = ["Waterloo Region", "Toronto / GTA", "Hamilton", "Online"];
  const remembered = LS.get(key, null);
  const st = { q: "", when: "", format: "", mine: false, view: "list", day: "", month: "", limit: 120,
    regions: data.filters?.event_regions?.length ? data.filters.event_regions : remembered ? [] : NEAR,  // first visit: near you
    kinds: data.filters?.event_kinds || [], ...(remembered || {}) };
  st.limit = 120; st.day = "";
  const whereOf = () => (!st.regions.length ? "" : st.regions.length > 1 ? "near" : st.regions[0]);
  const count = (k) => all.reduce((m, e) => ((m[e[k]] = (m[e[k]] || 0) + 1), m), {});
  const regions = count("region"), kinds = count("kind");
  const saved = all.filter((e) => e.mark).length;
  main.innerHTML = `
    <div>
        <div class="section-title" style="margin-top:0"><h2>Events</h2><span class="muted" id="ev-count"></span><span class="spacer"></span>
          <div class="seg" id="ev-view">${[["list", "List"], ["month", "Month"], ["hack", "Hackathons"]].map(([v, l]) => `<button type="button" data-view="${v}">${l}</button>`).join("")}</div>
          <button class="btn btn-ghost btn-sm" id="ev-cal">📅 Add to my calendar</button>
          <button class="btn btn-ghost btn-sm" id="ev-refresh">${data.refreshing ? '<span class="spinner"></span> Refreshing…' : "↻ Refresh"}</button></div>
        <div class="ev-bar" id="ev-bar">
          <input type="search" id="ev-q" placeholder="Search events" value="${esc(st.q)}">
          <select id="ev-when">${WHEN.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
          <select id="ev-where"><option value="">Anywhere</option><option value="near">Near me</option>${REGION_ORDER.filter((r) => regions[r]).concat(Object.keys(regions).filter((r) => !REGION_ORDER.includes(r)).sort()).map((r) => `<option value="${esc(r)}">${esc(r)} (${regions[r]})</option>`).join("")}</select>
          <select id="ev-kind"><option value="">All types</option>${Object.keys(KIND_LABEL).filter((k) => kinds[k]).map((k) => `<option value="${k}">${esc(k === "other" ? "Other" : KIND_LABEL[k])} (${kinds[k]})</option>`).join("")}</select>
          <select id="ev-format"><option value="">In person + online</option><option value="in">In person</option><option value="online">Online</option></select>
          <label class="switch"><input type="checkbox" id="ev-mine"><span></span>Saved <span class="muted">(${saved})</span></label>
        </div>
        <div id="ev-list"></div>
    </div>`;
  const inRange = (e) => {
    if (st.day) return new Date(e.start_ts * 1000).toDateString() === new Date(st.day + "T12:00").toDateString();
    if (!st.when) return true;
    const now = new Date(), start = new Date(e.start_ts * 1000);
    const endOfDay = new Date(now); endOfDay.setHours(23, 59, 59, 999);
    const lim = { today: endOfDay.getTime(), week: now.getTime() + 7 * 864e5, "2w": now.getTime() + 14 * 864e5, month: now.getTime() + 31 * 864e5 }[st.when];
    return start.getTime() <= lim;
  };
  const save = () => {
    LS.set(key, { ...st, day: "", limit: 120 });
    clearTimeout(save._t);  // the alert digest uses Where/Type too
    save._t = setTimeout(() => api(`/api/profiles/${S.pid}/prefs`, { method: "PUT", json: { event_regions: st.regions, event_kinds: st.kinds } }).catch(() => {}), 800);
  };
  const filtered = () => {
    const q = st.q.toLowerCase().trim();
    return all.filter((e) => (!st.regions.length || st.regions.includes(e.region)) && (!st.kinds.length || st.kinds.includes(e.kind))
      && (!st.mine || e.mark) && inRange(e) && (!st.format || (st.format === "online") === e.online)
      && (!q || `${e.title} ${e.location || ""} ${e.description || ""} ${e.feed_label || ""} ${e.company || ""}`.toLowerCase().includes(q)))
      .sort((a, b) => a.start_ts - b.start_ts || (b.major ? 1 : 0) - (a.major ? 1 : 0) || (b.company ? 1 : 0) - (a.company ? 1 : 0));
  };
  const bindMarks = () => $("#ev-list").querySelectorAll("[data-mark]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.closest("[data-ev-id]").dataset.evId, e = all.find((x) => x.id === id);
    const mark = e ? (e.mark ? null : "interested") : (b.classList.contains("on") ? null : "interested");
    if (e) e.mark = mark;
    await api(`/api/events/${id}/mark`, { method: "PUT", json: { profile_id: S.pid, mark } });
    if (mark) toast("Saved: it's on your calendar link with a reminder the day before");
    draw();
  }));
  const drawList = () => {
    const evs = filtered();
    $("#ev-count").textContent = `${evs.length} upcoming${st.regions.length || st.kinds.length || st.mine || st.when || st.format || st.q ? " (filtered)" : ""}`;
    const byDay = {};
    evs.slice(0, st.limit).forEach((e) => { const d = new Date(e.start_ts * 1000).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" }); (byDay[d] = byDay[d] || []).push(e); });
    $("#ev-list").innerHTML = (st.day ? `<div class="row small" style="margin-bottom:8px"><span class="tag">${esc(new Date(st.day + "T12:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }))}</span><a href="#" id="ev-day-clear">Show every day</a></div>` : "")
      + (Object.keys(byDay).length ? Object.entries(byDay).map(([day, list]) => `<div class="day"><h3>${esc(day)}</h3><div class="card">${list.map(eventRow).join("")}</div></div>`).join("")
        + (evs.length > st.limit ? `<div class="row" style="justify-content:center"><button class="btn btn-ghost" id="ev-more">Show more (${evs.length - st.limit} left)</button></div>` : "")
      : `<div class="card empty">${all.length ? (st.mine ? "No saved events yet. Tap ☆ on any event." : "Nothing matches those filters. Try Anywhere.") : "No upcoming events yet. Hit ↻ Refresh."}</div>`);
    $("#ev-more")?.addEventListener("click", () => { st.limit += 200; drawList(); });
    $("#ev-day-clear")?.addEventListener("click", (e) => { e.preventDefault(); st.day = ""; drawList(); });
    bindMarks();
  };
  const drawMonth = () => {
    const now = new Date(), base = st.month ? new Date(st.month + "-01T12:00") : new Date(now.getFullYear(), now.getMonth(), 1, 12);
    const y = base.getFullYear(), m = base.getMonth(), first = (new Date(y, m, 1).getDay() + 6) % 7, days = new Date(y, m + 1, 0).getDate();
    const byDay = {};
    filtered().forEach((e) => { const d = new Date(e.start_ts * 1000); if (d.getFullYear() === y && d.getMonth() === m) (byDay[d.getDate()] = byDay[d.getDate()] || []).push(e); });
    $("#ev-count").textContent = `${Object.values(byDay).flat().length} this month`;
    const iso = (d) => `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const cells = Array.from({ length: first }, () => '<div class="mcell empty"></div>').concat(Array.from({ length: days }, (_, i) => {
      const d = i + 1, list = byDay[d] || [], today = d === now.getDate() && m === now.getMonth() && y === now.getFullYear();
      return `<button type="button" class="mcell ${today ? "today" : ""} ${list.length ? "has" : ""}" data-day="${iso(d)}"><span class="mday">${d}</span>
        ${list.slice(0, 3).map((e) => `<span class="mev ${e.mark ? "saved" : ""} k-${esc(e.kind)}">${e.major ? "★ " : ""}${esc(e.title)}</span>`).join("")}
        ${list.length > 3 ? `<span class="muted mmore">+${list.length - 3} more</span>` : ""}</button>`;
    }));
    $("#ev-list").innerHTML = `<div class="card card-pad"><div class="row"><button class="icon-btn" id="m-prev" aria-label="Previous month">‹</button>
        <h3 style="margin:0">${base.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</h3><button class="icon-btn" id="m-next" aria-label="Next month">›</button></div>
      <div class="mgrid">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((x) => `<div class="mhead">${x}</div>`).join("")}${cells.join("")}</div></div>`;
    const shift = (n) => { const d = new Date(y, m + n, 1); st.month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; drawMonth(); };
    $("#m-prev").addEventListener("click", () => shift(-1)); $("#m-next").addEventListener("click", () => shift(1));
    $("#ev-list").querySelectorAll("[data-day].has").forEach((b) => b.addEventListener("click", () => { st.day = b.dataset.day; st.view = "list"; draw(); }));
  };
  const drawHacks = async () => {
    $("#ev-list").innerHTML = '<div class="empty"><span class="spinner"></span></div>';
    const d = await api(`/api/events/hackathons?profile_id=${S.pid}`);
    const fmt = (e) => { const a = new Date(e.start_ts * 1000), b = new Date((e.end_ts || e.start_ts) * 1000), o = { month: "short", day: "numeric" };
      return a.toDateString() === b.toDateString() ? a.toLocaleDateString(undefined, o) : `${a.toLocaleDateString(undefined, o)} – ${b.toLocaleDateString(undefined, o)}`; };
    const row = (h) => `<div class="hack-row" ${h.event ? `data-ev-id="${esc(h.event.id)}"` : ""}>
        <div style="min-width:0"><b>${esc(h.name)}</b> <span class="muted small">${esc(h.city)}</span>
          <div class="small">${h.event ? `<span class="tag good">${esc(fmt(h.event))}</span>` : `<span class="muted">Usually ${esc(h.when)} · dates not out yet</span>`}</div></div>
        <div class="ev-actions">${h.event ? `<button class="icon-btn ${h.event.mark ? "on" : ""}" data-mark title="Save it to your calendar">${starIcon(!!h.event.mark)}</button>` : ""}
          <a class="btn btn-ghost btn-sm" href="${safeUrl(h.event?.url || h.site)}" target="_blank" rel="noopener">${h.event ? "Details" : "Site"} ${extIcon}</a></div></div>`;
    const near = new Set(st.regions.length ? st.regions : NEAR);
    const group = (title, list) => list.length ? `<div class="section-title"><h3 style="margin:0">${title}</h3><span class="muted">${list.filter((h) => h.event).length} with dates</span></div><div class="card">${list.map(row).join("")}</div>` : "";
    $("#ev-count").textContent = `${d.hackathons.length} major hackathons`;
    $("#ev-list").innerHTML = `<p class="muted small" style="margin:0 0 8px">The big student hackathons in Canada and the US. Dates appear as soon as MLH or Devpost lists them; ☆ puts one on your calendar.</p>`
      + group("Near you", d.hackathons.filter((h) => near.has(h.region)))
      + group("Rest of Canada", d.hackathons.filter((h) => h.country === "CA" && !near.has(h.region)))
      + group("United States", d.hackathons.filter((h) => h.country === "US" && !near.has(h.region)));
    bindMarks();
  };
  const draw = () => {
    $("#ev-when").value = st.when; $("#ev-where").value = whereOf(); $("#ev-kind").value = st.kinds[0] || ""; $("#ev-format").value = st.format;
    $("#ev-mine").checked = st.mine;
    main.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === st.view));
    $("#ev-bar").classList.toggle("hidden", st.view === "hack");
    ({ list: drawList, month: drawMonth, hack: drawHacks }[st.view] || drawList)();
  };
  main.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => { st.view = b.dataset.view; st.day = ""; save(); draw(); }));
  $("#ev-when").addEventListener("change", (e) => { st.when = e.target.value; save(); draw(); });
  $("#ev-where").addEventListener("change", (e) => { const v = e.target.value; st.regions = v === "near" ? NEAR : v ? [v] : []; save(); draw(); });
  $("#ev-kind").addEventListener("change", (e) => { st.kinds = e.target.value ? [e.target.value] : []; save(); draw(); });
  $("#ev-format").addEventListener("change", (e) => { st.format = e.target.value; save(); draw(); });
  $("#ev-mine").addEventListener("change", (e) => { st.mine = e.target.checked; save(); draw(); });
  $("#ev-q").addEventListener("input", (e) => { st.q = e.target.value; LS.set(key, { ...st, day: "" }); draw(); });
  $("#ev-refresh").addEventListener("click", async () => {
    await api("/api/events/refresh", { method: "POST" });
    $("#ev-refresh").innerHTML = '<span class="spinner"></span> Refreshing…';
    const poll = async () => { const d = await api(`/api/events?profile_id=${S.pid}&days=1`); if (d.refreshing) return setTimeout(poll, 2000); toast("Events refreshed"); renderEvents(); };
    setTimeout(poll, 2500);
  });
  draw();
  $("#ev-cal").addEventListener("click", () => openCalendar(data));
}

boot().catch((e) => { $("#main").innerHTML = `<div class="card empty">Couldn't reach the Co-op Coach server: ${esc(e.message)}</div>`; });
