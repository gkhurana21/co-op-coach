/* ⚙ Settings: profile & resume, alerts, AI, inbox, sources (events + job sites), account & data.
   Loaded before app.js; uses its helpers ($, api, esc, toast, S, ...) at call time. */

const SETTINGS = [["profile", "Profile & resume"], ["alerts", "Alerts"], ["ai", "AI & extension"], ["tracking", "Inbox"],
  ["sources", "Sources"], ["account", "Account & data"]];
const OLD_SECTIONS = { inbox: "tracking", workday: "tracking", jobs: "sources", events: "sources", data: "account" };

function settingsSection() { const s = LS.get("settings.sec", "profile"); return OLD_SECTIONS[s] || s; }
function openSettings(sec) { LS.set("settings.sec", OLD_SECTIONS[sec] || sec); switchTab("settings"); }

function renderSettings() {
  const sec = SETTINGS.some(([k]) => k === settingsSection()) ? settingsSection() : "profile";
  $("#main").innerHTML = `<div class="settings">
    <nav class="settings-nav">${SETTINGS.map(([k, l]) => `<button class="${k === sec ? "on" : ""}" data-sec="${k}">${l}</button>`).join("")}</nav>
    <div class="settings-body" id="settings-body"><div class="empty"><span class="spinner"></span></div></div></div>`;
  $("#main").querySelectorAll("[data-sec]").forEach((b) => b.addEventListener("click", () => { LS.set("settings.sec", b.dataset.sec); renderSettings(); }));
  return ({ profile: settingsProfile, alerts: settingsAlerts, ai: settingsAI, tracking: settingsTracking,
    sources: settingsSources, account: settingsAccount }[sec])($("#settings-body"));
}

function settingsTracking(body) {
  body.innerHTML = `<div class="stack" style="max-width:860px"><div id="set-inbox"></div></div>`;
  settingsInbox($("#set-inbox"));
}

function settingsSources(body) {
  body.innerHTML = `<div class="stack"><div id="set-events"></div><div id="set-jobs"></div></div>`;
  settingsEvents($("#set-events"));
  settingsJobs($("#set-jobs"));
}

