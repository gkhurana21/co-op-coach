/* Jobs → tracks: a track's page (Postings · Companies · Market) and the ＋ Tracks list. A track is a kind of role or
   of employer; every one opens the same page, and the ones pinned sit in the Jobs bar after Matches.
   Loaded before app.js; uses its helpers ($, api, esc, toast, S, LS, jobRow, bindJobActions...) at call time. */

const T = { seq: 0, facet: null, list: null };
const SECTIONS = [["postings", "Postings"], ["companies", "Companies"], ["market", "Market"]];
const TREND_TAG = { new: '<span class="tag new">New</span>', rising: '<span class="tag warn">Rising</span>' };
// every render takes a number; one that finishes after the person moved on (another track, section or page) stops
const tstale = (t) => t !== T.seq || !["track", "tracks"].includes(S.tab);
const trackId = () => LS.get("track", "");
const tenc = (id) => encodeURIComponent(id);
const pctOf = (x) => `${Math.round((x || 0) * 100)}%`;
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (ym) => { const [y, m] = ym.split("-"); return `${MONTH_SHORT[Number(m) - 1]} ${y.slice(2)}`; };
const wage = (v, cur) => `${cur === "USD" ? "US$" : cur === "CAD" ? "CA$" : "$"}${v >= 100 ? Math.round(v) : v.toFixed(v % 1 ? 2 : 0)}`;

function openTrack(id, section, facet) {
  LS.set("track", id);
  if (section) LS.set(`tsec:${id}`, section);
  T.facet = facet ? { track: id, ...facet } : null;
  if (S.tab === "track") renderTrack(); else switchTab("track");
}
function openNiche(nid) { openTrack(`niche:${nid}`); }  // chat cards and older links name the niche

async function ensureBar(force = false) {
  if (!force && S.trackBarPid === S.pid && S.trackBar) return;
  try {
    const d = await api(`/api/tracks/bar?profile_id=${S.pid}`);
    S.trackBar = d.pins; S.trackBarPid = S.pid; S.trackMax = d.max;
  } catch { S.trackBar = S.trackBar || []; }
  renderNav();
}

// ---------- a track's page ----------
async function renderTrack() {
  const main = $("#main");
  let id = trackId();
  if (!id) return goView("tracks");
  ensureBar();
  renderNav();
  main.innerHTML = `<div id="t-head" class="t-head"><div class="empty"><span class="spinner"></span></div></div><div id="t-body"></div>`;
  const t = ++T.seq;
  let h;
  try { h = await api(`/api/tracks/${tenc(id)}?profile_id=${S.pid}`); }
  catch (e) {
    if (tstale(t)) return;
    LS.set("track", "");
    toast(`That track isn't there any more (${e.message})`, 4500);
    return goView("tracks");
  }
  if (tstale(t)) return;
  if (h.id !== id) {  // an older link (a niche, a found track now covered by a shipped one) opens its track
    if (T.facet && T.facet.track === id) T.facet.track = h.id;
    LS.set(`tsec:${h.id}`, LS.get(`tsec:${id}`, "postings"));
    LS.set("track", h.id); id = h.id; renderNav();
  }
  T.head = h;
  renderNav();  // a track that isn't pinned shows in the bar by its name while it's open
  drawHead(h);
  drawSection(t);
}

function trackSection(id = trackId()) {
  const s = LS.get(`tsec:${id}`, "postings");
  return SECTIONS.some(([k]) => k === s) ? s : "postings";
}

