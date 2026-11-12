/* People: everyone who wrote to you about an application (from your inbox and pasted emails), your LinkedIn
   connections and people you added, grouped by kind of company and company: who works where, so you know whom to
   reach out to. Loaded before app.js; uses its helpers at call time. */

const RC = { q: "", industry: "", source: "", role: "", startups: false, hiring: false, yc: false, min_funding: 0, min_team: 0, teams: false, starred: false, hidden: false, seq: 0 };
const money = (n) => (n >= 1e9 ? `$${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B` : n >= 1e6 ? `$${Math.round(n / 1e6)}M` : `$${Math.round(n / 1e3)}K`);
const RC_SOURCES = [["", "Everyone"], ["recruiters", "Recruiters"], ["linkedin", "LinkedIn connections"], ["you", "Added by you"]];

const RC_DRAFTS = [
  ["thank_you", "Say thanks", ""],
  ["other", "Check in on my application", "A short, polite check-in on where my application stands."],
  ["networking", "Ask for a 15-minute chat", ""],
  ["referral", "Ask about a referral", ""],
];

async function renderRecruiters() {
  const main = $("#main");
  const t = ++RC.seq;
  if (!$("#rc-body")) {
    main.innerHTML = `<div class="row x-head"><h2>People</h2><span class="spacer"></span>
        <button class="btn btn-ghost btn-sm" id="rc-add">+ Add someone</button>
        <button class="btn btn-ghost btn-sm" id="rc-li" title="LinkedIn → Settings & Privacy → Data privacy → Get a copy of your data → Connections">⬆ Import LinkedIn connections</button>
        <input type="file" id="rc-li-file" accept=".csv,text/csv" hidden>
        <a class="btn btn-ghost btn-sm" id="rc-csv" download>⬇ Spreadsheet (.csv)</a></div>
      <div id="rc-body"><div class="empty"><span class="spinner"></span></div></div>`;
    $("#rc-add").addEventListener("click", () => editRecruiter(null));
    $("#rc-li").addEventListener("click", () => linkedinHelp());
    $("#rc-li-file").addEventListener("change", (e) => importLinkedIn(e.target.files[0]));
  }
  $("#rc-csv").href = `/api/recruiters/export?profile_id=${S.pid}`;  // the person may have switched since
  offerLinkedInExport();
  const p = new URLSearchParams({ profile_id: S.pid, q: RC.q, industry: RC.industry, source: RC.source, role: RC.role, startups: RC.startups, hiring: RC.hiring, yc: RC.yc, min_funding: RC.min_funding, min_team: RC.min_team, teams: RC.teams, starred: RC.starred, hidden: RC.hidden });
  let d;
  try { d = await api(`/api/recruiters?${p}`); } catch (e) { if (t === RC.seq && $("#rc-body")) $("#rc-body").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  const body = $("#rc-body");
  if (t !== RC.seq || S.tab !== "recruiters" || !body) return;
  const filtered = RC.role || RC.startups || RC.hiring || RC.yc || RC.min_funding || RC.min_team;
  const nobody = !d.people && !d.teams && !d.hidden && !RC.q && !RC.source && !filtered;
  const fc = d.funding_check || {};
  body.innerHTML = `
    <p class="summary-line"><b>${d.people}</b> ${d.people === 1 ? "person" : "people"} at <b>${d.companies}</b> ${d.companies === 1 ? "company" : "companies"}: recruiters from your emails, your LinkedIn connections and people you added. Saved on this computer only.</p>
    ${nobody ? `<div class="card card-pad stack">
        <h3>No one here yet</h3>
        <div class="muted">Who works where, so you know whom to reach out to: everyone who writes to you about an application (name, email, title, phone and LinkedIn from their sign-off), your LinkedIn connections, and people you meet. Grouped by kind of company and company. 🎓 next to a company finds alumni from your school there on LinkedIn.</div>
        <div class="row">${d.inbox_connected ? "" : '<button class="btn btn-sm" id="rc-inbox">Connect your inbox</button>'}<button class="btn btn-ghost btn-sm" id="rc-li2">Import LinkedIn connections</button><button class="btn btn-ghost btn-sm" id="rc-paste">Paste a recruiter's email</button><button class="btn btn-ghost btn-sm" id="rc-add2">Add someone you met</button></div>
      </div>` : `
    <div class="card filters">
      <div class="row">
        <input type="search" id="rc-q" placeholder="Find a name, company or note…" value="${esc(RC.q)}">
        <button class="chip ${RC.starred ? "on" : ""}" id="rc-star">⭐ Starred</button>
        <label class="toggle" title="careers@, talent@ and other shared inboxes"><input type="checkbox" id="rc-teams" ${RC.teams ? "checked" : ""}> Shared inboxes${d.teams ? ` (${d.teams})` : ""}</label>
        ${d.hidden || RC.hidden ? `<label class="toggle"><input type="checkbox" id="rc-hidden" ${RC.hidden ? "checked" : ""}> Hidden (${d.hidden})</label>` : ""}
      </div>
      <div class="chips">${RC_SOURCES.map(([k, l]) => `<button class="chip ${RC.source === k ? "on" : ""}" data-src="${k}">${esc(l)}${k && d.sources[k] ? ` <span class="n">${d.sources[k]}</span>` : ""}</button>`).join("")}</div>
      <div class="chips"><button class="chip ${RC.role ? "" : "on"}" data-role="">Any role</button>${d.role_labels.filter((r) => d.roles[r.role]).map((r) => `<button class="chip ${RC.role === r.role ? "on" : ""}" data-role="${r.role}">${esc(r.label)} <span class="n">${d.roles[r.role]}</span></button>`).join("")}</div>
      <div class="row rc-filters">
        <label class="toggle" title="YC companies, startups from your postings, and companies whose funding you've checked"><input type="checkbox" id="rc-startups" ${RC.startups ? "checked" : ""}> 🚀 Startups</label>
        <label class="toggle" title="Internships open to you now, or hiring on the YC directory"><input type="checkbox" id="rc-hiring" ${RC.hiring ? "checked" : ""}> Hiring now</label>
        <label class="toggle"><input type="checkbox" id="rc-yc" ${RC.yc ? "checked" : ""}> YC-backed</label>
        <select id="rc-fund" title="Only companies whose checked funding is at least this">${[[0, "Any funding"], [1e6, "Raised $1M+"], [5e6, "Raised $5M+"], [2e7, "Raised $20M+"], [1e8, "Raised $100M+"]].map(([v, l]) => `<option value="${v}" ${RC.min_funding === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        <select id="rc-team" title="Team size from the YC directory">${[[0, "Any size"], [10, "10+ people"], [50, "50+ people"], [200, "200+ people"]].map(([v, l]) => `<option value="${v}" ${RC.min_team === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        <span class="spacer"></span>
        ${fc.running ? `<span class="muted small"><span class="spinner"></span> Checking funding ${fc.done}/${fc.total}…</span>` : `<button class="btn btn-ghost btn-sm" id="rc-fund-all" title="Your Claude Code or ChatGPT searches the web for each founder's company (only what sources state)">💰 Check founders' funding</button>`}
      </div>
      ${RC.min_funding ? '<div class="muted small">Funding filters count only companies you\'ve checked (💰). Most tiny startups disclose nothing.</div>' : ""}
      <div class="chips"><button class="chip ${RC.industry ? "" : "on"}" data-ind="">All kinds</button>${d.industries.map((i) => `<button class="chip ${RC.industry === i.industry ? "on" : ""}" data-ind="${esc(i.industry)}">${esc(i.label)} <span class="n">${i.count}</span></button>`).join("")}</div>
    </div>
    ${d.groups.length ? d.groups.map((g) => `<div class="section-title"><h2>${esc(g.label)}</h2><span class="muted">${g.companies.length} ${g.companies.length === 1 ? "company" : "companies"}</span></div>
      <div class="stack">${g.companies.map((c) => `<div class="card rc-co">
        <div class="rc-co-head"><b>${esc(c.company)}</b><span class="muted small">${c.people.length} ${plural(c.people.length, "contact")}</span>${coSignals(c, g.industry)}
          ${c.company && c.company !== "Company not known" ? `<span class="spacer"></span><a class="small" href="${safeUrl(alumniUrl(c.company))}" target="_blank" rel="noopener" title="Alumni from your school at ${esc(c.company)}, on LinkedIn (signed in as you)">🎓 Find alumni</a>` : ""}</div>
        ${c.people.map(personRow).join("")}</div>`).join("")}</div>`).join("") : '<div class="card empty">No one matches.</div>'}`}`;
  bindRecruiters(d);
}

function personRow(p) {
  const last = !p.last_seen ? "" : p.emails ? `${ago(p.last_seen)}${p.last_subject ? ` · ${esc(p.last_subject)}` : ""}`
    : p.source === "linkedin" ? `LinkedIn connection since ${new Date(p.last_seen * 1000).toLocaleDateString(undefined, { month: "short", year: "numeric" })}`
    : `added ${ago(p.last_seen)}`;
  const status = p.last_status ? `<span class="tag ${p.last_status === "rejected" ? "bad" : p.last_status === "offer" || p.last_status === "interviewing" ? "good" : ""}">${esc(STATUS_LABEL[p.last_status] || p.last_status)}</span>` : "";
  return `<div class="rc-person" data-id="${p.id}">
    <button class="icon-btn ${p.starred ? "on" : ""}" data-rc="star" title="${p.starred ? "Unstar" : "Star"}">${starIcon(p.starred)}</button>
    <div class="rc-main">
      <div><b>${esc(p.name || p.email || "Someone")}</b>${p.kind === "team" ? ' <span class="tag">shared inbox</span>' : ""}${p.title ? ` <span class="muted">· ${esc(p.title)}</span>` : ""} ${status}</div>
      <div class="rc-reach small">
        ${p.email ? `<a href="mailto:${esc(p.email)}">${esc(p.email)}</a> <button class="linkish" data-copy="${esc(p.email)}" title="Copy">copy</button>` : `<span class="muted">${{ you: "no email saved", pasted: "no address in the pasted email", linkedin: "no email shared on LinkedIn" }[p.source] || "no direct email (wrote through a hiring system)"}</span>`}
        ${p.phone ? `<span>· <a href="tel:${esc(p.phone.replace(/[^\d+x]/g, ""))}">${esc(p.phone)}</a></span>` : ""}
        ${p.linkedin ? `<span>· <a href="${safeUrl(p.linkedin)}" target="_blank" rel="noopener">LinkedIn ${extIcon}</a></span>` : ""}
      </div>
      ${last ? `<div class="muted small ellipsis" title="${esc(p.last_subject || "")}">${last}${p.emails > 1 ? ` · ${p.emails} emails` : ""}</div>` : ""}
      ${p.applications.length ? `<div class="chips">${p.applications.map((a) => `<span class="tag">${esc(a.title)} · ${esc(STATUS_LABEL[a.status] || a.status)}</span>`).join("")}</div>` : ""}
      ${p.notes ? `<div class="small rc-notes">📝 ${esc(p.notes)}</div>` : ""}
    </div>
    <details class="cal"><summary class="btn btn-ghost btn-sm" title="More">⋯</summary><div class="cal-menu">
      <a href="#" data-rc="edit">Edit or add a note</a>
      ${RC_DRAFTS.map(([k, l]) => `<a href="#" data-rc="draft" data-kind="${k}">Draft: ${esc(l)}</a>`).join("")}
      <a href="#" data-rc="hide">${p.hidden ? "Show again" : "Hide"}</a>
      <a href="#" data-rc="delete">Delete</a>
    </div></details>
  </div>`;
}

function bindRecruiters(d) {
  const people = new Map(d.groups.flatMap((g) => g.companies.flatMap((c) => c.people)).map((p) => [String(p.id), p]));
  const again = () => renderRecruiters();
  $("#rc-inbox")?.addEventListener("click", () => openSettings("inbox"));
  $("#rc-paste")?.addEventListener("click", () => switchTab("tracker"));
  $("#rc-add2")?.addEventListener("click", () => editRecruiter(null));
  $("#rc-li2")?.addEventListener("click", () => linkedinHelp());
  document.querySelectorAll("#rc-body [data-role]").forEach((b) => b.addEventListener("click", () => { RC.role = b.dataset.role; again(); }));
  for (const k of ["startups", "hiring", "yc"]) $(`#rc-${k}`)?.addEventListener("change", (e) => { RC[k] = e.target.checked; again(); });
  $("#rc-fund")?.addEventListener("change", (e) => { RC.min_funding = Number(e.target.value); again(); });
  $("#rc-team")?.addEventListener("change", (e) => { RC.min_team = Number(e.target.value); again(); });
  $("#rc-fund-all")?.addEventListener("click", async () => {
    try {
      await api("/api/recruiters/funding", { method: "POST", json: { profile_id: S.pid, founders: true } });
      toast("Checking funding for your founders' companies: your AI searches the web for each, about a minute apiece", 6000);
      const poll = async () => { const d2 = await api(`/api/recruiters?profile_id=${S.pid}&role=founder`); if (S.tab !== "recruiters") return; if (d2.funding_check.running) { setTimeout(poll, 5000); } else { if (d2.funding_check.error) toast(d2.funding_check.error, 6000); } again(); };
      setTimeout(poll, 3000);
    } catch (e) { toast(e.message, 5000); }
  });
  document.querySelectorAll("#rc-body [data-fund]").forEach((b) => b.addEventListener("click", async () => {
    b.innerHTML = '<span class="spinner"></span> searching…';
    b.disabled = true;
    try {
      const r = await api("/api/recruiters/funding", { method: "POST", json: { profile_id: S.pid, company: b.dataset.fund } });
      toast(r.found ? `${r.company}: ${r.total_usd ? money(r.total_usd) + " raised" : "no amount disclosed"}${r.last_round ? ` (${r.last_round})` : ""}` : `${r.company}: no funding found${r.note ? ` (${r.note})` : ""}`, 6000);
      again();
    } catch (e) { toast(e.message, 6000); b.textContent = "💰 Check funding"; b.disabled = false; }
  }));
  document.querySelectorAll("#rc-body [data-src]").forEach((b) => b.addEventListener("click", () => { RC.source = b.dataset.src; again(); }));
  let qt;
  $("#rc-q")?.addEventListener("input", (e) => { clearTimeout(qt); qt = setTimeout(() => { RC.q = e.target.value; again(); }, 250); });
  $("#rc-star")?.addEventListener("click", () => { RC.starred = !RC.starred; again(); });
  $("#rc-teams")?.addEventListener("change", (e) => { RC.teams = e.target.checked; again(); });
  $("#rc-hidden")?.addEventListener("change", (e) => { RC.hidden = e.target.checked; again(); });
  document.querySelectorAll("#rc-body [data-ind]").forEach((b) => b.addEventListener("click", () => { RC.industry = b.dataset.ind; again(); }));
  document.querySelectorAll("#rc-body [data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copied"); } catch { toast("Couldn't copy"); }
  }));
  document.querySelectorAll("#rc-body .rc-person").forEach((row) => {
    const p = people.get(row.dataset.id);
    const put = (fields) => api(`/api/recruiters/${p.id}`, { method: "PUT", json: { profile_id: S.pid, ...fields } });
    row.querySelectorAll("[data-rc]").forEach((el) => el.addEventListener("click", async (e) => {
      e.preventDefault();
      el.closest("details")?.removeAttribute("open");
      try {
        const act = el.dataset.rc;
        if (act === "star") { await put({ starred: !p.starred }); again(); }
        else if (act === "edit") editRecruiter(p);
        else if (act === "hide") { await put({ hidden: !p.hidden }); toast(p.hidden ? "Showing them again" : "Hidden"); again(); }
        else if (act === "delete") {
          if (!confirm(`Delete ${p.name || p.email}? A later email from them adds them back.`)) return;
          await api(`/api/recruiters/${p.id}?profile_id=${S.pid}`, { method: "DELETE" }); again();
        } else if (act === "draft") draftTo(p, el.dataset.kind);
      } catch (err) { toast(err.message, 5000); }
    }));
  });
}