// ---------- Profile & resume ----------
function settingsProfile(body) {
  const p = profile();
  const prof = p.profile || {}, prefs = p.prefs || {};
  const hasResume = !!p.resume_filename;
  const aimOpts = [["balanced", "Balanced", "Rank purely on fit"], ["yc", "YC startups", "Boost YC-backed startups"], ["startup", "Startups", "Boost YC + other startups"], ["bigcorp", "Big corps", "Boost big tech, quant, banks"]];
  const locOpts = ["Canada", "US", "Remote", "Other"];
  const locLabels = { Canada: "Canada", US: "United States", Remote: "Remote", Other: "International" };
  const cities = (prefs.locations || []).filter((l) => !locOpts.includes(l));
  body.innerHTML = `
    <div class="grid-2">
      <div class="stack" style="align-content:start">
        <div class="card card-pad stack">
          <div class="row"><h2>Resume</h2><span class="spacer"></span>${hasResume ? `<span class="muted small">${esc(p.resume_filename)}</span>` : ""}</div>
          <label class="dropzone" id="drop">
            <input type="file" id="file" accept=".pdf,.docx,.txt,.md" hidden>
            <b>${hasResume ? "Upload a new version" : "Drop your resume here"}</b>
            <div class="muted small">PDF or DOCX</div>
          </label>
        </div>
        ${hasResume ? profileCard(prof) : ""}
      </div>
      <div class="stack" style="align-content:start">
        <form class="card card-pad stack" id="prefs">
          <h2>What you're looking for</h2>
          <div class="stack" style="gap:6px"><span class="filter-label">Aiming for</span><div class="chips">
            ${aimOpts.map(([v, l, h]) => `<button type="button" class="chip ${prefs.aim === v ? "on" : ""}" data-aim="${v}" title="${esc(h)}">${esc(l)}</button>`).join("")}
          </div></div>
          <div class="stack" style="gap:6px"><span class="filter-label">Roles</span><div class="chips">
            ${S.app.categories.map((c) => { const on = (prefs.categories?.length ? prefs.categories : prof.target_categories || []).includes(c); return `<button type="button" class="chip ${on ? "on" : ""}" data-pcat="${esc(c)}">${esc(c)}</button>`; }).join("")}
          </div></div>
          <div class="stack" style="gap:6px"><span class="filter-label">Where</span><div class="chips">
            ${locOpts.map((l) => `<button type="button" class="chip ${(prefs.locations || []).includes(l) ? "on" : ""}" data-ploc="${l}">${locLabels[l]}</button>`).join("")}
          </div>
          <input type="text" id="cities" placeholder="Cities, e.g. Toronto, Waterloo" value="${esc(cities.join(", "))}">
          <label class="toggle" title="F-1 CPT/OPT, J-1/TN or remote. Each posting's visa signals still count."><input type="checkbox" id="us-open" ${prefs.us_open !== false ? "checked" : ""}> Open to US roles</label></div>
          <div class="stack" style="gap:6px"><span class="filter-label">Terms</span><div class="chips">
            ${S.app.terms.map((t) => `<button type="button" class="chip ${(prefs.terms || []).includes(t) ? "on" : ""}" data-pterm="${esc(t)}">${esc(t)}</button>`).join("") || '<span class="muted small">Terms appear after the first sync.</span>'}
          </div></div>
          <div class="grid-2" style="gap:10px">
            <label class="field">Work authorization
              <select id="citizenship">${Object.entries(S.app.work_auth).map(([k, l]) => `<option value="${k}" ${(prefs.citizenship || "Canadian") === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
            <label class="field">Hide titles containing
              <input type="text" id="exclude" placeholder="sales, marketing" value="${esc((prefs.exclude_keywords || []).join(", "))}"></label>
          </div>
          <div class="row"><button class="btn">Save</button></div>
        </form>
      </div>
    </div>`;

  const drop = $("#drop"), file = $("#file");
  const upload = async (f) => {
    if (!f) return;
    drop.innerHTML = `<div class="spinner" style="width:22px;height:22px"></div><div style="margin-top:8px"><b>Reading ${esc(f.name)}…</b></div><div class="muted small">${S.app.ai_enabled ? "This can take 20-40 seconds" : "Parsing"}</div>`;
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api(`/api/profiles/${S.pid}/resume`, { method: "POST", body: fd });
      toast(r.note || "Resume read: matches re-ranked", 5000);
      await loadState(); renderSettings();
    } catch (e) { toast(e.message, 5000); renderSettings(); }
  };
  file.addEventListener("change", () => upload(file.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));

  const form = $("#prefs");
  form.querySelectorAll("[data-aim]").forEach((b) => b.addEventListener("click", () => { form.querySelectorAll("[data-aim]").forEach((x) => x.classList.remove("on")); b.classList.add("on"); }));
  form.querySelectorAll("[data-pcat],[data-ploc],[data-pterm]").forEach((b) => b.addEventListener("click", () => b.classList.toggle("on")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    await api(`/api/profiles/${S.pid}/prefs`, { method: "PUT", json: {
      aim: form.querySelector("[data-aim].on")?.dataset.aim || "balanced",
      categories: [...form.querySelectorAll("[data-pcat].on")].map((b) => b.dataset.pcat),
      locations: [...form.querySelectorAll("[data-ploc].on")].map((b) => b.dataset.ploc).concat(split($("#cities").value)),
      terms: [...form.querySelectorAll("[data-pterm].on")].map((b) => b.dataset.pterm),
      citizenship: $("#citizenship").value, us_open: $("#us-open").checked, exclude_keywords: split($("#exclude").value),
    } });
    await loadState();
    toast("Saved: matches re-ranked");
  });
  $("#fix-facts")?.addEventListener("click", () => $("#facts-form").classList.toggle("hidden"));
  $("#li-pdf")?.addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api(`/api/profiles/${S.pid}/linkedin`, { method: "POST", body: fd });
      toast(r.filled.length ? `Filled from LinkedIn: ${r.filled.join(", ")}` : "Nothing missing. Your resume already covers it", 5000);
      await loadState(); renderSettings();
    } catch (err) { toast(err.message, 6000); }
  });
  $("#facts-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const grad = $("#ff-grad").value.trim(), yr = (grad.match(/20\d\d/) || [])[0];
    const overrides = { year_of_study: Number($("#ff-year").value), coop_program: $("#ff-coop").checked, grad_date: grad, ...(yr ? { grad_year: Number(yr) } : {}) };
    await api(`/api/profiles/${S.pid}/prefs`, { method: "PUT", json: { profile_overrides: overrides } });
    toast("Saved. Matching uses these now"); await loadState(); renderSettings();
  });
  $("#ff-reset")?.addEventListener("click", async () => {
    await api(`/api/profiles/${S.pid}/prefs`, { method: "PUT", json: { profile_overrides: {} } });
    await loadState(); renderSettings();
  });
}

function profileCard(p) {
  const row = (k, v) => (v ? `<dt>${k}</dt><dd>${esc(v)}</dd>` : "");
  const ov = (profile().prefs || {}).profile_overrides || {};
  return `<div class="card card-pad stack" id="sees-card">
    <div class="row"><h3 style="margin:0">What Co-op Coach sees</h3><span class="spacer"></span><button class="btn btn-ghost btn-sm" id="fix-facts" title="Correct anything it read wrong">Edit</button></div>
    ${p.summary ? `<p style="margin:0">${esc(p.summary)}</p>` : ""}
    <form class="stack hidden" id="facts-form" style="gap:8px">
      <div class="row" style="flex-wrap:wrap">
        <label class="small">Year <select id="ff-year">${[1, 2, 3, 4, 5].map((y) => `<option ${Number(p.year_of_study) === y ? "selected" : ""}>${y}</option>`).join("")}</select></label>
        <label class="small">Graduating <input type="text" id="ff-grad" value="${esc(p.grad_date || p.grad_year || "")}" placeholder="Apr 2029" style="width:110px"></label>
        <label class="switch"><input type="checkbox" id="ff-coop" ${p.coop_program ? "checked" : ""}><span></span>Co-op program</label>
      </div>
      <div class="row"><button class="btn btn-sm">Save</button>${Object.keys(ov).length ? '<button type="button" class="btn btn-ghost btn-sm" id="ff-reset">Use what the resume says</button>' : ""}</div>
    </form>
    <dl class="kv">
      ${row("School", p.university)}${row("Program", p.program)}${row("Degree", p.degree_level)}
      ${row("Year", p.year_of_study ? `Year ${p.year_of_study}${p.coop_program ? " · co-op" : ""}` : "")}${row("Graduating", p.grad_date || p.grad_year || "")}
      ${row("GPA", p.gpa)}${row("Targets", (p.target_categories || []).join(", "))}
      ${(p.links || []).length ? `<dt>Links</dt><dd>${p.links.map((l) => `<a href="${esc(/^https?:/.test(l) ? l : "https://" + l)}" target="_blank" rel="noopener">${esc(l.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))}</a>`).join(" · ")}</dd>` : ""}
    </dl>
    ${(p.skills || []).length ? `<details><summary class="small muted" style="cursor:pointer">Skills (${p.skills.length})</summary><div class="chips" style="margin-top:6px">${p.skills.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</div></details>` : ""}
    ${(p.experiences || []).length || (p.projects || []).length ? `<details><summary class="small muted" style="cursor:pointer">Experience & projects</summary>
      ${(p.experiences || []).map((e) => `<div class="exp"><b>${esc(e.title)}</b>${e.company ? ` · ${esc(e.company)}` : ""}<div class="muted small">${esc(e.dates || "")}</div></div>`).join("")}
      ${(p.projects || []).map((x) => `<div class="exp"><b>${esc(x.name)}</b>${(x.tech || []).length ? ` <span class="muted small">${esc(x.tech.join(", "))}</span>` : ""}</div>`).join("")}</details>` : ""}
    <label class="muted small" style="cursor:pointer">＋ Add your LinkedIn PDF to fill gaps (LinkedIn → More → Save to PDF)
      <input type="file" id="li-pdf" accept="application/pdf" class="hidden"></label>
  </div>`;
}

// ---------- Alerts ----------
function settingsAlerts(body) {
  const n = profile().prefs?.notify || {};
  body.innerHTML = `<form class="card card-pad stack" id="alerts-form" style="max-width:640px">
    <h2>Alerts</h2>
    <p class="muted small" style="margin:0">Strong new matches, Autopilot, inbox updates and reminders go to every channel you fill in (Mac banners are always on).</p>
    <label class="field">Phone push (ntfy) <span class="hint">Install the free ntfy app, subscribe to a hard-to-guess topic, paste it here</span>
      <input type="text" id="ntfy" placeholder="e.g. coopcoach-gk-8f3k2" value="${esc(n.ntfy_topic || "")}"></label>
    <label class="field">Discord webhook
      <input type="url" id="discord" placeholder="https://discord.com/api/webhooks/…" value="${esc(n.discord_webhook || "")}"></label>
    <label class="field">Email <span class="hint">Needs SMTP settings in .env</span>
      <input type="email" id="email" placeholder="you@school.ca" value="${esc(n.email || "")}"></label>
    <label class="range">New-match alerts at score ≥ <input type="range" id="nmin" min="30" max="90" step="5" value="${n.min_score || 60}"> <b id="nmin-v">${n.min_score || 60}</b></label>
    <div class="row"><button class="btn">Save</button><button type="button" class="btn btn-ghost" id="test-notify">Send a test</button></div>
  </form>`;
  $("#nmin").addEventListener("input", (e) => ($("#nmin-v").textContent = e.target.value));
  const collect = () => ({ notify: { ntfy_topic: $("#ntfy").value.trim(), discord_webhook: $("#discord").value.trim(), email: $("#email").value.trim(), min_score: Number($("#nmin").value) } });
  $("#alerts-form").addEventListener("submit", async (e) => { e.preventDefault(); await api(`/api/profiles/${S.pid}/prefs`, { method: "PUT", json: collect() }); await loadState(); toast("Saved"); });
  $("#test-notify").addEventListener("click", async () => {
    await api(`/api/profiles/${S.pid}/prefs`, { method: "PUT", json: collect() });
    try { const r = await api(`/api/profiles/${S.pid}/test-notify`, { method: "POST" }); toast(r.results.join(" · "), 5000); }
    catch (err) { toast(err.message, 5000); }
  });
}

// ---------- Passcode & account ----------
function settingsAccount(body) {
  const p = profile();
  const lean = (S.app.features?.mode || "lean") === "lean";
  body.innerHTML = `<div class="stack" style="max-width:640px">
    <div class="card card-pad stack"><div class="row"><h2>Features</h2><span class="spacer"></span><span class="tag">${lean ? "Lean" : "Everything"}</span></div>
      <p class="muted small" style="margin:0"><b>Lean</b> shows the main things: jobs, applications, your inbox, calendar, Path and Jarvis. <b>Everything</b> also shows recruiters, browsing by track, public events, hackathons and Excel export. Switching never deletes anything.</p>
      <div class="row"><button class="btn btn-sm ${lean ? "btn-primary" : ""}" data-features="lean">Lean</button><button class="btn btn-sm ${lean ? "" : "btn-primary"}" data-features="all">Everything</button></div></div>

    <form class="card card-pad stack" id="pc-form">
      <div class="row"><h2>Passcode</h2><span class="spacer"></span><span class="tag ${p.has_passcode ? "good" : "warn"}">${p.has_passcode ? "On" : "Off"}</span></div>
      <p class="muted small" style="margin:0">${p.has_passcode ? `Locks ${esc(p.name)}'s data. Auto-locks after ${S.app.auto_lock_minutes} idle minutes.` : "Anyone using this browser can open this profile."}</p>
      ${p.has_passcode ? '<input type="password" id="pc-cur" placeholder="Current passcode" autocomplete="current-password">' : ""}
      <div class="row"><input type="password" id="pc-new" placeholder="New passcode (4+)" autocomplete="new-password" style="flex:1"><input type="password" id="pc-new2" placeholder="Confirm" autocomplete="new-password" style="flex:1"></div>
      <div class="row"><button class="btn btn-sm">${p.has_passcode ? "Change passcode" : "Set passcode"}</button><span class="spacer"></span>${p.has_passcode ? '<button type="button" class="btn btn-ghost btn-sm" id="pc-remove">Remove passcode</button>' : ""}</div>
    </form>
    <div class="card card-pad stack" id="set-impact"><div class="row"><h2>Your numbers</h2></div><div class="muted small">Counting…</div></div>
    <div id="set-data"></div>
    <div class="card card-pad row"><div><b>Delete ${esc(p.name)}</b><div class="muted small">Profile, favourites, tracker and documents. Can't be undone.</div></div><span class="spacer"></span><button class="btn btn-danger btn-sm" id="del-profile">Delete</button></div>
  </div>`;
  settingsData($("#set-data"));
  api(`/api/impact?profile_id=${S.pid}`).then((n) => {
    const tile = (v, l) => `<div class="stat-mini"><b>${Number(v).toLocaleString()}</b><span>${l}</span></div>`;
    $("#set-impact").innerHTML = `<div class="row"><h2>Your numbers</h2><span class="spacer"></span><button class="btn btn-ghost btn-sm" id="copy-line">Copy a resume line</button></div>
      <div class="stat-grid">${tile(n.postings, "postings live")}${tile(n.career_sites, "career sites")}${tile(n.applied, "applied")}${tile(n.response_rate, "% response rate")}
        ${tile(n.interviews, "interviews")}${tile(n.prepared, "prepared by Autopilot")}${tile(n.emails, "emails tracked")}${tile(n.ai_actions, "AI actions")}</div>
      <div class="muted small" id="impact-line">${esc(n.resume_line)}</div>`;
    $("#copy-line").addEventListener("click", async () => { try { await navigator.clipboard.writeText(n.resume_line); toast("Copied"); } catch { toast("Couldn't copy"); } });
  }).catch(() => $("#set-impact").remove());
  $("#pc-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("#pc-new").value !== $("#pc-new2").value) return toast("New passcodes don't match");
    try {
      await api(`/api/profiles/${S.pid}/passcode`, { method: "POST", json: { current: $("#pc-cur")?.value || "", new: $("#pc-new").value } });
      toast(p.has_passcode ? "Passcode changed" : "Passcode on"); await loadState(); renderSettings();
    } catch (err) { toast(err.message, 4000); }
  });
  $("#pc-remove")?.addEventListener("click", async () => {
    if (!$("#pc-cur").value) return toast("Enter your current passcode to remove it");
    if (!confirm("Remove the passcode? Anyone using this browser will be able to open this profile.")) return;
    try { await api(`/api/profiles/${S.pid}/passcode`, { method: "POST", json: { current: $("#pc-cur").value, remove: true } }); toast("Passcode removed"); await loadState(); renderSettings(); }
    catch (err) { toast(err.message, 4000); }
  });
  $("#del-profile").addEventListener("click", async () => {
    if (!confirm(`Delete ${p.name}'s profile, favourites and tracker? This can't be undone.`)) return;
    await api(`/api/profiles/${S.pid}`, { method: "DELETE" });
    S.pid = null; await loadState(); render();
  });
  body.querySelectorAll("[data-features]").forEach((b) => b.addEventListener("click", async () => {
    const r = await api("/api/features", { method: "PUT", json: { mode: b.dataset.features } });
    S.app.features = r;
    toast(r.mode === "lean" ? "Lean: the extra parts are tucked away" : "Everything is back");
    renderNav(); openSettings("account");
  }));
}

// ---------- Inbox ----------
async function settingsInbox(body) {
  const accts = await api(`/api/email/accounts?profile_id=${S.pid}`);
  const inbox = await api(`/api/email/events?profile_id=${S.pid}&limit=1`);
  const tracking = (profile()?.prefs || {}).track_email !== false;
  body.innerHTML = `<div class="card card-pad stack">
    <div class="row"><h2>Inbox</h2><span class="spacer"></span>
      <label class="switch" title="Read your connected inboxes for application updates"><input type="checkbox" id="track-email" ${tracking ? "checked" : ""}><span></span>Track emails</label>
      ${accts.length && tracking ? `<button class="btn btn-ghost btn-sm" id="mail-sync">${inbox.syncing ? '<span class="spinner"></span> Checking…' : "Check now"}</button>` : ""}</div>
    <p class="muted small" style="margin:0">Recruiting emails move your applications along by themselves. Passwords stay in the Keychain. No inbox? Paste any email into the chat or Applications.</p>
    ${accts.map((a) => `<div class="acct-row ${a.enabled && tracking ? "" : "off"}">
        <label class="switch" title="Track this inbox"><input type="checkbox" data-acct-toggle="${a.id}" ${a.enabled ? "checked" : ""}><span></span></label>
        <div style="flex:1;min-width:200px"><b>${esc(a.address)}</b> <span class="tag">${a.kind === "outlook" || a.kind === "outlook_web" ? "Outlook" : a.host === "imap.gmail.com" ? "Gmail" : esc((a.host || "IMAP").replace(/^imap\.(mail\.)?/, ""))}</span>
        <div class="small muted">${a.enabled ? `<span class="dot ${String(a.last_status || "").startsWith("error") ? "err" : a.last_status ? "ok" : ""}"></span>${esc(a.last_status || "first scan in progress…")}${a.last_sync ? ` · ${ago(a.last_sync)}` : ""}` : "paused"}</div></div>
        ${a.kind === "outlook_web" && /sign in again/i.test(a.last_status || "") ? `<button class="btn btn-sm" data-owa-again="${esc(a.address)}">Sign in again</button><span class="muted small" id="owa-flow"></span>` : ""}
        <button class="btn btn-ghost btn-sm" data-del-acct="${a.id}">Disconnect</button></div>`).join("")}
    <details class="connect" id="conn" ${accts.length ? "" : "open"}>
      <summary>＋ Connect ${accts.length ? "another" : "an"} inbox</summary>
      <div class="stack">
        <div class="chips">${[["gmail.com", "Gmail"], ["outlook.com", "Outlook / Hotmail"], ["yahoo.com", "Yahoo"], ["icloud.com", "iCloud"], ["aol.com", "AOL"], ["uwaterloo.ca", "School (Microsoft 365)"], ["", "Other"]].map(([d, l]) => `<button type="button" class="chip" data-prov="${d}">${l}</button>`).join("")}</div>
        <input type="email" id="conn-address" placeholder="The email you apply with, e.g. you.jobs@gmail.com" autocomplete="email">
        <div id="conn-body" class="muted small">Type your address (or pick a provider) and you'll see the steps.</div>
      </div>
    </details>
  </div>`;
  body.querySelectorAll("[data-owa-again]").forEach((b) => b.addEventListener("click", () => startOutlookWeb(b.dataset.owaAgain)));
  body.querySelectorAll("[data-del-acct]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Disconnect this inbox? Your tracker keeps everything found so far.")) return;
    await api(`/api/email/accounts/${b.dataset.delAcct}`, { method: "DELETE" }); renderSettings();
  }));
  $("#mail-sync")?.addEventListener("click", async () => {
    await api("/api/email/sync", { method: "POST", json: { profile_id: S.pid } });
    toast("Checking your inbox for application updates…"); pollInbox();
  });
  $("#track-email").addEventListener("change", async (e) => {
    await api(`/api/profiles/${S.pid}/prefs`, { method: "PUT", json: { track_email: e.target.checked } });
    toast(e.target.checked ? "Email tracking on" : "Email tracking paused");
    await loadState(); renderSettings();
    if (e.target.checked && accts.length) { await api("/api/email/sync", { method: "POST", json: { profile_id: S.pid } }); pollInbox(); }
  });
  body.querySelectorAll("[data-acct-toggle]").forEach((cb) => cb.addEventListener("change", async () => {
    await api(`/api/email/accounts/${cb.dataset.acctToggle}`, { method: "PUT", json: { enabled: cb.checked } });
    toast(cb.checked ? "Tracking this inbox again" : "Paused this inbox"); renderSettings();
  }));
  initConnect();
}

// ---------- Job sources ----------
async function settingsJobs(body) {
  const cos = await api("/api/companies");
  const rep = S.app.sync.last_report || {};
  const agg = {};
  Object.entries(rep.sources || {}).forEach(([k, v]) => {
    const name = k.split(":")[0];
    agg[name] = agg[name] || { ok: 0, fail: 0, count: 0, errors: [] };
    if (v.ok) { agg[name].ok++; agg[name].count += v.count || 0; } else { agg[name].fail++; agg[name].errors.push(`${k}: ${v.error}`); }
  });
  const label = { simplify: "SimplifyJobs list", greenhouse: "Greenhouse boards", lever: "Lever boards", ashby: "Ashby boards", workday: "Workday sites", successfactors: "SuccessFactors sites", jibe: "Jibe sites", yc: "Y Combinator pages", linkedin: "LinkedIn search", indeed: "Indeed search" };
  body.innerHTML = `<div class="card card-pad stack">
      <div class="row"><h2>Job sources</h2><span class="spacer"></span><span class="muted small">${cos.length.toLocaleString()} career sites · ${cos.filter((c) => c.last_count > 0).length.toLocaleString()} hiring interns</span></div>
      <details class="small"><summary class="muted" style="cursor:pointer">Last sync ${S.app.sync.last_finished ? `${ago(S.app.sync.last_finished)} · ${(rep.new_jobs || rep.shared_feed?.new || 0).toLocaleString()} new · ${(rep.closed_jobs || rep.shared_feed?.closed || 0).toLocaleString()} closed` : "never"}</summary>
        <div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>Source</th><th>Status</th><th>Internships</th></tr></thead><tbody>
          ${Object.entries(agg).map(([k, v]) => `<tr><td>${esc(label[k] || k)}</td><td><span class="dot ${v.fail ? "err" : "ok"}"></span>${v.ok} ok${v.fail ? `, <span title="${esc(v.errors.join("\n"))}">${v.fail} failed</span>` : ""}</td><td>${v.count.toLocaleString()}</td></tr>`).join("")}
          ${rep.shared_feed ? `<tr><td>Shared job feed</td><td><span class="dot ok"></span>up to date${rep.shared_feed.changed ? ` · ${rep.shared_feed.changed.toLocaleString()} updated` : ""}</td><td>${(rep.total_active || 0).toLocaleString()}</td></tr>` : ""}
          ${!Object.keys(agg).length && !rep.shared_feed ? '<tr><td colspan="3" class="muted">No sync yet</td></tr>' : ""}
        </tbody></table></div></details>
      <div class="row small" id="disc-row"><span class="muted" id="disc-text">Finding new company boards…</span><span class="spacer"></span>
        <button class="btn btn-ghost btn-sm" id="disc-btn" title="Look for Greenhouse, Ashby and Lever boards the web links to that Co-op Coach doesn't read yet (a few minutes, in the background)">🔎 Find new boards</button></div>
      <form class="row" id="co-form" style="flex-wrap:wrap">
        <input type="url" id="co-url" placeholder="Add a career site: Greenhouse, Lever, Ashby, Workday or YC link" required style="flex:2 1 260px">
        <input type="text" id="co-name" placeholder="Company" style="flex:1 1 120px">
        <select id="co-tier" style="width:auto">${Object.entries(TIER_LABEL()).map(([k, l]) => `<option value="${k}" ${k === "startup" ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
        <button class="btn" id="co-btn">Add</button>
      </form>
      <input type="search" id="co-search" placeholder="Find a company you watch (to pause or remove it)" value="${esc(S.coSearch || "")}">
      <div class="table-wrap"><table><tbody id="co-rows">${coRows(cos)}</tbody></table></div>
    </div>`;
  $("#co-search").addEventListener("input", (e) => { S.coSearch = e.target.value; $("#co-rows").innerHTML = coRows(cos); bindCoRows(); });
  bindCoRows();
  drawDiscover();
  $("#disc-btn").addEventListener("click", async () => {
    await api("/api/discover", { method: "POST" });
    toast("Looking for new company boards in the background: a few minutes");
    drawDiscover();
  });
  $("#co-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#co-btn"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Checking site…';
    try {
      const r = await api("/api/companies", { method: "POST", json: { url: $("#co-url").value, name: $("#co-name").value, tier: $("#co-tier").value } });
      toast(`Added (${r.ats}): ${r.internships} internships open right now`);
      renderSettings();
    } catch (err) { toast(err.message, 6000); btn.disabled = false; btn.textContent = "Add"; }
  });
}

function coRows(cos) {
  const q = (S.coSearch || "").toLowerCase().trim();
  if (!q) return "";  // 4,000+ rows help no one: search for the one you mean
  const list = cos.filter((c) => c.name.toLowerCase().includes(q) || c.slug.toLowerCase().includes(q));
  const shown = list.slice(0, 30);
  return shown.map((c) => `<tr>
        <td><b>${esc(c.name)}</b></td><td class="muted small">${esc({ yc: "Y Combinator", successfactors: "SuccessFactors", jibe: "Jibe" }[c.ats] || c.ats)}</td>
        <td><span class="tag tier tier-${esc(c.tier || "other")}">${esc(TIER_LABEL()[c.tier] || "Other")}</span></td>
        <td>${c.last_count ?? "–"}</td>
        <td class="small"><span class="dot ${c.last_status === "ok" ? "ok" : c.last_status ? "err" : ""}"></span>${esc(c.last_status === "ok" ? "ok" : (c.last_status || "pending").slice(0, 60))}</td>
        <td style="text-align:right;white-space:nowrap"><label class="toggle"><input type="checkbox" data-co-toggle="${c.id}" ${c.enabled ? "checked" : ""}> on</label>
          <button class="btn btn-ghost btn-sm" data-co-del="${c.id}">Remove</button></td></tr>`).join("")
    + (list.length > shown.length ? `<tr><td colspan="6" class="muted small">${list.length - shown.length} more: type more of the name.</td></tr>` : "")
    + (!list.length ? `<tr><td colspan="6" class="muted small">No matches.</td></tr>` : "");
}

function bindCoRows() {
  document.querySelectorAll("[data-co-toggle]").forEach((cb) => cb.addEventListener("change", () => api(`/api/companies/${cb.dataset.coToggle}`, { method: "PATCH", json: { enabled: cb.checked } })));
  document.querySelectorAll("[data-co-del]").forEach((b) => b.addEventListener("click", async () => { await api(`/api/companies/${b.dataset.coDel}`, { method: "DELETE" }); renderSettings(); }));
}

// ---------- Event sources ----------
async function settingsEvents(body) {
  const data = await api(`/api/events?profile_id=${S.pid}&days=1`);
  const GROUPS = ["Cities", "Hackathons", "Toronto & Waterloo", "Canadian universities", "US universities", "Added by you"];
  const byGroup = {};
  data.feeds.forEach((f) => { const g = GROUPS.includes(f.grp) ? f.grp : "Added by you"; (byGroup[g] = byGroup[g] || []).push(f); });
  const feedRow = (f) => `<div class="feed-row ${f.enabled ? "" : "off"}">
        <label class="switch" title="${f.enabled ? "Pause" : "Resume"} this source"><input type="checkbox" data-feed-on="${f.id}" ${f.enabled ? "checked" : ""}><span></span></label>
        <div style="min-width:0;flex:1"><b>${esc(f.label || f.url)}</b> <span class="muted small">${f.enabled ? `${f.upcoming} upcoming` : "paused"}</span>
          ${String(f.last_status || "").startsWith("ok") || !f.last_status ? "" : `<div class="small muted"><span class="dot err"></span>${esc(f.last_status)}</div>`}</div>
        <button class="icon-btn" data-del-feed="${f.id}" title="Remove source">✕</button></div>`;
  body.innerHTML = `<div class="card card-pad stack">
      <div class="row"><h2>Event sources</h2><span class="spacer"></span><span class="muted small">${data.feeds.filter((f) => f.enabled).length} of ${data.feeds.length} on</span></div>
      ${GROUPS.filter((g) => byGroup[g]).map((g) => { const list = byGroup[g], on = list.filter((f) => f.enabled).length, up = list.reduce((n, f) => n + (f.enabled ? f.upcoming : 0), 0);
        return `<details class="feed-group"><summary><b>${esc(g)}</b> <span class="muted small">${on}/${list.length} on · ${up} upcoming</span><span class="spacer"></span>
          <label class="switch" title="Turn the whole group ${on ? "off" : "on"}" onclick="event.stopPropagation()"><input type="checkbox" data-group-on="${esc(g)}" ${on ? "checked" : ""}><span></span></label></summary>
          ${list.map(feedRow).join("")}</details>`; }).join("") || '<div class="muted small">No sources yet.</div>'}
      <form class="row" id="feed-form" style="flex-wrap:wrap">
        <input type="url" id="feed-url" placeholder="Add a calendar: .ics / webcal, Luma, Meetup, Eventbrite or a university calendar" required style="flex:2 1 260px">
        <input type="text" id="feed-label" placeholder="Name (optional)" style="flex:1 1 120px">
        <label class="toggle"><input type="checkbox" id="feed-career" checked> Career events only</label>
        <button class="btn" id="feed-btn">Add</button>
      </form>
    </div>`;
  body.querySelectorAll("[data-feed-on]").forEach((cb) => cb.addEventListener("change", async () => { await api(`/api/event-feeds/${cb.dataset.feedOn}`, { method: "PUT", json: { enabled: cb.checked } }); renderSettings(); }));
  body.querySelectorAll("[data-group-on]").forEach((cb) => cb.addEventListener("change", async () => { await api("/api/event-feeds/group", { method: "PUT", json: { grp: cb.dataset.groupOn, enabled: cb.checked } }); toast(cb.checked ? "Group on. Events arrive on the next refresh" : "Group paused"); renderSettings(); }));
  body.querySelectorAll("[data-del-feed]").forEach((b) => b.addEventListener("click", async () => { if (!confirm("Remove this source and its events?")) return; await api(`/api/event-feeds/${b.dataset.delFeed}`, { method: "DELETE" }); renderSettings(); }));
  $("#feed-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#feed-btn"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Checking…';
    try {
      const r = await api("/api/event-feeds", { method: "POST", json: { url: $("#feed-url").value, label: $("#feed-label").value, career_only: $("#feed-career").checked } });
      toast(`Source added: ${r.found} events found`); renderSettings();
    } catch (err) { toast(err.message, 5000); btn.disabled = false; btn.textContent = "Add"; }
  });
}

// ---------- AI & assistants ----------
const SKILLS = [
  ["autopilot", "today's routine: prepare the best applications, flag follow-ups"],
  ["apply Shopify", "fill an application in Chrome, stop before Submit"],
  ["hunt quant internships in NYC", "search, vet who can apply, star or prepare"],
  ["followups", "inbox updates, quiet applications, follow-up drafts"],
  ["track-email", "paste a recruiter email: tracked, links kept, time on your calendar"],
  ["interview-prep RBC", "research, likely questions, STAR stories"],
  ["events", "this week's info sessions, hackathons, networking"],
  ["resume", "review, or tailor for a posting"],
];

// Signing in happens in Terminal and the browser, out of the page's sight: after "Sign in…", ask every 2 s (the server
// re-checks behind it) until it's done or 3 minutes pass, and check right away when you come back to this window.
const SIGNIN = { claude: { btn: "cc-signin", name: "Claude Code" }, codex: { btn: "cx-signin", name: "ChatGPT (Codex)" } };
const signinWatch = {};
let signinRecheckAt = 0;

async function checkSignin(kind, refresh) {
  let st;
  try { st = await api(`/api/${kind}/status?profile_id=${S.pid}${refresh ? "&refresh=true" : ""}`); } catch { return; }
  if (!st.logged_in || (!signinWatch[kind] && !document.getElementById(SIGNIN[kind].btn))) return;
  stopSignin(kind);
  toast(`${SIGNIN[kind].name} is signed in`);
  await loadState();
  if (document.getElementById(SIGNIN[kind].btn)) renderSettings();
}

function watchSignin(kind) {
  stopSignin(kind);
  const w = signinWatch[kind] = { until: Date.now() + 180000, busy: false };
  w.timer = setInterval(async () => {
    if (Date.now() > w.until) return stopSignin(kind);
    if (w.busy) return;
    w.busy = true;
    try { await checkSignin(kind, false); } finally { w.busy = false; }
  }, 2000);
}

function stopSignin(kind) { clearInterval(signinWatch[kind]?.timer); delete signinWatch[kind]; }

function recheckSignin() {
  if (document.hidden || Date.now() - signinRecheckAt < 1500) return;  // focus and visibilitychange often fire together
  signinRecheckAt = Date.now();
  for (const kind of Object.keys(SIGNIN)) if (signinWatch[kind] || document.getElementById(SIGNIN[kind].btn)) checkSignin(kind, true);
}
window.addEventListener("focus", recheckSignin);
document.addEventListener("visibilitychange", recheckSignin);

function extensionCard() {
  return `
    <div class="card card-pad stack"><h2>Chrome extension</h2>
      <p style="margin:0">Co-op Coach in a side panel next to any website, so you don't have to switch back to this app.</p>
      <ul class="clean small">
        <li><b>Fills applications for you.</b> On a job application page, press <i>Fill this form</i>: it types your details and answers and attaches your tailored resume. You check it and press Submit yourself.</li>
        <li><b>Marks it applied.</b> After you submit, one click moves it to Applied in your tracker.</li>
        <li><b>Your day at a glance.</b> What's ready to apply, what's coming up and your NeetCode streak while you browse.</li>
        <li><b>Reads your Outlook tab.</b> Only needed if Microsoft sign-in doesn't work for your school.</li>
      </ul>
      <div class="ext-steps"><div class="tel">Set it up once · about a minute · not on the Chrome Web Store yet, so it installs from this computer</div>
        <ol class="steps">
          <li>Open a new Chrome tab and go to <button type="button" class="chip ext-copy" data-copy="chrome://extensions">chrome://extensions</button> <span class="muted">(click to copy, then paste it in the address bar)</span></li>
          <li>Turn on <b>Developer mode</b>, top right.</li>
          <li>Click <b>Load unpacked</b> and choose ${S.app.extension_dir ? `this folder: <button type="button" class="chip ext-copy" data-copy="${esc(S.app.extension_dir)}">${esc(S.app.extension_dir)}</button> <span class="muted">(on a Mac, press ⌘⇧G in the file window and paste it)</span>` : "the <b>extension</b> folder inside your coop-coach folder."}</li>
          <li>Click the puzzle piece in Chrome's toolbar, then <b>Co-op Coach</b>. A side panel opens.</li>
          <li>Press <b>Get a code</b> below, paste it into the panel and press <b>Connect</b>.</li>
        </ol>
        <div class="muted small">The code works for 5 minutes. Restarted Chrome or the app? Get a new code and connect again.</div></div>
      <div class="row"><button class="btn" id="extension-connect">Get a code</button><button class="btn btn-ghost" id="extension-revoke">Disconnect</button></div><output id="extension-code" style="overflow-wrap:anywhere;user-select:all"></output></div>`;
}

function bindExtensionCard() {
  $("#extension-connect").addEventListener("click", async () => {
    try { const r = await api("/api/extension/code", {method: "POST", json: {profile_id: S.pid}}); $("#extension-code").innerHTML = `<span class="ext-code">${esc(r.code)}</span> <button type="button" class="btn btn-ghost btn-sm ext-copy" data-copy="${esc(r.code)}">Copy</button>`; }
    catch (e) { toast(e.message, 5000); }
  });
  $("#extension-connect").closest(".card").addEventListener("click", async (e) => {
    const b = e.target.closest(".ext-copy");
    if (!b) return;
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copied"); } catch { toast("Couldn't copy. Select it and copy it yourself", 4000); }
  });
  $("#extension-revoke").addEventListener("click", async () => {
    try { await api("/api/extension/disconnect", {method: "POST", json: {profile_id: S.pid}}); $("#extension-code").textContent = "Disconnected."; }
    catch (e) { toast(e.message, 5000); }
  });
}

async function settingsAI(body) {
  const [cc, cx] = await Promise.all([api(`/api/claude/status?profile_id=${S.pid}`), api(`/api/codex/status?profile_id=${S.pid}`)]);
  const engine = cc.engine, using = cc.provider;
  const line = (st, name, signin) => !st.installed ? `Not installed.${name === "ChatGPT" ? " Install the ChatGPT app (it includes Codex)." : ' <a href="https://claude.com/claude-code" target="_blank" rel="noopener">Get Claude Code</a>.'}`
    : !st.logged_in ? `Installed (${esc(st.version)}), not signed in. <button class="btn btn-sm" id="${signin}">Sign in…</button>`
    : st.paused_minutes ? `Hit your plan's usage limit: back in ${st.paused_minutes} min. Groq covers until then.`
    : `Signed in · ${esc(st.version)}${st.plan ? ` · ${esc(st.plan)}` : ""}${st.model ? ` · ${esc(st.model)}` : ""}`;
  const connected = cc.connections.length > 0;
  body.innerHTML = `<div class="stack" style="max-width:860px">
    <div class="card card-pad stack">
      <div class="row"><h2>Your AI</h2><span class="spacer"></span><span class="muted small">Now: <b>${esc(cc.label || "built-in rules")}</b></span></div>
      <p class="muted small" style="margin:0">The AI that writes your cover letters and answers, reviews your resume and chats as Jarvis. Connect the one you already pay for; a free one fills in if it's busy.</p>
      <div class="engine ${using === "claude_code" ? "on" : ""}">
        <div style="flex:1"><b>Claude Code</b> <span class="muted small">your Claude plan</span><div class="small">${line(cc, "Claude", "cc-signin")}</div></div>
        <label class="switch"><input type="radio" name="engine" value="claude_code" ${engine === "claude_code" ? "checked" : ""} ${cc.installed ? "" : "disabled"}><span></span>Use</label></div>
      <div class="engine ${using === "codex" ? "on" : ""}">
        <div style="flex:1"><b>ChatGPT</b> <span class="muted small">your ChatGPT plan, through Codex</span><div class="small">${line(cx, "ChatGPT", "cx-signin")}</div></div>
        <label class="switch"><input type="radio" name="engine" value="codex" ${engine === "codex" ? "checked" : ""} ${cx.installed ? "" : "disabled"}><span></span>Use</label></div>
      <div class="engine ${using === "groq" ? "on" : ""}">
        <div style="flex:1"><b>Groq</b> <span class="muted small">free and fast; contact details are redacted</span></div>
        <label class="switch"><input type="radio" name="engine" value="auto" ${engine === "auto" ? "checked" : ""}><span></span>Use</label></div>
    </div>
    <div class="card card-pad stack">
      <div class="row"><h2>Co-op Coach inside your AI</h2><span class="spacer"></span></div>
      <p class="muted small" style="margin:0">Use Co-op Coach from Claude Code or ChatGPT directly. It fills forms in your browser and stops before Submit.</p>
      <div class="row" style="flex-wrap:wrap;gap:8px">
        <button class="btn ${connected ? "btn-ghost" : ""}" id="cc-connect" ${cc.installed ? "" : "disabled"}>${connected ? "Update Claude Code plugin" : "Connect Claude Code"}</button>
        <button class="btn btn-ghost" id="cx-connect" ${cx.installed ? "" : "disabled"}>Connect ChatGPT (Codex)</button>
        ${connected ? '<span class="tag good">connected</span><button class="btn btn-ghost btn-sm" id="cc-disconnect">Disconnect all</button>' : ""}</div>
      <div id="cc-connect-out" class="small"></div>
      <details class="small"><summary class="muted" style="cursor:pointer">Commands you can type there</summary>
        <div class="ap-skills" style="margin-top:6px">${SKILLS.map(([c, d]) => `<div><code>/coop-coach:${esc(c)}</code> <span class="muted">${esc(d)}</span></div>`).join("")}
        <div class="muted">In ChatGPT (Codex): <code>$autopilot</code>, or just ask.</div></div></details>
    </div>${extensionCard()}</div>`;
  bindExtensionCard();
  const signin = (kind) => async () => {
    try {
      await api(`/api/${kind}/signin`, { method: "POST", json: { profile_id: S.pid } });
      toast("Terminal opened: finish signing in there. This page updates by itself.", 7000);
      watchSignin(kind);
    } catch (e) { toast(e.message, 6000); }
  };
  $("#cc-signin")?.addEventListener("click", signin("claude"));
  $("#cx-signin")?.addEventListener("click", signin("codex"));
  body.querySelectorAll('input[name="engine"]').forEach((r) => r.addEventListener("change", async () => {
    try { await api("/api/claude/engine", { method: "PUT", json: { profile_id: S.pid, engine: r.value } }); await loadState(); renderSettings(); }
    catch (err) { toast(err.message, 6000); renderSettings(); }
  }));
  const connect = (url, label) => async () => {
    const out = $("#cc-connect-out");
    out.innerHTML = `<span class="spinner"></span> Installing Co-op Coach into ${label}…`;
    try {
      const r = await api(url, { method: "POST", json: { profile_id: S.pid } });
      out.innerHTML = (r.ok ? `Connected to ${label}. Start a new session and ask it to run your autopilot.` : "Something didn't work. You can run these yourself:")
        + `<details style="margin-top:4px"><summary class="muted" style="cursor:pointer">Details</summary><pre class="kit-pre">${esc((r.steps || []).map((s) => `${s.ok ? "✓" : "✗"} ${s.step}\n${s.out}`).join("\n\n") + "\n\n" + (r.commands || []).join("\n"))}</pre></details>`;
    } catch (e) { out.textContent = e.message; }
  };
  $("#cc-connect").addEventListener("click", connect("/api/claude/connect", "Claude Code"));
  $("#cx-connect").addEventListener("click", connect("/api/codex/connect", "ChatGPT (Codex)"));
  $("#cc-disconnect")?.addEventListener("click", async () => {
    if (!confirm("Disconnect Claude Code and ChatGPT? The connection is revoked and the plugins are removed.")) return;
    await api("/api/claude/disconnect", { method: "POST", json: { profile_id: S.pid, uninstall: true } }).catch((e) => toast(e.message));
    await api("/api/codex/disconnect", { method: "POST", json: { profile_id: S.pid } }).catch(() => {});
    renderSettings();
  });
}

// ---------- Data & sync (Supabase) ----------
async function settingsData(body) {
  const d = await api(`/api/database?profile_id=${S.pid}`);
  const on = d.backend === "supabase";
  body.innerHTML = `<div class="card card-pad stack">
    <div class="row"><h2>Where your data lives</h2><span class="spacer"></span><span class="tag ${on ? (d.ok ? "good" : "bad") : ""}">${on ? (d.ok ? "Supabase · connected" : "Supabase · not reachable") : "This computer"}</span></div>
    ${on ? `<p class="muted small" style="margin:0">Your Supabase project <code>${esc(d.ref || "")}</code> (${esc(d.region || "")}), in a private schema. Passwords and tokens stay in your Mac's Keychain.</p>
      ${d.counts ? `<div class="summary-line">${Object.entries(d.counts).map(([k, n]) => `<b>${n.toLocaleString()}</b> ${k}`).join(" · ")}</div>` : ""}
      ${d.error ? `<div class="banner">⚠️ ${esc(d.error)}</div>` : ""}`
      : `<p class="muted small" style="margin:0">In a database file on this computer. The README shows how to move it to your own Supabase project (for a calendar link that works anywhere).</p>`}
  </div>
  <div class="card card-pad stack" id="feed-card"><div class="muted small"><span class="spinner"></span></div></div>`;
  drawFeed();
}

// the shared job feed: a crawler reads every board a few times a day, and this app pulls what changed
async function drawFeed() {
  const el = $("#feed-card");
  if (!el) return;
  const f = await api("/api/central").catch(() => null);
  if (!f || !$("#feed-card")) return;
  const times = f.crawls.length > 1 ? `${f.crawls.slice(0, -1).join(", ")} and ${f.crawls.at(-1)}` : f.crawls.join("");
  const last = f.last_pull || {}, crawl = f.last_crawl;
  el.innerHTML = `<div class="row"><h2>Shared job feed</h2><span class="spacer"></span><span class="tag ${f.on ? "good" : ""}">${f.on ? "On" : "Off"}</span></div>
    ${f.on ? `<p class="muted small" style="margin:0">One crawler reads every board at ${esc(times)} (your time), with quick checks of the boards that just posted in between. Every 15 minutes this app copies what changed (in seconds) and checks the boards that matter most to you itself (niches you follow, companies you starred or applied to, the boards behind your best matches), instead of reading about 9,500 boards.</p>
      ${f.stale ? `<div class="banner">⚠️ The crawler hasn't run ${crawl ? `since ${ago(crawl.finished_at)}` : "yet"}, so this app reads every board itself until it's back.</div>` : ""}
      <div class="summary-line">${crawl ? `Last crawl <b>${ago(crawl.finished_at)}</b> (${esc(crawl.kind || "full")})` : "No crawl yet"}
        · <b>${(f.open || 0).toLocaleString()}</b> open postings${f.pulled_at ? ` · checked ${ago(f.pulled_at)}${last.new ? `, <b>${last.new}</b> new` : ""}` : ""}
        ${f.local_boards ? ` · <b>${f.local_boards}</b> board${f.local_boards > 1 ? "s" : ""} you added, read here` : ""}</div>
      <div class="row"><button class="btn btn-ghost btn-sm" id="feed-pull">Check now</button><span class="spacer"></span><button class="btn btn-ghost btn-sm" id="feed-off">Read every board myself instead</button></div>`
    : f.available ? `<p class="muted small" style="margin:0">Your Supabase project has the shared feed: a crawler reads every board at ${esc(times)} and this app copies what changed in seconds, instead of reading about 9,500 boards itself (a minute or more, every few hours).</p>
      <div class="row"><button class="btn btn-primary btn-sm" id="feed-on">Use the shared feed</button></div>`
    : `<p class="muted small" style="margin:0">Co-op Coach checks every company's job page itself, every few hours (about a minute each time).</p>`}`;
  $("#feed-on")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.innerHTML = '<span class="spinner"></span> Copying the feed…';
    try { const r = await api("/api/central", { method: "POST", json: { on: true } }); toast(`Shared feed on: ${(r.changed || 0).toLocaleString()} postings up to date in ${r.seconds}s`); }
    catch (err) { toast(err.message, 6000); }
    drawFeed();
  });
  $("#feed-off")?.addEventListener("click", async () => {
    if (!confirm("Read every board on this computer again? Syncs take a minute or more instead of seconds.")) return;
    await api("/api/central", { method: "POST", json: { on: false } }).catch((err) => toast(err.message));
    drawFeed();
  });
  $("#feed-pull")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.innerHTML = '<span class="spinner"></span> Checking…';
    await api("/api/sync", { method: "POST" }).catch((err) => toast(err.message));
    setTimeout(drawFeed, 4000);
  });
}

// new boards found on the public web (Common Crawl and SimplifyJobs links), about once a month or on request
async function drawDiscover() {
  const el = $("#disc-text"), btn = $("#disc-btn");
  if (!el) return;
  const d = await api("/api/discover").catch(() => null);
  if (!d || !$("#disc-text")) return;
  const last = d.last || {};
  btn.disabled = d.running;
  el.innerHTML = d.running ? `<span class="spinner"></span> ${esc(d.progress || "Finding new boards")}…`
    : last.error ? `Finding new boards failed ${ago(last.at)}: ${esc(last.error)}`
    : last.at ? `New boards: <b>${(last.added || 0).toLocaleString()}</b> found ${ago(last.at)} (${(last.with_internships || 0).toLocaleString()} with internships open) · checks again monthly`
    : "Co-op Coach looks for new Greenhouse, Ashby and Lever boards once a month.";
  if (d.running) setTimeout(drawDiscover, 4000);
}