function drawHead(h) {
  const can = h.can || {};
  const menu = [can.find_more && `<a href="#" data-tact="find">🔎 Find more firms</a>`, can.add_firm && `<a href="#" data-tact="add">＋ Add a firm</a>`,
    can.reminders && `<a href="#" data-tact="remind">🔔 Remind me before firms open</a>`, can.delete && `<a href="#" data-tact="delete">Delete this track</a>`].filter(Boolean);
  $("#t-head").innerHTML = `
    <div class="row t-title"><h2>${esc(h.icon || "")} ${esc(h.name)}</h2><span class="tag">${esc(h.kind_label)}</span>
      ${TREND_TAG[h.trend] || ""}${h.source === "yours" ? '<span class="tag">Yours</span>' : h.source === "found" ? '<span class="tag" title="Found in the feed: companies keep posting this kind of role">Found in the feed</span>' : ""}
      <span class="spacer"></span>
      <button class="btn btn-sm ${h.pinned ? "btn-ghost" : ""}" id="t-pin" title="${h.pinned ? "Take it out of the Jobs bar" : "Add it to the Jobs bar, after Matches"}">${h.pinned ? "📌 Pinned" : "📌 Pin"}</button>
      <button class="btn btn-sm btn-ghost" id="t-follow" title="New roles in this track always alert you${can.find_more ? ", and its firms' job boards are read every sync" : ""}">${h.following ? "✓ Following" : "🔔 Follow"}</button>
      ${menu.length ? `<details class="cal"><summary class="btn btn-ghost btn-sm" title="More">⋯</summary><div class="cal-menu">${menu.join("")}</div></details>` : ""}
    </div>
    ${h.blurb ? `<p class="summary-line">${esc(h.blurb)}</p>` : ""}
    <div class="t-counts">
      <button class="linkish" data-count="open" title="Every open posting, including ones you can't apply to"><b>${h.open.toLocaleString()}</b> open</button><span class="muted">·</span>
      <button class="linkish" data-count="ok"><b>${h.open_to_you.toLocaleString()}</b> open to you</button><span class="muted">·</span>
      <button class="linkish" data-count="new"><b>${h.new_week.toLocaleString()}</b> new this week</button>
    </div>
    <div class="t-tabs" role="tablist">${SECTIONS.map(([k, l]) => `<button role="tab" class="${trackSection(h.id) === k ? "on" : ""}" data-sec="${k}">${l}</button>`).join("")}</div>`;
  $("#t-pin").addEventListener("click", async () => {
    try {
      const r = await api(`/api/tracks/${tenc(h.id)}/pin`, { method: "PUT", json: { profile_id: S.pid, on: !h.pinned } });
      toast(r.pinned ? `${h.name} is in your Jobs bar` : `${h.name} is out of your Jobs bar`);
      await ensureBar(true); h.pinned = r.pinned; drawHead(h);
    } catch (e) { toast(e.message, 5000); }
  });
  $("#t-follow").addEventListener("click", async () => {
    const r = await api(`/api/tracks/${tenc(h.id)}/follow`, { method: "PUT", json: { profile_id: S.pid, on: !h.following } });
    toast(r.following ? `Following ${h.name}: its new roles will alert you${r.boards_added ? ` (${r.boards_added} job boards added to your sync)` : ""}` : `Stopped following ${h.name}`, 5000);
    h.following = r.following; drawHead(h);
  });
  document.querySelectorAll("#t-head [data-sec]").forEach((b) => b.addEventListener("click", () => {
    LS.set(`tsec:${h.id}`, b.dataset.sec); T.facet = null; drawHead(h); drawSection(++T.seq);
  }));
  document.querySelectorAll("#t-head [data-count]").forEach((b) => b.addEventListener("click", () => {
    const f = { ...tfilters(h.id), show_ineligible: b.dataset.count === "open", since_days: b.dataset.count === "new" ? 7 : 0 };
    LS.set(`tf:${h.id}`, f); LS.set(`tsec:${h.id}`, "postings"); T.facet = null; drawHead(h); drawSection(++T.seq);
  }));
  document.querySelectorAll("#t-head [data-tact]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault(); a.closest("details").open = false;
    ({ find: () => findMore(h), add: () => addFirm(h), remind: () => remindAll(h), delete: () => deleteTrack(h) })[a.dataset.tact]();
  }));
}

function drawSection(t) {
  const s = trackSection();
  const run = { postings: drawPostings, companies: drawCompanies, market: drawMarket }[s];
  return run(t).catch((e) => { const b = $("#t-body"); if (!tstale(t) && b) b.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; });
}

// ---------- Postings: the same rows as Matches, with this track's own filters ----------
function tfilters(id) {
  return { q: "", sort: "match", since_days: 0, region: "", term: "", show_ineligible: false, ...LS.get(`tf:${id}`, {}) };
}

function postingsQuery(id, offset = 0) {
  const f = tfilters(id), fac = T.facet && T.facet.track === id ? T.facet : null;
  const p = new URLSearchParams({ profile_id: S.pid, track: id, q: f.q, region: f.region, term: f.term, since_days: f.since_days,
    sort: f.sort, show_ineligible: !!(f.show_ineligible || fac?.show_ineligible), limit: 50, offset });
  if (fac) { p.set("facet", fac.facet || ""); if (fac.population) p.set("population", fac.population); }
  return "/api/jobs?" + p.toString();
}