async function draftTo(p, kind) {
  const [, , notes] = RC_DRAFTS.find(([k]) => k === kind) || [];
  toast("Drafting…");
  try {
    const r = await api("/api/agent/tool", { method: "POST", json: { profile_id: S.pid, name: "draft_message", args: {
      kind, company: p.company || "", person: [p.name, p.title].filter(Boolean).join(", "), notes: notes || "",
      job_id: p.applications[0]?.job_id || "" } } });
    if (r.error) return toast(r.error, 5000);
    viewDraft(await api(`/api/drafts/${r.doc_id}?profile_id=${S.pid}`));
  } catch (e) { toast(e.message, 5000); }
}

function editRecruiter(p) {
  const root = $("#modal-root");
  const f = (k, label, type = "text", ph = "") => `<label class="field">${label}<input type="${type}" name="${k}" value="${esc((p && p[k]) || "")}" placeholder="${esc(ph)}"></label>`;
  root.innerHTML = `<div class="modal-bg"><form class="modal card stack" id="rc-form">
    <div class="row"><h2>${p ? esc(p.name || p.email || "Contact") : "Add someone"}</h2><span class="spacer"></span><button type="button" class="icon-btn" id="rc-close">✕</button></div>
    <div class="grid-2">${f("name", "Name", "text", "Priya Shah")}${f("title", "Title", "text", "University Recruiter")}
      ${f("email", "Email", "email", "priya@company.com")}${f("phone", "Phone", "text", "416-555-0123")}
      ${f("company", "Company", "text", "Northwind Robotics")}${f("linkedin", "LinkedIn", "text", "linkedin.com/in/…")}</div>
    <label class="field">Notes<textarea name="notes" rows="3" placeholder="Met at the career fair; said to email after the OA">${esc((p && p.notes) || "")}</textarea></label>
    <div class="row"><span class="muted small">Your changes stay: later emails won't overwrite them.</span><span class="spacer"></span><button class="btn">${p ? "Save" : "Add"}</button></div>
  </form></div>`;
  const close = () => (root.innerHTML = "");
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#rc-close").addEventListener("click", close);
  $("#rc-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fields = Object.fromEntries(new FormData(e.target).entries());
    if (p) for (const k of Object.keys(fields)) if ((p[k] || "") === fields[k]) delete fields[k];  // only what changed
    try {
      if (p) await api(`/api/recruiters/${p.id}`, { method: "PUT", json: { profile_id: S.pid, ...fields } });
      else await api("/api/recruiters", { method: "POST", json: { profile_id: S.pid, ...fields } });
      close(); toast(p ? "Saved" : "Added"); renderRecruiters();
    } catch (err) { toast(err.message, 5000); }
  });
}