async function drawPostings(t, append = false) {
  const id = trackId(), body = $("#t-body");
  if (!append) {
    const f = tfilters(id), fac = T.facet && T.facet.track === id ? T.facet : null;
    body.innerHTML = `
      <div class="card filters">
        <div class="row">
          <input type="search" id="tp-q" placeholder="Search company, title or city…" value="${esc(f.q)}">
          <select id="tp-sort" title="Sort">${[["match", "Best match"], ["newest", "Newest first"], ["company", "Company A–Z"]].map(([v, l]) => `<option value="${v}" ${f.sort === v ? "selected" : ""}>${l}</option>`).join("")}</select>
          <select id="tp-since" title="Posted within">${[[0, "Any time"], [1, "Last 24h"], [3, "Last 3 days"], [7, "Last week"], [30, "Last month"]].map(([v, l]) => `<option value="${v}" ${Number(f.since_days) === v ? "selected" : ""}>${l}</option>`).join("")}</select>
          <select id="tp-region" title="Region">${[["", "Anywhere"], ["Canada", "Canada"], ["US-ok", "🇺🇸 US · open to international students"], ["US", "United States (all)"], ["Remote", "Remote"], ["Other", "International"]].map(([v, l]) => `<option value="${v}" ${f.region === v ? "selected" : ""}>${l}</option>`).join("")}</select>
          <select id="tp-term" title="Term"><option value="">Any term</option>${S.app.terms.map((x) => `<option ${f.term === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
          <label class="toggle" title="Roles you can't apply to: US-residents only, citizens only, PhD only..."><input type="checkbox" id="tp-inel" ${f.show_ineligible ? "checked" : ""}> Show ones I can't apply to</label>
        </div>
        ${fac ? `<div class="row"><span class="chip on" id="tp-facet">${esc(fac.label)} <span aria-hidden="true">✕</span></span><span class="muted small">${fac.population ? "From this track's Market: open to you, from the last 120 days" : ""}</span></div>` : ""}
      </div>
      <div class="section-title"><h2>${fac ? esc(fac.label) : "Postings"}</h2><span class="muted" id="tp-total"></span></div>
      <div class="card joblist" id="tp-list"><div class="empty"><span class="spinner"></span></div></div>
      <div class="load-more" id="tp-more"></div>`;
    const upd = (patch) => { LS.set(`tf:${id}`, { ...tfilters(id), ...patch }); drawPostings(++T.seq); };
    let qt;
    $("#tp-q").addEventListener("input", (e) => { clearTimeout(qt); qt = setTimeout(() => upd({ q: e.target.value }), 250); });
    $("#tp-sort").addEventListener("change", (e) => upd({ sort: e.target.value }));
    $("#tp-since").addEventListener("change", (e) => upd({ since_days: Number(e.target.value) }));
    $("#tp-region").addEventListener("change", (e) => upd({ region: e.target.value }));
    $("#tp-term").addEventListener("change", (e) => upd({ term: e.target.value }));
    $("#tp-inel").addEventListener("change", (e) => upd({ show_ineligible: e.target.checked }));
    $("#tp-facet")?.addEventListener("click", () => { T.facet = null; drawPostings(++T.seq); });
  }
  const shown = append ? (T.jobs || []).length : 0;
  const d = await api(postingsQuery(id, shown));
  if (tstale(t) || trackId() !== id || trackSection() !== "postings") return;
  T.jobs = append ? (T.jobs || []).concat(d.jobs) : d.jobs;
  $("#tp-total").textContent = `${d.total.toLocaleString()} posting${d.total === 1 ? "" : "s"}`;
  const list = $("#tp-list");
  list.innerHTML = T.jobs.length ? T.jobs.map(jobRow).join("") : `<div class="empty">No postings here with these filters.</div>`;
  $("#tp-more").innerHTML = T.jobs.length < d.total ? `<button class="btn btn-ghost" id="tp-more-btn">Show more (${(d.total - T.jobs.length).toLocaleString()} left)</button>` : "";
  $("#tp-more-btn")?.addEventListener("click", () => drawPostings(T.seq, true));
  bindJobActions(list, () => drawPostings(++T.seq));
}

// ---------- Companies ----------
function coState(id) { return { segment: "", q: "", chip: "", offset: 0, ...LS.get(`tco:${id}`, {}) }; }

function companyRow(r, h) {
  const where = [r.where, r.note].filter(Boolean).join(" · ");
  const open = r.open_to_you ? `<b>${r.open_to_you}</b> open to you${r.new ? ` · <span class="tag new">${r.new} new</span>` : ""}`
    : r.open ? `<span class="muted">${r.open} you can't apply to</span>` : '<span class="muted">none open</span>';
  const search = `https://www.google.com/search?q=${encodeURIComponent(r.name + " careers internship")}`;
  return `<div class="co-row ${r.hidden ? "hidden-firm" : ""}">
    <div class="co-name"><b>${esc(r.name)}</b>${r.small ? ' <span class="tag" title="Fewer people know to apply">lesser-known</span>' : ""}${(r.tags || []).map((x) => ` <span class="tag">${esc(x)}</span>`).join("")}
      ${where ? `<div class="muted small ellipsis" title="${esc(where)}">${esc(where)}</div>` : ""}</div>
    <div class="co-open small">${open}${r.best ? `<div class="muted small ellipsis" title="${esc(r.best.title)}">${esc(r.best.title)}</div>` : ""}</div>
    <div class="co-when small">${r.opens ? `Opens ${esc(r.opens.slice(0, 3))}` : ""}${r.people ? `${r.opens ? "<br>" : ""}<button class="linkish small" data-people="${esc(r.name)}" title="People you know there">👥 ${r.people}</button>` : ""}</div>
    <div class="co-act">
      ${r.open ? `<button class="btn btn-ghost btn-sm" data-co-postings="${esc(r.facet)}" data-co-name="${esc(r.name)}">Postings →</button>` : ""}
      <details class="cal"><summary class="btn btn-ghost btn-sm" title="More">⋯</summary><div class="cal-menu">
        <a href="${safeUrl(r.link || search)}" target="_blank" rel="noopener">${r.link ? "Careers page" : "Find their careers page"} ${extIcon}</a>
        <a href="${safeUrl(alumniUrl(r.name))}" target="_blank" rel="noopener">Find alumni at ${esc(r.name)} ${extIcon}</a>
        ${h.can.add_firm ? `<a href="#" data-firm="${esc(r.key)}" data-status="${r.hidden ? "shown" : "hidden"}">${r.hidden ? "Put back on the list" : "Not for me: take it off the list"}</a>` : ""}
      </div></details>
    </div></div>`;
}

async function drawCompanies(t) {
  const id = trackId(), h = T.head, st = coState(id), body = $("#t-body");
  if (!body.querySelector("#tc-list")) body.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  const p = new URLSearchParams({ profile_id: S.pid, segment: st.segment, q: st.q, chip: st.chip, offset: st.offset });
  const d = await api(`/api/tracks/${tenc(id)}/companies?${p}`);
  if (tstale(t) || trackId() !== id || trackSection() !== "companies") return;
  const chips = [["hiring", "Hiring now"], ["open", "Open to you"], ["small", "Lesser-known"], ["canada", "🇨🇦 Canada"]];
  body.innerHTML = `
    <div class="row t-co-filters">
      ${d.segments.length > 1 ? `<select id="tc-seg" title="Segment"><option value="">All ${d.total} companies</option>${d.segments.map((s) => `<option value="${esc(s.id)}" ${st.segment === s.id ? "selected" : ""}>${esc(s.name)} (${s.n})</option>`).join("")}</select>` : ""}
      <input type="search" id="tc-q" placeholder="Find a company or city…" value="${esc(st.q)}">
      ${chips.map(([k, l]) => `<button class="chip ${st.chip === k ? "on" : ""}" data-tc-chip="${k}">${l} <span class="n">${d.chips[k] ?? ""}</span></button>`).join("")}
    </div>
    <div class="summary-line">${d.shown.toLocaleString()} compan${d.shown === 1 ? "y" : "ies"}${d.shown !== d.total ? ` of ${d.total.toLocaleString()}` : ""} · <b>${d.hiring.toLocaleString()}</b> hiring now · hiring first</div>
    ${st.segment && d.segments.find((s) => s.id === st.segment)?.blurb ? `<p class="muted small">${esc(d.segments.find((s) => s.id === st.segment).blurb)}</p>` : ""}
    <div class="card" id="tc-list">${d.rows.length ? d.rows.map((r) => companyRow(r, h)).join("") : `<div class="empty">${d.total ? "No companies match." : h.can.find_more ? "No firms yet. Open ⋯ and press Find more firms, or add one." : "No companies yet."}</div>`}</div>
    ${d.shown > d.limit ? `<div class="row t-pager"><button class="btn btn-ghost btn-sm" id="tc-prev" ${st.offset ? "" : "disabled"}>← Previous</button>
      <span class="muted small">${st.offset + 1}–${Math.min(st.offset + d.limit, d.shown)} of ${d.shown}</span>
      <button class="btn btn-ghost btn-sm" id="tc-next" ${st.offset + d.limit < d.shown ? "" : "disabled"}>Next →</button></div>` : ""}
    ${d.to_review.length ? `<details class="card card-pad"><summary class="small"><b>To review (${d.to_review.length})</b>: firms found for this track. Add the ones that fit.</summary>
      ${d.to_review.map((s) => `<div class="co-row sugg"><div class="co-name"><b>${esc(s.name)}</b>${s.segment ? ` <span class="tag">${esc(s.segment)}</span>` : ""}<div class="muted small">${esc(s.why)}</div></div>
        <div class="small"><span class="tag good" title="What the live check found">✓ ${esc(s.verified || "checked")}</span></div><div></div>
        <div class="co-act">${s.link ? `<a class="icon-btn" href="${safeUrl(s.link)}" target="_blank" rel="noopener" title="Open">${extIcon}</a>` : ""}
          <button class="btn btn-sm" data-firm="${esc(s.key)}" data-status="added">Add</button><button class="btn btn-ghost btn-sm" data-firm="${esc(s.key)}" data-status="dismissed">Not for me</button></div></div>`).join("")}</details>` : ""}
    ${d.hidden.length ? `<details class="card card-pad"><summary class="small">Taken off your list (${d.hidden.length})</summary>${d.hidden.map((r) => companyRow(r, h)).join("")}</details>` : ""}`;
  const upd = (patch) => { LS.set(`tco:${id}`, { ...coState(id), offset: 0, ...patch }); drawCompanies(++T.seq); };
  $("#tc-seg")?.addEventListener("change", (e) => upd({ segment: e.target.value }));
  let qt;
  $("#tc-q").addEventListener("input", (e) => { clearTimeout(qt); qt = setTimeout(() => upd({ q: e.target.value }), 250); });
  body.querySelectorAll("[data-tc-chip]").forEach((b) => b.addEventListener("click", () => upd({ chip: st.chip === b.dataset.tcChip ? "" : b.dataset.tcChip })));
  $("#tc-prev")?.addEventListener("click", () => upd({ offset: Math.max(0, st.offset - d.limit) }));
  $("#tc-next")?.addEventListener("click", () => upd({ offset: st.offset + d.limit }));
  body.querySelectorAll("[data-co-postings]").forEach((b) => b.addEventListener("click", () =>
    openFacet(b.dataset.coPostings, `At ${b.dataset.coName}`, "")));
  body.querySelectorAll("[data-people]").forEach((b) => b.addEventListener("click", () => openPeople(b.dataset.people)));
  body.querySelectorAll("[data-firm]").forEach((b) => b.addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/niches/${tenc(h.niche)}/firms/${tenc(b.dataset.firm)}`, { method: "PUT", json: { profile_id: S.pid, status: b.dataset.status } });
      toast({ added: "Added to the list", dismissed: "Dismissed", hidden: "Taken off your list", shown: "Back on your list" }[b.dataset.status] || "Done");
      drawCompanies(++T.seq);
    } catch (err) { toast(err.message, 5000); }
  }));
}

function openFacet(facet, label, population = "market", extra = {}) {
  const id = trackId();
  T.facet = { track: id, facet, label, population, ...extra };
  LS.set(`tsec:${id}`, "postings");
  drawHead(T.head);
  drawSection(++T.seq);
}

// niche tracks: grow and edit the firm list (the same list the niche routes and the chat edit)
async function findMore(h) {
  toast(`Looking for more firms${S.app.ai_enabled ? " and checking each one live" : ""}…`, 6000);
  try {
    const r = await api(`/api/niches/${tenc(h.niche)}/find`, { method: "POST", json: { profile_id: S.pid } });
    toast(r.from_postings || r.from_ai ? `Found ${r.from_postings} in your postings${r.ai ? ` and ${r.from_ai} from ${r.ai}` : ""}, each checked live: they're under To review` : "Nothing new found this time", 6000);
    if (trackId() === h.id) { LS.set(`tsec:${h.id}`, "companies"); drawHead(h); drawSection(++T.seq); }
  } catch (e) { toast(e.message, 6000); }
}

function addFirm(h) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><form class="modal card stack" id="af-form">
    <div class="row"><h2>Add a firm to ${esc(h.name)}</h2><span class="spacer"></span><button type="button" class="icon-btn" id="af-close">✕</button></div>
    <input type="text" id="af-name" placeholder="Firm name" maxlength="80" required>
    <input type="url" id="af-url" placeholder="Careers or job-board link (optional)">
    ${h.segments.length ? `<select id="af-seg">${h.segments.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}<option value="yours">Your list</option></select>` : ""}
    <div class="row"><span class="muted small">Co-op Coach checks it's live before adding it.</span><span class="spacer"></span><button class="btn">Add</button></div>
  </form></div>`;
  const close = () => { root.innerHTML = ""; };
  $("#af-close").addEventListener("click", close);
  $("#af-name").focus();
  $("#af-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api(`/api/niches/${tenc(h.niche)}/firms`, { method: "POST", json: { profile_id: S.pid, name: $("#af-name").value.trim(), url: $("#af-url").value.trim(), segment: $("#af-seg")?.value || "yours" } });
      toast(r.restored ? `${r.name} is back on your list` : r.found ? `Added ${r.name}: ${r.verified}` : `Added ${r.name} (couldn't check it: ${r.verified})`, 5000);
      close();
      if (trackId() === h.id) { LS.set(`tsec:${h.id}`, "companies"); drawHead(h); drawSection(++T.seq); }
    } catch (err) { toast(err.message, 5000); }
  });
}

async function remindAll(h, months = []) {
  try {
    const r = await api(`/api/niches/${tenc(h.niche)}/reminders`, { method: "POST", json: { profile_id: S.pid, months } });
    toast(r.created.length ? `${r.created.length} reminder${r.created.length > 1 ? "s" : ""} added to your calendar, two weeks before firms open` : "Those reminders are already set", 5000);
  } catch (e) { toast(e.message, 5000); }
}

async function deleteTrack(h) {
  if (!confirm(`Delete the ${h.name} track and its firm list?`)) return;
  await api(`/api/tracks/${tenc(h.id)}?profile_id=${S.pid}`, { method: "DELETE" });
  if (S.filters.track === h.id) { S.filters.track = ""; LS.set("filters", S.filters); }
  LS.set("track", ""); await ensureBar(true); goView("tracks");
}

// ---------- Market ----------
async function drawMarket(t) {
  const id = trackId(), body = $("#t-body");
  body.innerHTML = `<div class="empty"><span class="spinner"></span> Reading what these postings ask for…</div>`;
  const m = await api(`/api/tracks/${tenc(id)}/market?profile_id=${S.pid}`);
  if (tstale(t) || trackId() !== id || trackSection() !== "market") return;
  const g = m.glance, pay = g.pay, fit = m.fit || {};
  const max = Math.max(1, ...m.skills.map((s) => s.count));
  const hmax = Math.max(1, ...m.history.map((x) => x.n));
  const p = profile();
  const who = m.where.who || {};
  body.innerHTML = `
    ${p && !p.resume_filename ? `<div class="banner"><b>Add your resume</b><span class="muted">Your fit and gaps need it.</span><span class="spacer"></span><button class="btn btn-sm" id="tm-resume">Add resume</button></div>` : ""}
    <p class="summary-line">From the <b>${m.size.toLocaleString()}</b> postings here that are open to you, from the last ${m.days} days. <b>${m.described.toLocaleString()}</b> have a full description: skill shares and pay come from those${m.described && m.headed < m.described ? `, and ${m.headed.toLocaleString()} split requirements from nice-to-haves` : ""}.${m.reading ? ` Reading ${m.reading} more in full now: look again in a minute.` : ""}</p>
    <div class="tiles">
      <button class="tile" data-facet="" data-label="Open to you (Market)"><span>Open to you</span><b>${g.open_to_you.toLocaleString()}</b><small>last ${m.days} days</small></button>
      <div class="tile"><span>Your fit</span><b>${g.fit == null ? "–" : `${g.fit}%`}</b><small>${g.fit != null ? "of the skills asked, on your resume" : m.described ? "add your resume" : "once postings are read in full"}</small></div>
      <div class="tile"><span>Top gaps</span><b class="tile-list">${g.gaps.length ? g.gaps.map((x) => `<a href="#" data-facet="skill:${esc(x)}" data-label="Asks for ${esc(x)}">${esc(x)}</a>`).join(", ") : "none"}</b><small>asked by 15%+, not on your resume</small></div>
      <button class="tile" data-facet="pay" data-label="States pay" ${pay.n ? "" : "disabled"}><span>Typical pay</span><b>${pay.n ? `${wage(pay.median, pay.currency)}/h` : "–"}</b><small>${pay.n ? `median of ${pay.n} that state it` : "no stated pay yet"}</small></button>
      <div class="tile"><span>Peak months</span><b>${g.peak.length ? g.peak.map(monthLabel).join(", ") : "–"}</b><small>most postings went up</small></div>
    </div>
    ${!m.size ? `<div class="card empty">No postings open to you here from the last ${m.days} days.</div>` : `
    <div class="x-grid">
      <div class="card card-pad stack">
        <div class="row"><h3>Skills they ask for</h3><span class="spacer"></span><span class="muted small">of ${m.described} with full text${m.trend_ready ? " · ↑↓ last 30 days vs the 90 before" : ""}</span></div>
        ${m.described ? `<div class="sbars t-skills">${m.skills.slice(0, 18).map((s) => `<button class="sbar ${s.status === "listed" || s.status === "surface" ? "have" : s.share >= 0.15 ? "gap" : ""}" data-facet="skill:${esc(s.skill)}" data-label="Asks for ${esc(s.skill)}" title="${s.req} list it under requirements, ${s.nice} as nice to have">
          <span class="sname">${esc(s.skill)}</span>
          <span class="track"><span class="fill" style="width:${Math.round(100 * s.count / max)}%"></span><span class="req" style="width:${Math.round(100 * s.req / max)}%"></span></span>
          <span class="n">${pctOf(s.share)}${m.trend_ready && s.trend && s.trend !== "flat" ? ` <span class="${s.trend === "up" ? "up" : "down"}">${s.trend === "up" ? "↑" : "↓"}</span>` : ""}</span>
          <span>${s.status === "listed" ? '<span class="tag good">✓ yours</span>' : s.status === "surface" ? '<span class="tag good">in bullets</span>' : s.share >= 0.15 ? '<span class="tag warn">gap</span>' : '<span class="tag">–</span>'}</span>
        </button>`).join("")}</div>
          <div class="muted small"><span class="key req"></span> darker: listed under requirements · click a skill for its postings</div>
          ${g.gaps.length ? `<div class="row"><button class="btn btn-sm" id="tm-studio">Close the gaps in Resume Studio →</button><span class="muted small">Same numbers, skill by skill: surface it, confirm it, or plan a project.</span></div>` : ""}`
          : `<div class="muted small">None of these postings has a full description yet. Open a few, or sync: Co-op Coach reads more each time.</div>`}
      </div>
      <div class="stack">
        <div class="card card-pad stack">
          <h3>Pay</h3>
          ${pay.n ? `<div><b>${wage(pay.median, pay.currency)}/h</b> median · middle half ${wage(pay.p25, pay.currency)}–${wage(pay.p75, pay.currency)}/h <span class="muted small">(${pay.currency === "$" ? "dollars, country not stated" : pay.currency})</span></div>
            <div class="muted small">From <a href="#" data-facet="pay" data-label="States pay">${pay.stated} posting${pay.stated === 1 ? "" : "s"}</a> of ${pay.described} with full text that state pay${Object.keys(pay.other_currencies || {}).length ? `; ${Object.entries(pay.other_currencies).map(([c, k]) => `${k} in ${c === "$" ? "unstated dollars" : c}`).join(", ")} not mixed in` : ""}. Yearly and monthly pay shown per hour (a year is 2,080 hours). Nothing converted, nothing guessed.</div>
            <div class="stack small">${pay.quotes.map((q) => `<div class="quote">“${esc(q.quote)}” <span class="muted">— ${esc(q.company)}</span></div>`).join("")}</div>`
            : `<div class="muted small">None of the ${pay.described} postings with full text state pay yet.</div>`}
        </div>
        <div class="card card-pad stack">
          <h3>Where & when</h3>
          ${m.where.terms.length ? `<div><div class="filter-label">Terms</div><div class="chips">${m.where.terms.map((x) => `<button class="chip" data-facet="term:${esc(x.term)}" data-label="${esc(x.term)}">${esc(x.term)} <span class="n">${x.n}</span></button>`).join("")}</div></div>` : ""}
          ${m.where.cities.length ? `<div><div class="filter-label">Cities</div><div class="chips">${m.where.cities.map((x) => `<button class="chip" data-facet="city:${esc(x.city)}" data-label="In ${esc(x.city)}">${esc(x.city)} <span class="n">${x.n}</span></button>`).join("")}
            ${m.where.remote ? `<button class="chip" data-facet="remote" data-label="Remote">Remote <span class="n">${m.where.remote}</span></button>` : ""}</div></div>` : ""}
          <div><div class="filter-label">Who can apply (every open posting here)</div><div class="chips">
            ${[["ok", "You can apply", ""], ["check", "Check eligibility", "warn"], ["no", "You can't", "bad"]].filter(([k]) => who[k]).map(([k, l]) => `<button class="chip" data-facet="elig:${k}" data-label="${l}" data-all="1">${l} <span class="n">${who[k]}</span></button>`).join("")}</div></div>
          <div><div class="filter-label">Posted per month, open and closed</div>
            <div class="hist">${m.history.map((x) => `<div class="hbar" title="${monthLabel(x.month)}: ${x.n}"><span style="height:${Math.round(100 * x.n / hmax)}%"></span><small>${MONTH_SHORT[Number(x.month.slice(5)) - 1][0]}</small></div>`).join("")}</div></div>
          ${m.openings && m.openings.months.length ? `<div><div class="filter-label">When firms open${m.openings.season ? ` (${esc(m.openings.season)})` : ""}</div>
            <div class="months">${m.openings.months.map((mo) => `<div class="month" title="${esc(mo.firms.map((f) => f.name).join(", "))}"><b>${esc(mo.name.slice(0, 3))}</b><span>${mo.firms.length} firm${mo.firms.length > 1 ? "s" : ""}</span>
              <button class="btn btn-ghost btn-sm" data-remind="${mo.month}" title="A reminder two weeks before ${esc(mo.name)}${mo.season ? ` (${esc(mo.season)} hiring)` : ""}">🔔</button></div>`).join("")}</div></div>` : ""}
        </div>
      </div>
    </div>`}`;
  $("#tm-resume")?.addEventListener("click", () => openSettings("profile"));
  $("#tm-studio")?.addEventListener("click", () => { LS.set("docsub", "studio"); LS.set("fitcat", `track:${id}`); switchTab("documents"); });
  body.querySelectorAll("[data-facet]").forEach((b) => b.addEventListener("click", (e) => {
    e.preventDefault();
    const all = b.dataset.all === "1";
    openFacet(b.dataset.facet, b.dataset.label, all ? "" : "market", all ? { show_ineligible: true } : {});
  }));
  body.querySelectorAll("[data-remind]").forEach((b) => b.addEventListener("click", () => remindAll(T.head, [Number(b.dataset.remind)])));
}