function linkedinHelp() {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack">
    <div class="row"><h2>Import your LinkedIn connections</h2><span class="spacer"></span><button type="button" class="icon-btn" id="li-close">✕</button></div>
    <ol class="small">
      <li>On LinkedIn: <b>Me → Settings & Privacy → Data privacy → Get a copy of your data</b>.</li>
      <li>Pick <b>Connections</b> (or the larger archive) and request it. LinkedIn emails you a link, usually within minutes.</li>
      <li>Download it, unzip, and choose <b>Connections.csv</b> here.</li>
    </ol>
    <div class="muted small">You get their name, company, title and profile link as of your export (an email only when they chose to share it). It stays in your own database; nothing is sent anywhere. Import again later to catch job changes; your edits stay.</div>
    <div class="row"><span class="spacer"></span><button class="btn" id="li-pick">Choose Connections.csv</button></div>
  </div></div>`;
  const close = () => (root.innerHTML = "");
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#li-close").addEventListener("click", close);
  $("#li-pick").addEventListener("click", () => { close(); $("#rc-li-file").value = ""; $("#rc-li-file").click(); });
}

async function importLinkedIn(file) {
  if (!file) return;
  if (file.size > 12e6) return toast("That file is too big for a connections list", 5000);
  toast("Importing…");
  try {
    const r = await api("/api/recruiters/import-linkedin", { method: "POST", json: { profile_id: S.pid, csv: await file.text() } });
    toast(`${r.added} ${r.added === 1 ? "person" : "people"} added${r.updated ? `, ${r.updated} updated` : ""} from your LinkedIn connections`, 5000);
    RC.source = "linkedin";
    renderRecruiters();
  } catch (e) { toast(e.message, 6000); }
}

function coSignals(c, industry) {  // a funding check is for startups, not Shopify, IMC or a bank
  const g = c.signals;
  if (!g) return "";
  const f = g.funding;
  const bits = [
    g.yc ? `<span class="tag" title="${esc(g.one_liner || "Y Combinator company")}">YC ${esc(g.yc)}</span>` : "",
    g.team ? `<span class="muted small">${g.team} people</span>` : "",
    g.open ? `<span class="tag good" title="Internships open to you in Co-op Coach">${g.open} open</span>` : g.yc_hiring ? '<span class="tag good" title="Hiring on the YC directory">hiring</span>' : "",
    f && f.found ? `<span class="tag" title="${esc([f.investors.join(", "), f.sources.join(" · ")].filter(Boolean).join(" — "))}">💰 ${f.total_usd ? money(f.total_usd) : "undisclosed"}${f.last_round ? ` · ${esc(f.last_round)}` : ""}${f.last_date ? ` ${esc(f.last_date.slice(0, 4))}` : ""}</span>`
      : f ? '<span class="muted small" title="Checked: no funding found">💰 none found</span>'
      : c.company && c.company !== "Company not known" && !["bigtech", "quant", "finance"].includes(industry) ? `<button class="linkish small" data-fund="${esc(c.company)}" title="Your AI looks up what it has raised">💰 Check funding</button>` : "",
  ].filter(Boolean);
  return bits.length ? `<span class="rc-signals">${bits.join(" ")}</span>` : "";
}

// LinkedIn's export zip waiting in Downloads: one click to import it (read here, on this computer)
async function offerLinkedInExport() {
  const d = await api(`/api/recruiters/linkedin-export?profile_id=${S.pid}`).catch(() => null);
  const f = d?.found;
  $("#li-offer")?.remove();
  if (!f || S.tab !== "recruiters") return;
  $("#rc-body")?.insertAdjacentHTML("beforebegin", `<div class="banner" id="li-offer">📦 <b>Your LinkedIn export is in Downloads</b>
    <span class="muted">${esc(f.name)} · ${ago(f.modified)} · ${f.rows.toLocaleString()} connections</span><span class="spacer"></span>
    <button class="btn btn-sm" id="li-offer-go">Import them</button><button class="btn btn-ghost btn-sm" id="li-offer-no">Not now</button></div>`);
  const send = (json) => api("/api/recruiters/linkedin-export", { method: "POST", json: { profile_id: S.pid, key: f.key, ...json } });
  $("#li-offer-go").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const r = await send({});
      toast(`${r.added} ${r.added === 1 ? "person" : "people"} added${r.updated ? `, ${r.updated} updated` : ""} from your LinkedIn connections`, 5000);
      RC.source = "linkedin"; renderRecruiters();
    } catch (err) { toast(err.message, 6000); e.target.disabled = false; }
  });
  $("#li-offer-no").addEventListener("click", async () => { await send({ dismiss: true }); $("#li-offer")?.remove(); });
}