// ---------- ＋ Tracks ----------
function trackCard(c) {
  const full = !c.pinned && (S.trackBar || []).length >= (S.trackMax || 6);
  return `<div class="card track-card">
    <button class="track-open" data-topen="${esc(c.id)}">
      <div class="row"><b>${esc(c.icon || "")} ${esc(c.name)}</b>${TREND_TAG[c.trend] || ""}${c.following ? '<span class="tag good">Following</span>' : ""}</div>
      ${c.blurb ? `<div class="muted small clamp">${esc(c.blurb)}</div>` : ""}
      <div class="small"><b>${c.open_to_you.toLocaleString()}</b> open to you${c.new_week ? ` · <span class="tag new">${c.new_week} new this week</span>` : ""}</div>
    </button>
    <button class="btn btn-sm ${c.pinned ? "btn-ghost" : ""}" data-tpin="${esc(c.id)}" data-on="${c.pinned ? 0 : 1}" ${full ? `disabled title="Your Jobs bar is full: unpin one first"` : ""}>${c.pinned ? "Unpin" : "📌 Pin"}</button>
  </div>`;
}

async function renderTracks() {
  const main = $("#main");
  ensureBar();
  renderNav();
  main.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  const t = ++T.seq;
  const d = await api(`/api/tracks?profile_id=${S.pid}`);
  if (tstale(t)) return;
  T.list = d;
  const group = (title, cards, note = "") => cards.length ? `<div class="section-title"><h2>${title}</h2>${note ? `<span class="muted small">${note}</span>` : ""}</div>
    <div class="track-cards">${cards.map(trackCard).join("")}</div>` : "";
  main.innerHTML = `
    <section class="card card-pad stack interns-ask">
      <h2 style="margin:0">Does a company hire interns?</h2>
      <form class="row" id="int-form"><input type="search" id="int-q" placeholder="Type a company, like Notion or Jane Street" style="flex:1;min-width:200px" autocomplete="off"><button class="btn">Check</button></form>
      <div id="int-out"></div>
    </section>
    <p class="summary-line">A track is a group of jobs: a kind of role (like Machine Learning) or of company (like Big Tech or New AI startups). Pin up to ${d.max_pins} and they show up under Jobs.</p>
    ${group("Your tracks", d.pins, `${d.pins.length} of ${d.max_pins} pinned`)}
    ${group("Suggested for you", d.suggested, "from your targets, what you follow and what you aim for")}
    ${group("New & rising", d.rising, "kinds of role more companies started posting")}
    ${group("Roles", d.roles)}
    ${group("Employers", d.employers)}
    ${group("Yours", d.yours)}
    ${group("More roles in the feed", d.more_roles, "kinds of role many companies post that aren't a track above")}
    <div class="section-title"><h2>Start your own</h2><span class="muted small">a kind of employer: Co-op Coach fills it from your postings${S.app.ai_enabled ? ` and ${esc(aiName())} suggests firms (each checked live)` : ""}</span></div>
    <div class="card card-pad stack">
      <form class="row" id="tr-new"><input type="text" id="tr-new-name" placeholder="e.g. Climate tech, Game studios, Biotech" style="flex:1" maxlength="60"><button class="btn btn-sm">Create</button></form>
      <div class="chips">${d.starters.map((s) => `<button class="chip" data-starter="${esc(s.name)}">${esc(s.name)}</button>`).join("")}</div>
    </div>`;
  $("#int-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#int-q").value.trim();
    if (!q) return;
    const out = $("#int-out");
    out.innerHTML = '<span class="spinner"></span>';
    try {
      const r = await api(`/api/interns?profile_id=${S.pid}&q=${encodeURIComponent(q)}`);
      out.innerHTML = `<div class="int-answer ${r.verdict}"><p class="int-line">${esc(r.answer)}</p>
        ${r.open_now.length ? `<ul class="int-open">${r.open_now.map((j) => `<li><a href="${safeUrl(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a></li>`).join("")}</ul>` : ""}
        <div class="tel">${[r.tier, r.seasons.length ? `seen: ${r.seasons.slice(-4).join(", ")}` : "", r.usual_month ? `usually opens ${r.usual_month}` : "", r.watched ? "we check their job page" : ""].filter(Boolean).map(esc).join(" · ")}</div>
        <div class="row" style="margin-top:8px"><button class="btn btn-ghost btn-sm" id="int-follow">Follow ${esc(r.company)}</button><span class="muted small">You'll get a heads-up when they post a new internship.</span></div></div>`;
      $("#int-follow").addEventListener("click", async () => {
        await toggleFav("company", r.key, false, r.company);
        toast(`Following ${r.company}`);
      });
    } catch (err) { out.innerHTML = `<div class="muted small">${esc(err.message)}</div>`; }
  });
  main.querySelectorAll("[data-topen]").forEach((b) => b.addEventListener("click", () => openTrack(b.dataset.topen)));
  main.querySelectorAll("[data-tpin]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const r = await api(`/api/tracks/${tenc(b.dataset.tpin)}/pin`, { method: "PUT", json: { profile_id: S.pid, on: b.dataset.on === "1" } });
      toast(r.pinned ? `${r.track} is in your Jobs bar` : `${r.track} is out of your Jobs bar`);
      await ensureBar(true); renderTracks();
    } catch (e) { toast(e.message, 5000); }
  }));
  const create = async (name) => {
    if (!name) return;
    try {
      const r = await api("/api/tracks", { method: "POST", json: { profile_id: S.pid, name } });
      await ensureBar(true);
      openTrack(r.id, "companies");
      findMore({ ...T.head, id: r.id, niche: r.niche, name: r.name });
    } catch (e) { toast(e.message, 5000); }
  };
  $("#tr-new").addEventListener("submit", (e) => { e.preventDefault(); create($("#tr-new-name").value.trim()); });
  main.querySelectorAll("[data-starter]").forEach((b) => b.addEventListener("click", () => create(b.dataset.starter)));
}

// the Track picker in Matches and Resume Studio: every track, pinned ones first
async function trackOptions(selected) {
  if (!T.list || T.listPid !== S.pid) { T.list = await api(`/api/tracks?profile_id=${S.pid}`); T.listPid = S.pid; }
  const d = T.list, seen = new Set();
  const opt = (c) => { if (seen.has(c.id)) return ""; seen.add(c.id); return `<option value="${esc(c.id)}" ${selected === c.id ? "selected" : ""}>${esc(c.icon || "")} ${esc(c.name)}</option>`; };
  const grp = (label, cards) => { const o = cards.map(opt).join(""); return o ? `<optgroup label="${esc(label)}">${o}</optgroup>` : ""; };
  return grp("Your tracks", d.pins) + grp("Roles", d.roles) + grp("Employers", [...d.employers, ...d.yours]) + grp("Found in the feed", [...d.rising, ...d.more_roles]);
}
