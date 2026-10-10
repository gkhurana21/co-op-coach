/* Documents (Resume Studio, cover letters, formats), application packages, Workday logins, chat.
   Loaded before app.js; uses its helpers ($, api, esc, toast, S, ...) at call time. */

const D = { sub: "studio", resume: null, lint: null, template: "jake", saveT: null, lintT: null, dirty: false, suggestions: {} };
const SEV_LABEL = { high: "Fix", medium: "Improve", low: "Polish" };
const CAT_LABEL = { impact: "Impact & clarity", brevity: "Brevity", style: "Style", completeness: "Completeness" };
const CAT_MAX = { impact: 40, brevity: 20, style: 20, completeness: 20 };

async function renderDocuments() {
  const main = $("#main");
  D.sub = LS.get("docsub", "studio");
  main.innerHTML = `<div class="subnav">
      ${[["studio", "Resume Studio"], ["letters", "Cover letters"]].map(([k, l]) =>
        `<button class="chip ${D.sub === k ? "on" : ""}" data-sub="${k}">${l}</button>`).join("")}
    </div><div id="docs-body"><div class="empty"><span class="spinner"></span></div></div>`;
  main.querySelectorAll("[data-sub]").forEach((b) => b.addEventListener("click", () => { LS.set("docsub", b.dataset.sub); renderDocuments(); }));
  if (D.sub === "letters" || D.sub === "formats") return renderLetters();
  return renderStudio();
}

// ---------- Resume Studio ----------
async function renderStudio() {
  const data = await api(`/api/resume?profile_id=${S.pid}`);
  const body = $("#docs-body");
  if (!data.resume) {
    body.innerHTML = `<div class="card empty">Add your resume in <a href="#" id="to-coach">Settings → Profile</a> first.</div>`;
    $("#to-coach").addEventListener("click", (e) => { e.preventDefault(); openSettings("profile"); });
    return;
  }
  D.resume = data.resume; D.lint = data.lint; D.template = data.template; D.suggestions = {};
  body.innerHTML = `
    <div class="studio">
      <div class="card" id="editor"></div>
      <div class="studio-side">
        <div class="card card-pad stack">
          <div class="score-card"><div><div class="score-big" id="score-num"></div><div class="muted small" id="score-grade"></div></div><div class="bars" id="score-bars"></div></div>
          <div class="row">
            <select id="tpl">${Object.entries(data.templates).map(([k, l]) => `<option value="${k}" ${k === D.template ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
            <span class="spacer"></span>
            <span class="muted small" id="save-state">${data.built_by === "you" ? "Saved" : `Built from your upload (${byLabel(data.built_by)})`}</span>
          </div>
          <div class="row">
            <a class="btn btn-sm" id="dl-pdf" target="_blank" rel="noopener">⬇ PDF</a>
            <details class="cal"><summary class="btn btn-ghost btn-sm">More ▾</summary><div class="cal-menu">
              <a id="dl-docx">Word (.docx)</a>
              <a id="dl-tex" title="Jake's Resume LaTeX source">LaTeX source</a>
              ${data.latex_compiler ? `<a id="dl-latexpdf" target="_blank" rel="noopener">Jake's LaTeX PDF</a>` : ""}
              <a href="#" id="overleaf" title="Sends your resume to Overleaf">Open in Overleaf</a>
              <a href="#" id="compare">Check against my uploaded file</a></div></details>
            <button class="btn btn-ghost btn-sm" id="improve-all">✨ Fix weak bullets</button>
          </div>
          <div class="row"><b class="small">What to fix</b> <span class="muted small" id="issue-count"></span><span class="spacer"></span><button class="btn btn-sm hidden" id="fix-all">Fix all small things</button></div>
          <div class="issue-list" id="issues"></div>
        </div>
        <div class="card card-pad stack" id="coach-card">
          <div class="row"><h3 style="margin:0">🧠 Resume Coach</h3><span class="spacer"></span>
            <button class="btn btn-sm" id="coach-run">${S.app.ai_enabled ? "Review my resume" : "Quick check"}</button></div>
          <div class="muted small" id="coach-hint">${S.app.ai_enabled ? `${aiName()} reads the whole thing like a recruiter and suggests only the few changes that matter. It never invents numbers or facts.` : "Rule-based check. Add a free Groq key for a full AI review."}</div>
          <div id="coach-out" class="stack"></div>
        </div>
        <div class="card card-pad stack" id="style-card"></div>
        <div class="fit-line small" id="fit-line"></div>
        <div class="preview-wrap" id="pv"><iframe id="preview" title="Resume preview"></iframe></div>
      </div>
    </div>`;
  $("#tpl").addEventListener("change", (e) => { D.template = e.target.value; changed(true); });
  $("#compare").addEventListener("click", (e) => { e.preventDefault(); e.target.closest("details").open = false; openCompare(); });
  $("#fix-all").addEventListener("click", () => applyFixes("auto"));
  $("#improve-all").addEventListener("click", improveWeak);
  $("#coach-run").addEventListener("click", runCoach);
  $("#overleaf").addEventListener("click", async (e) => { e.preventDefault(); if (D.dirty) await new Promise((r) => setTimeout(r, 1300)); openInOverleaf(); });
  updateDownloads();
  drawStyle(); drawEditor(); drawScore(); drawPreview();
  new ResizeObserver(fitPreview).observe($("#pv"));
}

// ---------- 🧠 Resume Coach ----------
async function runCoach() {
  const btn = $("#coach-run"), out = $("#coach-out");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Reading your resume…';
  try {
    const r = await api("/api/resume/review", { method: "POST", json: { profile_id: S.pid, resume: D.resume } });
    D.review = r.review; D.reviewBy = r.by; drawCoach();
  } catch (e) { toast(e.message, 6000); }
  btn.disabled = false; btn.textContent = "Review again";
}

function getPath(path) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[isNaN(k) ? k : Number(k)]), D.resume);
}

function drawCoach() {
  const v = D.review, out = $("#coach-out");
  if (!v || !out) return;
  const cls = { "Ready to send": "good", "Almost there": "", "Needs work": "warn" }[v.verdict] || "";
  out.innerHTML = `
    <div class="row"><span class="tag ${cls}">${esc(v.verdict)}</span><span class="muted small">${D.reviewBy === "rules" ? "rule-based" : `by ${aiName()}`}</span></div>
    <p style="margin:0">${esc(v.first_impression)}</p>
    ${(v.strengths || []).length ? `<div><div class="filter-label">Keep as is</div><ul class="clean small">${v.strengths.map((s) => `<li>✅ ${esc(s)}</li>`).join("")}</ul></div>` : ""}
    ${(v.changes || []).length ? `<div class="stack" style="gap:8px"><div class="filter-label">Changes that matter</div>
      ${v.changes.map((c, n) => `<div class="coach-change ${c.done ? "done" : ""}" data-ch="${n}">
        <div><b>${n + 1}. ${esc(c.title)}</b> ${c.done ? `<span class="tag good">${c.done}</span>` : ""}</div>
        <div class="muted small">${esc(c.why)}</div>
        ${(c.path || c.fix_id) && c.after ? `<div class="diff"><div class="del">${esc(c.before)}</div><div class="ins">${esc(c.after)}</div></div>
          ${c.done ? "" : `<div class="row"><button class="btn btn-sm" data-accept="${n}">Accept</button><button class="btn btn-ghost btn-sm" data-skip="${n}">Skip</button>
          <button class="btn btn-ghost btn-sm" data-goto="${n}">Show me</button></div>`}` : (c.after ? `<div class="small">${esc(c.after)}</div>` : "")}
      </div>`).join("")}</div>` : ""}
    ${(v.questions || []).length ? `<div><div class="filter-label">Questions for you</div><ul class="clean small">${v.questions.map((q) => `<li>❓ ${esc(q)}</li>`).join("")}</ul>
      <div class="muted small">Answer one in the box below and the coach will turn it into a line.</div></div>` : ""}
    <form class="row" id="coach-ask"><input type="text" id="coach-q" placeholder="Ask the coach: “make the RBC bullets more technical”" style="flex:1"><button class="btn btn-ghost btn-sm">Ask</button></form>
    <div id="coach-reply" class="small"></div>`;
  out.querySelectorAll("[data-accept]").forEach((b) => b.addEventListener("click", () => {
    const c = v.changes[Number(b.dataset.accept)];
    if (c.fix_id) { applyFixes([c.fix_id]).then(() => { c.done = "Applied"; drawCoach(); }); return; }
    if (getPath(c.path) === undefined) return toast("That line moved; review again");
    setPath(c.path, c.after); c.done = "Applied"; drawEditor(); drawCoach(); changed(true);
  }));
  out.querySelectorAll("[data-skip]").forEach((b) => b.addEventListener("click", () => { v.changes[Number(b.dataset.skip)].done = "Skipped"; drawCoach(); }));
  out.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => {
    if (!v.changes[Number(b.dataset.goto)].path) return;
    const [sec, i, , j] = v.changes[Number(b.dataset.goto)].path.split(".");
    const t = document.querySelector(`[data-b="${sec}.${i}.${j}"]`) || document.querySelector(`[data-entry="${sec}.${i}"]`);
    if (t) { t.scrollIntoView({ behavior: "smooth", block: "center" }); t.classList.remove("flash"); void t.offsetWidth; t.classList.add("flash"); }
  }));
  $("#coach-ask").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#coach-q").value.trim(); if (!q) return;
    $("#coach-reply").innerHTML = '<span class="spinner"></span> Thinking…';
    try {
      const r = await api("/api/chat", { method: "POST", json: { profile_id: S.pid, focus: "resume", resume: D.resume, messages: [{ role: "user", content: q }] } });
      $("#coach-reply").innerHTML = linkify(r.reply).replace(/\n/g, "<br>");
    } catch (err) { $("#coach-reply").textContent = err.message; }
  });
}

// ---------- look & feel: colours, contact style, layout, one page ----------
const PALETTE = [["Classic black", "#000000"], ["Navy", "#1f3a68"], ["Ocean", "#0b5c8a"], ["Teal", "#0f6e73"], ["Forest", "#1f6f50"],
  ["Waterloo gold", "#8a6a00"], ["Burgundy", "#7a1f2b"], ["Crimson", "#b4232f"], ["Royal purple", "#4338ca"], ["Plum", "#6b2d6b"],
  ["Slate", "#334155"], ["Charcoal", "#2b2b2b"]];
function drawStyle() {
  const st = D.resume.style || (D.resume.style = {});
  const sw = (key) => PALETTE.map(([n, c]) => `<button type="button" class="swatch ${st[key] === c ? "on" : ""}" data-sw="${key}|${c}" title="${n}" style="background:${c}"></button>`).join("");
  const seg = (key, opts) => `<div class="seg">${opts.map(([v, l]) => `<button type="button" class="${st[key] === v ? "on" : ""}" data-seg="${key}|${v}">${l}</button>`).join("")}</div>`;
  const tog = (key, label) => `<label class="switch"><input type="checkbox" data-tog="${key}" ${st[key] ? "checked" : ""}><span></span>${label}</label>`;
  $("#style-card").innerHTML = `
    <div class="row"><h3 style="margin:0">🎨 Look</h3><span class="spacer"></span><span class="muted small">applies to PDF, Word and LaTeX</span></div>
    <div><div class="filter-label">Name & headings</div><div class="swatches">${sw("accent")}<label class="swatch custom" title="Any colour"><input type="color" data-color="accent" value="${st.accent || "#000000"}"></label></div></div>
    <div><div class="filter-label">Links</div><div class="swatches">${sw("link_color")}<label class="swatch custom" title="Any colour"><input type="color" data-color="link_color" value="${st.link_color || "#000000"}"></label></div></div>
    <div><div class="filter-label">Contact line</div>${seg("contact", [["icons", "Icons + labels"], ["emoji", "😀 Emoji + labels"], ["plain", "Plain links"]])}
      <div class="muted small" style="margin-top:4px">${st.contact === "plain" ? "Shows full addresses (github.com/you)." : "Shows “GitHub”, “LinkedIn”… each one clickable."}${st.contact === "emoji" ? " LaTeX uses icons instead (it can't print emoji)." : ""}</div></div>
    <div class="stack" style="gap:8px">
      ${tog("one_page", "Fit to one page (shrinks type and spacing just enough)")}
      ${tog("link_icons", "Little link icon next to companies & projects")}
      ${tog("company_first", "Company above job title")}
    </div>`;
  const card = $("#style-card");
  const set = (k, v) => { st[k] = v; drawStyle(); changed(true); };
  card.querySelectorAll("[data-sw]").forEach((b) => b.addEventListener("click", () => { const [k, c] = b.dataset.sw.split("|"); set(k, c); }));
  card.querySelectorAll("[data-color]").forEach((i) => i.addEventListener("change", () => set(i.dataset.color, i.value)));
  card.querySelectorAll("[data-seg]").forEach((b) => b.addEventListener("click", () => { const [k, v] = b.dataset.seg.split("|"); set(k, v); }));
  card.querySelectorAll("[data-tog]").forEach((i) => i.addEventListener("change", () => set(i.dataset.tog, i.checked)));
}

function showFit() {
  const fr = $("#preview"), line = $("#fit-line");
  if (!fr || !line) return;
  const f = fr.contentWindow && fr.contentWindow.__fit;
  if (!D.resume.style?.one_page) { line.innerHTML = '<span class="muted">One-page fitting is off.</span>'; return; }
  if (!f) { line.textContent = ""; return; }
  D.fitLevel = f.level;
  updateDownloads();
  const lines = Math.ceil(f.over / 15);
  line.innerHTML = f.fits ? `<span class="tag good">✓ Fits on one page</span> <span class="muted">body text ${f.pt}pt${f.level ? ", spacing tightened" : ""}</span>`
    : `<span class="tag warn">Still ${f.pages} pages at the smallest readable size</span> <span class="muted">cut about ${lines} line${lines === 1 ? "" : "s"}: your weakest bullets are flagged in “What to fix”.</span>`;
}

function updateDownloads() {
  $("#dl-pdf").href = `/api/resume/export?profile_id=${S.pid}&fmt=pdf&template=${D.template}`;
  $("#dl-docx").href = `/api/resume/export?profile_id=${S.pid}&fmt=docx${D.fitLevel != null ? `&level=${D.fitLevel}` : ""}`;
  $("#dl-tex").href = `/api/resume/export?profile_id=${S.pid}&fmt=tex`;
  if ($("#dl-latexpdf")) $("#dl-latexpdf").href = `/api/resume/export?profile_id=${S.pid}&fmt=latexpdf`;
}

async function openInOverleaf() {
  // Overleaf's public "open a snippet" endpoint: posts the LaTeX into a new project in a new tab
  const r = await fetch(`/api/resume/export?profile_id=${S.pid}&fmt=tex`);
  if (!r.ok) return toast("Couldn't build the LaTeX");
  const form = document.createElement("form");
  form.method = "POST"; form.action = "https://www.overleaf.com/docs"; form.target = "_blank";
  for (const [k, v] of [["snip", await r.text()], ["snip_name", "resume.tex"], ["engine", "pdflatex"]]) {
    const i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; form.appendChild(i);
  }
  document.body.appendChild(form); form.submit(); form.remove();
}

function fitPreview() {
  const wrap = $("#pv"), fr = $("#preview");
  if (!wrap || !fr) return;
  const scale = wrap.clientWidth / 816;
  fr.style.transform = `scale(${scale})`;
  wrap.style.height = `${1056 * scale}px`;
}

async function drawPreview() {
  const html = await fetch("/api/resume/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ resume: D.resume, template: D.template }) }).then((r) => r.text());
  const fr = $("#preview");
  if (fr) { fr.onload = () => setTimeout(showFit, 60); fr.srcdoc = html; fitPreview(); }
}

function drawScore() {
  const L = D.lint;
  $("#score-num").textContent = L.score;
  $("#score-num").style.color = L.score >= 85 ? "var(--good)" : L.score >= 72 ? "var(--accent)" : L.score >= 55 ? "var(--warn)" : "var(--bad)";
  $("#score-grade").textContent = L.grade;
  $("#score-bars").innerHTML = Object.entries(L.scores).map(([k, v]) => `<div class="bar"><span>${CAT_LABEL[k]}</span><div class="track"><div class="fill" style="width:${(100 * v) / CAT_MAX[k]}%"></div></div><span class="muted">${v}/${CAT_MAX[k]}</span></div>`).join("");
  const groups = { mistake: [], clarity: [], polish: [] };
  L.issues.forEach((i, n) => groups[i.kind || "clarity"].push([i, n]));
  const auto = L.issues.filter((i) => i.auto).length;
  $("#issue-count").textContent = L.issues.length ? `${groups.mistake.length} mistake${groups.mistake.length === 1 ? "" : "s"} · ${groups.clarity.length} to improve` : "";
  $("#fix-all").classList.toggle("hidden", !auto);
  $("#fix-all").textContent = `Fix all small things (${auto})`;
  const row = ([i, n]) => `<div class="issue" data-issue="${n}"><span class="sev sev-${i.kind === "mistake" ? "high" : i.severity}"></span><span style="flex:1">${esc(i.message)}</span>${i.op ? `<button class="chip" data-fix="${esc(i.id)}">Fix</button>` : ""}</div>`;
  $("#issues").innerHTML = (groups.mistake.length ? `<div class="filter-label">Mistakes</div>${groups.mistake.map(row).join("")}` : "")
    + (groups.clarity.length ? `<div class="filter-label" style="margin-top:8px">Make it clearer</div>${groups.clarity.map(row).join("")}` : "")
    + (groups.polish.length ? `<details style="margin-top:6px"><summary class="small muted" style="cursor:pointer">Polish (${groups.polish.length})</summary>${groups.polish.map(row).join("")}</details>` : "")
    || '<div class="muted small">Nothing to fix. Nice.</div>';
  document.querySelectorAll("#issues [data-fix]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); applyFixes([b.dataset.fix]); }));
  document.querySelectorAll("[data-issue]").forEach((el) => el.addEventListener("click", () => {
    const i = L.issues[Number(el.dataset.issue)];
    if (!i.path) return;
    const sel = i.path.length === 4 ? `[data-b="${i.path[0]}.${i.path[1]}.${i.path[3]}"]` : `[data-entry="${i.path[0]}.${i.path[1]}"]`;
    const t = document.querySelector(sel);
    if (t) { t.scrollIntoView({ behavior: "smooth", block: "center" }); t.classList.remove("flash"); void t.offsetWidth; t.classList.add("flash"); }
  }));
  // per-bullet chips
  document.querySelectorAll(".bullet-row").forEach((row) => {
    const [sec, i, j] = row.dataset.b.split(".");
    const mine = L.issues.filter((x) => x.path && x.path.length === 4 && x.path[0] === sec && x.path[1] === Number(i) && x.path[3] === Number(j));
    row.classList.toggle("has-high", mine.some((x) => x.kind === "mistake"));
    row.classList.toggle("has-medium", !mine.some((x) => x.kind === "mistake") && mine.some((x) => x.severity === "medium"));
    const box = row.querySelector(".b-issues");
    if (box) box.innerHTML = mine.map((x) => `<span class="tag ${x.kind === "mistake" ? "bad" : x.severity === "medium" ? "warn" : ""}" title="${esc(x.message)}">${esc(x.message.split(/[.(]/)[0].slice(0, 60))}</span>`).join("")
      + (mine.find((x) => x.op) ? `<button class="chip" data-quickfix="${esc(mine.find((x) => x.op).id)}">Fix</button>` : "");
    box?.querySelector("[data-quickfix]")?.addEventListener("click", (e) => applyFixes([e.currentTarget.dataset.quickfix]));
  });
}

async function applyFixes(ids) {
  try {
    const before = JSON.parse(JSON.stringify(D.resume));
    const r = await api("/api/resume/fix", { method: "POST", json: { profile_id: S.pid, resume: D.resume, ids } });
    if (!r.applied) return toast("Nothing to change there");
    D.resume = r.resume; D.lint = r.lint; D.undo = before;
    drawEditor(); drawScore(); drawPreview(); changed(true);
    toastUndo(`Fixed ${r.applied} thing${r.applied === 1 ? "" : "s"}`);
  } catch (e) { toast(e.message, 5000); }
}

function toastUndo(msg) {
  const t = $("#toast");
  t.innerHTML = `${esc(msg)} <button class="chip" id="toast-undo">Undo</button>`;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 6000);
  $("#toast-undo").addEventListener("click", () => {
    if (!D.undo) return;
    D.resume = D.undo; D.undo = null; drawEditor(); drawPreview(); changed(true); t.classList.remove("show");
  });
}

// ---------- check against the uploaded file ----------
async function openCompare() {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide"><div class="empty"><span class="spinner"></span> Re-reading your file…</div></div></div>`;
  const close = () => (root.innerHTML = "");
  let d;
  try { d = await api("/api/resume/compare", { method: "POST", json: { profile_id: S.pid, resume: D.resume } }); }
  catch (e) { close(); return toast(e.message, 6000); }
  const act = d.items.filter((x) => x.kind !== "only_mine"), mine = d.items.filter((x) => x.kind === "only_mine");
  const val = (v) => esc(String(v || "").slice(0, 400)) || '<span class="muted">(empty)</span>';
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide stack">
    <div class="row"><h2>Your copy vs. your file</h2><span class="spacer"></span><button class="icon-btn" id="cmp-close">✕</button></div>
    <p class="muted small" style="margin:0">Your edits are kept unless you tick something. Ticked ones (pre-ticked where it's clearly a fix: typos, missing links, blanks) take the file's version.</p>
    ${d.small_fixes ? `<div class="banner">${d.small_fixes} small fix${d.small_fixes === 1 ? "" : "es"} (split lines, spaces, capitals) would make this list cleaner. <button class="btn btn-sm" id="cmp-fix">Fix them first</button></div>` : ""}
    ${act.length ? `<div class="cmp-list">${act.map((x) => `<label class="cmp-row">
      <input type="checkbox" data-cmp="${esc(x.id)}" ${x.suggested ? "checked" : ""}>
      <div style="min-width:0;flex:1"><div class="small"><b>${esc(x.label)}</b>${x.untouched ? ' <span class="tag good">you never edited this</span>' : ""}</div>
        ${x.kind === "missing" ? `<div class="diff"><div class="ins">${val(x.file)}</div></div>` : `<div class="diff"><div class="del">${val(x.mine)}</div><div class="ins">${val(x.file)}</div></div>`}</div></label>`).join("")}</div>`
      : '<div class="empty small">Your copy already has everything from the file.</div>'}
    ${mine.length ? `<details class="small"><summary class="muted" style="cursor:pointer">Only in your copy (${mine.length}): kept as is</summary><ul class="clean">${mine.map((x) => `<li>${esc(x.label)}</li>`).join("")}</ul></details>` : ""}
    <div class="row"><button class="btn btn-ghost btn-sm" id="cmp-over" title="Throw away this copy and rebuild it from the file">Start over from my file</button><span class="spacer"></span>
      <button class="btn btn-ghost" id="cmp-all">Tick all</button><button class="btn" id="cmp-apply">Take the ticked ones</button></div>
  </div></div>`;
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#cmp-close").addEventListener("click", close);
  $("#cmp-fix")?.addEventListener("click", async () => { await applyFixes("auto"); openCompare(); });
  $("#cmp-all").addEventListener("click", () => root.querySelectorAll("[data-cmp]").forEach((c) => (c.checked = true)));
  $("#cmp-apply").addEventListener("click", async () => {
    const ids = [...root.querySelectorAll("[data-cmp]:checked")].map((c) => c.dataset.cmp);
    if (!ids.length) return close();
    try {
      const before = JSON.parse(JSON.stringify(D.resume));
      const r = await api("/api/resume/compare/apply", { method: "POST", json: { profile_id: S.pid, resume: D.resume, ids } });
      D.resume = r.resume; D.lint = r.lint; D.undo = before;
      close(); drawStyle(); drawEditor(); drawScore(); drawPreview(); changed(true);
      toastUndo(`Took ${r.applied} change${r.applied === 1 ? "" : "s"} from your file`);
    } catch (e) { toast(e.message, 6000); }
  });
  $("#cmp-over").addEventListener("click", async () => {
    if (!confirm("Throw away this copy (and your edits) and rebuild it from the file?")) return;
    try {
      const r = await api("/api/resume/rebuild", { method: "POST", json: { profile_id: S.pid } });
      D.resume = r.resume; D.lint = r.lint; close(); drawStyle(); drawEditor(); drawScore(); drawPreview();
      toast("Rebuilt from your file"); loadState();
    } catch (e) { toast(e.message, 6000); }
  });
}

function changed(redrawPreviewNow = false) {
  D.dirty = true;
  $("#save-state").textContent = "Unsaved changes…";
  updateDownloads();
  clearTimeout(D.lintT);
  D.lintT = setTimeout(async () => {
    D.lint = await api("/api/resume/lint", { method: "POST", json: { resume: D.resume } });
    drawScore(); drawPreview();
  }, redrawPreviewNow ? 0 : 450);
  clearTimeout(D.saveT);
  D.saveT = setTimeout(async () => {
    await api("/api/resume", { method: "PUT", json: { profile_id: S.pid, resume: D.resume, template: D.template } });
    D.dirty = false;
    const st = $("#save-state"); if (st) st.textContent = "Saved ✓";
  }, 1200);
}

const SEC_TITLES = { education: "Education", experience: "Experience", projects: "Projects", skills: "Skills", extras: "Leadership & other" };

function drawEditor() {
  const r = D.resume;
  const ed = $("#editor");
  const field = (path, val, ph) => `<input type="text" data-f="${path}" value="${esc(val || "")}" placeholder="${esc(ph)}">`;
  const bulletRows = (sec, i, bullets) => bullets.map((b, j) => `
      <div class="bullet-row" data-b="${sec}.${i}.${j}">
        <textarea data-bullet="${sec}.${i}.${j}" rows="2">${esc(b)}</textarea>
        <div class="b-tools">
          <button class="icon-btn" data-improve="${sec}.${i}.${j}" title="Suggest a stronger version">✨</button>
          <button class="icon-btn" data-bmove="${sec}.${i}.${j}.-1" title="Move up">↑</button>
          <button class="icon-btn" data-bdel="${sec}.${i}.${j}" title="Delete bullet">✕</button>
        </div>
        <div class="b-issues"></div>
        ${D.suggestions[`${sec}.${i}.${j}`] ? suggestionBox(`${sec}.${i}.${j}`) : ""}
      </div>`).join("");
  const entryTools = (sec, i) => `<span class="spacer"></span><button class="icon-btn" data-emove="${sec}.${i}.-1" title="Move up">↑</button><button class="icon-btn" data-edel="${sec}.${i}" title="Remove">✕</button>`;
  const sections = {
    education: () => r.education.map((x, i) => `<div class="ed-entry" data-entry="education.${i}">
        <div class="row" style="margin-bottom:6px"><b class="small">${esc(x.school || "School")}</b>${entryTools("education", i)}</div>
        <div class="ed-grid">${field(`education.${i}.school`, x.school, "School")}${field(`education.${i}.location`, x.location, "City, Prov")}
        ${field(`education.${i}.degree`, x.degree, "Degree, program")}${field(`education.${i}.dates`, x.dates, "Sept 2023 – Apr 2028")}</div>
        <textarea data-lines="education.${i}.details" rows="3" placeholder="One per line: GPA, awards, relevant coursework">${esc(x.details.join("\n"))}</textarea></div>`).join("")
      + `<button class="btn btn-ghost btn-sm" data-add="education">+ Education</button>`,
    experience: () => r.experience.map((x, i) => `<div class="ed-entry" data-entry="experience.${i}">
        <div class="row" style="margin-bottom:6px"><b class="small">${esc(x.title || "Role")} ${x.company ? "@ " + esc(x.company) : ""}</b>${entryTools("experience", i)}</div>
        <div class="ed-grid">${field(`experience.${i}.title`, x.title, "Title")}${field(`experience.${i}.dates`, x.dates, "May 2025 – Aug 2025")}
        ${field(`experience.${i}.company`, x.company, "Company")}${field(`experience.${i}.location`, x.location, "Toronto, ON")}
        ${field(`experience.${i}.link`, x.link, "🔗 Company website (optional), e.g. rbc.com")}</div>
        ${bulletRows("experience", i, x.bullets)}<button class="btn btn-ghost btn-sm" data-addb="experience.${i}">+ Bullet</button></div>`).join("")
      + `<button class="btn btn-ghost btn-sm" data-add="experience">+ Experience</button>`,
    projects: () => r.projects.map((x, i) => `<div class="ed-entry" data-entry="projects.${i}">
        <div class="row" style="margin-bottom:6px"><b class="small">${esc(x.name || "Project")}</b>${entryTools("projects", i)}</div>
        <div class="ed-grid">${field(`projects.${i}.name`, x.name, "Project name")}${field(`projects.${i}.dates`, x.dates, "Dates (optional)")}
        ${field(`projects.${i}.tech`, x.tech, "Tech: React, FastAPI, AWS")}${field(`projects.${i}.link`, x.link, "💻 Repo link, e.g. github.com/you/project")}
        ${field(`projects.${i}.demo`, x.demo, "🚀 Live demo link (optional)")}</div>
        ${bulletRows("projects", i, x.bullets)}<button class="btn btn-ghost btn-sm" data-addb="projects.${i}">+ Bullet</button></div>`).join("")
      + `<button class="btn btn-ghost btn-sm" data-add="projects">+ Project</button>`,
    skills: () => r.skills.map((x, i) => `<div class="ed-grid" data-entry="skills.${i}" style="grid-template-columns: 140px 1fr auto">${field(`skills.${i}.label`, x.label, "Languages")}${field(`skills.${i}.items`, x.items, "Python, Java, C++…")}<button class="icon-btn" data-edel="skills.${i}">✕</button></div>`).join("")
      + `<button class="btn btn-ghost btn-sm" data-add="skills">+ Skills row</button>`,
    extras: () => r.extras.map((x, i) => `<div class="ed-entry" data-entry="extras.${i}"><div class="row" style="margin-bottom:6px">${field(`extras.${i}.heading`, x.heading, "Heading, e.g. Leadership")}${entryTools("extras", i)}</div>
        <textarea data-lines="extras.${i}.items" rows="3" placeholder="One per line">${esc(x.items.join("\n"))}</textarea></div>`).join("")
      + `<button class="btn btn-ghost btn-sm" data-add="extras">+ Section</button>`,
  };
  const h = r.header;
  ed.innerHTML = `
    <div class="ed-sec"><h3>Header</h3><div class="ed-grid">
      ${field("header.name", h.name, "Full name")}${field("header.email", h.email, "Email")}
      ${field("header.phone", h.phone, "Phone (optional)")}${field("header.location", h.location, "City, Prov (optional)")}</div>
      <input type="text" data-links value="${esc(h.links.join(", "))}" placeholder="github.com/you, linkedin.com/in/you, portfolio">
    </div>
    ${r.section_order.map((sec, k) => `<div class="ed-sec"><h3>${SEC_TITLES[sec]}<span class="spacer"></span>
        <button class="icon-btn" data-smove="${k}.-1" title="Move section up">↑</button><button class="icon-btn" data-smove="${k}.1" title="Move section down">↓</button></h3>
        ${sections[sec]()}</div>`).join("")}`;
  bindEditor(ed);
}

function setPath(path, value) {
  const parts = path.split(".");
  let o = D.resume;
  for (let i = 0; i < parts.length - 1; i++) o = o[isNaN(parts[i]) ? parts[i] : Number(parts[i])];
  o[parts[parts.length - 1]] = value;
}

function bindEditor(ed) {
  ed.querySelectorAll("[data-f]").forEach((el) => el.addEventListener("input", () => { setPath(el.dataset.f, el.value); changed(); }));
  ed.querySelector("[data-links]").addEventListener("input", (e) => { D.resume.header.links = e.target.value.split(",").map((x) => x.trim()).filter(Boolean); changed(); });
  ed.querySelectorAll("[data-lines]").forEach((el) => el.addEventListener("input", () => { setPath(el.dataset.lines, el.value.split("\n").map((x) => x.trim()).filter(Boolean)); changed(); }));
  ed.querySelectorAll("[data-bullet]").forEach((el) => el.addEventListener("input", () => {
    const [sec, i, j] = el.dataset.bullet.split(".");
    D.resume[sec][Number(i)].bullets[Number(j)] = el.value.replace(/\n/g, " ");
    changed();
  }));
  const redraw = () => { drawEditor(); changed(true); };
  ed.querySelectorAll("[data-bdel]").forEach((b) => b.addEventListener("click", () => { const [sec, i, j] = b.dataset.bdel.split("."); D.resume[sec][Number(i)].bullets.splice(Number(j), 1); redraw(); }));
  ed.querySelectorAll("[data-bmove]").forEach((b) => b.addEventListener("click", () => {
    const [sec, i, j] = b.dataset.bmove.split(".").map((x, n) => (n ? Number(x) : x));
    const arr = D.resume[sec][i].bullets; if (j === 0) return;
    [arr[j - 1], arr[j]] = [arr[j], arr[j - 1]]; redraw();
  }));
  ed.querySelectorAll("[data-addb]").forEach((b) => b.addEventListener("click", () => { const [sec, i] = b.dataset.addb.split("."); D.resume[sec][Number(i)].bullets.push(""); drawEditor(); }));
  ed.querySelectorAll("[data-edel]").forEach((b) => b.addEventListener("click", () => { const [sec, i] = b.dataset.edel.split("."); if (confirm("Remove this entry?")) { D.resume[sec].splice(Number(i), 1); redraw(); } }));
  ed.querySelectorAll("[data-emove]").forEach((b) => b.addEventListener("click", () => { const [sec, i] = b.dataset.emove.split("."); const n = Number(i); if (!n) return; const a = D.resume[sec]; [a[n - 1], a[n]] = [a[n], a[n - 1]]; redraw(); }));
  ed.querySelectorAll("[data-smove]").forEach((b) => b.addEventListener("click", () => {
    const [k, d] = b.dataset.smove.split(".").map(Number); const o = D.resume.section_order; const t = k + d;
    if (t < 0 || t >= o.length) return; [o[k], o[t]] = [o[t], o[k]]; redraw();
  }));
  const blank = { education: { school: "", degree: "", location: "", dates: "", details: [] }, experience: { title: "", company: "", location: "", dates: "", link: "", bullets: [""] },
    projects: { name: "", tech: "", dates: "", link: "", demo: "", bullets: [""] }, skills: { label: "", items: "" }, extras: { heading: "", items: [] } };
  ed.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => { D.resume[b.dataset.add].push(structuredClone(blank[b.dataset.add])); drawEditor(); }));
  ed.querySelectorAll("[data-improve]").forEach((b) => b.addEventListener("click", () => improveBullets([b.dataset.improve])));
  ed.querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", () => {
    const [key, n] = b.dataset.use.split("|"); const [sec, i, j] = key.split(".");
    D.resume[sec][Number(i)].bullets[Number(j)] = D.suggestions[key].options[Number(n)];
    delete D.suggestions[key]; redraw();
  }));
  ed.querySelectorAll("[data-dismiss]").forEach((b) => b.addEventListener("click", () => { delete D.suggestions[b.dataset.dismiss]; drawEditor(); drawScore(); }));
  if (D.lint) drawScore();
}

function suggestionBox(key) {
  const s = D.suggestions[key];
  if (s.loading) return `<div class="suggest"><span><span class="spinner"></span> Thinking of a stronger version…</span></div>`;
  return `<div class="suggest">${s.options.map((o, n) => `<div class="opt"><span>${esc(o)}</span><button class="btn btn-sm" data-use="${key}|${n}">Use</button></div>`).join("")}
    <div class="row"><span class="muted small">${esc(s.note || "")}</span><span class="spacer"></span><button class="chip" data-dismiss="${key}">Dismiss</button></div></div>`;
}

async function improveBullets(keys) {
  keys.forEach((k) => (D.suggestions[k] = { loading: true }));
  drawEditor();
  const bullets = keys.map((k) => { const [sec, i, j] = k.split("."); return D.resume[sec][Number(i)].bullets[Number(j)]; });
  const [sec0, i0] = keys[0].split(".");
  const ctx = sec0 === "experience" ? `${D.resume.experience[Number(i0)].title} at ${D.resume.experience[Number(i0)].company} (${D.resume.experience[Number(i0)].dates})` : "personal project";
  try {
    const r = await api("/api/resume/improve", { method: "POST", json: { profile_id: S.pid, bullets, context: ctx } });
    r.results.forEach((res) => { const k = keys[res.index]; if (k) D.suggestions[k] = { options: res.options, note: res.note }; });
    keys.forEach((k) => { if (D.suggestions[k]?.loading) delete D.suggestions[k]; });
    if (r.by === "rules") toast("Rule-based suggestions: replace the [brackets] with your real numbers", 4000);
  } catch (e) { keys.forEach((k) => delete D.suggestions[k]); toast(e.message); }
  drawEditor();
}

function improveWeak() {
  const keys = [...new Set(D.lint.issues.filter((i) => i.category === "impact" && i.path && i.path.length === 4).map((i) => `${i.path[0]}.${i.path[1]}.${i.path[3]}`))].slice(0, 10);
  if (!keys.length) return toast("No weak bullets. Your impact score is solid");
  improveBullets(keys);
}

// ---------- Cover letters ----------
async function renderLetters() {
  const [list, fmts, apps, favs] = await Promise.all([
    api(`/api/letters?profile_id=${S.pid}`), api(`/api/letters/formats?profile_id=${S.pid}`),
    api(`/api/applications?profile_id=${S.pid}`), api(`/api/jobs?profile_id=${S.pid}&limit=1`),
  ]);
  const fmtName = Object.fromEntries(fmts.formats.map((f) => [f.id, f.name]));
  const jobOpts = new Map();
  favs.pinned.forEach((j) => jobOpts.set(j.id, `⭐ ${j.company} · ${j.title}`));
  apps.applications.forEach((a) => jobOpts.set(a.job_id, `${STATUS_LABEL[a.status]} · ${a.company} · ${a.title}`));
  $("#docs-body").innerHTML = `
    <div class="card card-pad stack" style="margin-bottom:16px">
      <div class="row"><h2>Cover letters</h2><span class="spacer"></span>
        <label class="toggle"><input type="checkbox" id="auto-letters" ${fmts.auto ? "checked" : ""}> Auto-draft one when I star or track a job</label></div>
      <p class="muted small" style="margin:0">Only facts from your resume and the posting. Anything only you know is left as a [bracketed blank].</p>
      <div class="row">
        <select id="new-job" style="flex:2;min-width:220px"><option value="">Pick a starred or tracked job…</option>${[...jobOpts].map(([id, l]) => `<option value="${esc(id)}">${esc(l)}</option>`).join("")}</select>
        <select id="new-fmt">${fmts.formats.map((f) => `<option value="${esc(f.id)}" ${f.id === fmts.default ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select>
        <button class="btn" id="new-letter">Write letter</button>
      </div>
    </div>
    <div class="card">${list.length ? `<div class="table-wrap"><table><thead><tr><th>Job</th><th>Format</th><th>By</th><th>Blanks</th><th>Updated</th><th></th></tr></thead><tbody>
      ${list.map((d) => `<tr><td><a href="#" data-open-letter="${d.id}"><b>${esc(d.title)}</b></a></td><td class="small">${esc(fmtName[d.format_id] || d.format_id || "")}</td>
        <td class="small">${byLabel(d.generated_by)}</td>
        <td>${d.placeholders ? `<span class="tag warn">${d.placeholders} to fill</span>` : '<span class="tag good">ready</span>'}</td>
        <td class="small muted">${ago(d.updated_at)}</td>
        <td style="text-align:right;white-space:nowrap"><a class="btn btn-ghost btn-sm" href="/api/letters/${d.id}/export?fmt=pdf" target="_blank">PDF</a> <a class="btn btn-ghost btn-sm" href="/api/letters/${d.id}/export?fmt=docx">Word</a> <button class="btn btn-ghost btn-sm" data-del-letter="${d.id}">Delete</button></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No letters yet. Star a posting or pick one above.</div>`}</div>
    <details class="card card-pad" id="styles-box" style="margin-top:16px"><summary style="cursor:pointer"><b>Letter styles</b> <span class="muted small">${fmts.formats.length} styles · add your own</span></summary><div id="formats-box" style="margin-top:12px"></div></details>`;
  $("#styles-box").addEventListener("toggle", (e) => { if (e.target.open && !$("#formats-box").innerHTML) renderFormats(); });
  $("#auto-letters").addEventListener("change", (e) => api("/api/letters/settings", { method: "PUT", json: { profile_id: S.pid, auto: e.target.checked } }));
  $("#new-letter").addEventListener("click", async () => {
    const job = $("#new-job").value; if (!job) return toast("Pick a job first");
    const btn = $("#new-letter"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Writing…';
    try { await api("/api/letters/generate", { method: "POST", json: { profile_id: S.pid, job_id: job, format_id: $("#new-fmt").value } }); openPackage(job); renderLetters(); }
    catch (e) { toast(e.message, 5000); btn.disabled = false; btn.textContent = "Write letter"; }
  });
  document.querySelectorAll("[data-open-letter]").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault(); const d = await api(`/api/letters/${a.dataset.openLetter}`); openPackage(d.job_id);
  }));
  document.querySelectorAll("[data-del-letter]").forEach((b) => b.addEventListener("click", async () => { if (confirm("Delete this letter?")) { await api(`/api/letters/${b.dataset.delLetter}`, { method: "DELETE" }); renderLetters(); } }));
}

// ---------- Formats ----------
async function renderFormats() {
  const f = await api(`/api/letters/formats?profile_id=${S.pid}`);
  ($("#formats-box") || $("#docs-body")).innerHTML = `
    <div class="grid-3">${f.formats.map((x) => `<div class="card fmt-card">
        <div class="row"><b>${esc(x.name)}</b><span class="spacer"></span>${x.id === f.default ? '<span class="tag good">default</span>' : `<button class="chip" data-default="${esc(x.id)}">Make default</button>`}</div>
        <div class="muted small">${esc(x.description || (x.style === "sample" ? "Your own letter. New letters copy its structure and tone." : "Your template"))}</div>
        <details><summary class="small" style="cursor:pointer">Show format</summary><pre>${esc(x.body)}</pre></details>
        ${x.builtin ? "" : `<button class="btn btn-ghost btn-sm" data-del-fmt="${esc(x.id)}">Delete</button>`}
      </div>`).join("")}</div>
    <div class="card card-pad stack" style="margin-top:16px">
      <h2>Add your own format</h2>
      <p class="muted small" style="margin:0">Two ways: <b>paste a cover letter you've written before</b> and new letters copy its structure and tone (best with an API key), or <b>write a template</b> using placeholders like <code>{company}</code>, <code>{role}</code>, <code>{experience_paragraph}</code>.</p>
      <div class="row"><input type="text" id="fmt-name" placeholder="Name, e.g. My RBC letter" style="flex:1"><input type="text" id="fmt-desc" placeholder="When to use it (optional)" style="flex:2"></div>
      <textarea id="fmt-body" class="letter-area" style="min-height:240px" placeholder="Dear {hiring_manager},&#10;&#10;I'm excited to apply for the {role} position at {company}…"></textarea>
      <div class="chips">${Object.entries(f.placeholders).map(([k, v]) => `<button class="chip" data-ph="${k}" title="${esc(v)}">{${k}}</button>`).join("")}</div>
      <div><button class="btn" id="fmt-save">Save format</button></div>
    </div>`;
  document.querySelectorAll("[data-default]").forEach((b) => b.addEventListener("click", async () => { await api("/api/letters/settings", { method: "PUT", json: { profile_id: S.pid, default: b.dataset.default } }); renderFormats(); }));
  document.querySelectorAll("[data-del-fmt]").forEach((b) => b.addEventListener("click", async () => { await api(`/api/letters/formats/${b.dataset.delFmt}?profile_id=${S.pid}`, { method: "DELETE" }); renderFormats(); }));
  document.querySelectorAll("[data-ph]").forEach((b) => b.addEventListener("click", () => {
    const ta = $("#fmt-body"); const ins = `{${b.dataset.ph}}`; const p = ta.selectionStart ?? ta.value.length;
    ta.value = ta.value.slice(0, p) + ins + ta.value.slice(p); ta.focus(); ta.selectionStart = ta.selectionEnd = p + ins.length;
  }));
  $("#fmt-save").addEventListener("click", async () => {
    try {
      const r = await api("/api/letters/formats", { method: "POST", json: { profile_id: S.pid, name: $("#fmt-name").value, description: $("#fmt-desc").value, body: $("#fmt-body").value } });
      toast(r.style === "sample" ? "Saved: new letters will follow this letter's structure and tone" : "Template saved"); renderFormats();
    } catch (e) { toast(e.message, 5000); }
  });
}

// ---------- Application package (modal) ----------
async function openPackage(jobId) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide"><div class="empty"><span class="spinner"></span> Preparing your application package…</div></div></div>`;
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) root.innerHTML = ""; });
  let pk;
  try { pk = await api(`/api/package?profile_id=${S.pid}&job_id=${encodeURIComponent(jobId)}`); }
  catch (e) { root.innerHTML = ""; return toast(e.message, 5000); }
  const j = pk.job, L = pk.letter, rep = pk.report;
  const q = `profile_id=${S.pid}&job_id=${encodeURIComponent(jobId)}`;
  const modal = root.querySelector(".modal");
  modal.innerHTML = `
    <div class="row"><div><div class="muted small">Application package</div><h2>${esc(j.company)} · ${esc(j.title)}</h2></div><span class="spacer"></span>
      ${j.url ? `<a class="btn btn-ghost btn-sm" href="${safeUrl(j.url)}" target="_blank" rel="noopener">Open posting ${extIcon}</a>` : ""}
      <a class="btn btn-sm" href="/api/package/download?${q}">⬇ Download everything (.zip)</a>
      <button class="icon-btn" id="pk-close" aria-label="Close">✕</button></div>
    <div class="grid-2" style="grid-template-columns: 1.35fr 1fr; align-items:start">
      <div class="stack">
        ${L ? `<div class="row"><h3>Cover letter</h3><span class="spacer"></span>
          <select id="pk-fmt">${pk.formats.map((f) => `<option value="${esc(f.id)}" ${f.id === L.format_id ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select>
          <button class="btn btn-ghost btn-sm" id="pk-regen">↻ Rewrite</button></div>
          ${L.placeholders.length ? `<div class="banner" style="margin:0">✍️ <span>Fill ${L.placeholders.length} blank${L.placeholders.length > 1 ? "s" : ""} in [brackets] before sending. ${S.app.ai_enabled ? "" : "Only you know why you want this company."}</span></div>` : ""}
          <textarea class="letter-area" id="pk-letter">${esc(L.content)}</textarea>
          <div class="row"><button class="btn btn-sm" id="pk-save">Save</button><span class="muted small" id="pk-saved">${L.generated_by === "you" ? "Edited by you" : AI_NAMES[L.generated_by] ? `Written by ${AI_NAMES[L.generated_by]}` : "Filled from the template"}</span><span class="spacer"></span>
            <a class="btn btn-ghost btn-sm" href="/api/letters/${L.id}/export?fmt=pdf" target="_blank">PDF</a><a class="btn btn-ghost btn-sm" href="/api/letters/${L.id}/export?fmt=docx">Word</a><a class="btn btn-ghost btn-sm" href="/api/letters/${L.id}/export?fmt=txt">Text</a></div>`
          : `<div class="empty">Add your resume in Settings → Profile to generate cover letters.</div>`}
      </div>
      <div class="stack">
        <h3>Resume for this job</h3>
        ${rep ? `<div class="card card-pad stack" style="box-shadow:none">
          <div class="row"><span>Keyword match</span><span class="spacer"></span><b id="pk-cov">${rep.coverage ?? "–"}${rep.coverage != null ? "%" : ""}</b></div>
          ${rep.matched.length ? `<div class="kw">${rep.matched.map((k) => `<span class="tag good">${esc(k)}</span>`).join("")}</div>` : ""}
          ${rep.missing.length ? `<div class="small muted">In the posting but not on your resume (add only if true):</div><div class="kw">${rep.missing.map((k) => `<span class="tag warn">${esc(k)}</span>`).join("")}</div>` : ""}
          ${rep.coverage == null ? '<div class="muted small">No description found for this posting, so keyword matching is limited.</div>' : ""}
          <div class="muted small">Resume score: <b>${pk.resume_score ?? "–"}</b>/100 · fix issues in Documents → Resume Studio</div>
          <button class="btn" id="pk-tailor">${pk.tailored ? "↻ Re-tailor" : "🎯 Tailor my resume for this job"}</button>
          <div id="pk-tailor-out"></div>
          <div class="row"><a class="btn btn-ghost btn-sm" href="/api/resume/export?${q}&fmt=pdf" target="_blank">Resume PDF</a><a class="btn btn-ghost btn-sm" href="/api/resume/export?${q}&fmt=docx">Resume Word</a></div>
        </div>` : `<div class="muted">Upload a resume first.</div>`}
      </div>
    </div>`;
  $("#pk-close").addEventListener("click", () => (root.innerHTML = ""));
  if (L) {
    $("#pk-save").addEventListener("click", async () => {
      await api(`/api/letters/${L.id}`, { method: "PUT", json: { content: $("#pk-letter").value } });
      $("#pk-saved").textContent = "Saved ✓";
    });
    $("#pk-regen").addEventListener("click", async () => {
      if ($("#pk-letter").value !== L.content && !confirm("Rewrite and lose your edits?")) return;
      $("#pk-regen").innerHTML = '<span class="spinner"></span>';
      try { await api("/api/letters/generate", { method: "POST", json: { profile_id: S.pid, job_id: jobId, format_id: $("#pk-fmt").value } }); }
      catch (e) { toast(e.message); }
      openPackage(jobId);
    });
  }
  $("#pk-tailor")?.addEventListener("click", async () => {
    const btn = $("#pk-tailor"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Tailoring…';
    try {
      const t = await api("/api/resume/tailor", { method: "POST", json: { profile_id: S.pid, job_id: jobId } });
      $("#pk-tailor-out").innerHTML = `<div class="small"><b>${AI_NAMES[t.by] ? `${AI_NAMES[t.by]} tailored it:` : "Reordered for this posting (no facts changed):"}</b>
        <ul class="clean">${(t.report.changes.length ? t.report.changes : ["Already well ordered for this posting"]).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        ${(t.report.tips || []).map((x) => `<div class="muted">💡 ${esc(x)}</div>`).join("")}
        ${t.report.coverage_after != null ? `<div>Keyword match now <b>${t.report.coverage_after}%</b></div>` : ""}</div>`;
      btn.disabled = false; btn.textContent = "↻ Re-tailor";
      if (t.report.coverage_after != null) $("#pk-cov").textContent = t.report.coverage_after + "%";
    } catch (e) { toast(e.message, 5000); btn.disabled = false; btn.textContent = "🎯 Tailor my resume for this job"; }
  });
}

// ---------- Workday logins (Applications tab) ----------
async function renderWorkdayCard() {
  const box = $("#workday-card");
  if (!box) return;
  const d = await api(`/api/portals?profile_id=${S.pid}`);
  const def = d.default_login, myEmail = (profile()?.profile || {}).email || "";
  const defBox = def ? `<div class="wd-default"><div><b>✓ One login for every Workday site</b><div class="muted small">${esc(def)} · every Workday job you mark as applied gets checked automatically</div></div><span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" id="wd-def-copy">Copy password</button>
      <button class="btn btn-ghost btn-sm" id="wd-def-edit">Change</button>
      <button class="btn btn-ghost btn-sm" id="wd-def-off">Turn off</button></div>` : "";
  const defForm = `<form class="stack${def ? " hidden" : ""}" id="wd-def-form">
      <div class="muted small">💡 Use the <b>same email and a Workday-only password</b> on every company's Workday, and save it here once. Generate makes a strong one.</div>
      <div class="row" style="flex-wrap:wrap"><input type="email" id="wd-def-user" placeholder="Email you use for Workday" value="${esc(def || myEmail)}" required style="flex:1 1 220px">
        <div class="pw-row" style="flex:1.4 1 280px"><input type="password" id="wd-def-pass" placeholder="${def ? "New password (empty keeps the current one)" : "Your Workday password"}" autocomplete="new-password"${def ? "" : " required"}>
          <button type="button" class="btn btn-ghost btn-sm" id="wd-def-gen">Generate</button><button type="button" class="btn btn-ghost btn-sm" id="wd-def-show">Show</button></div></div>
      <div class="row"><button class="btn">Save my Workday login</button>${def ? '<button type="button" class="btn btn-ghost" id="wd-def-cancel">Cancel</button>' : ""}</div>
    </form>`;
  box.innerHTML = `<div class="card card-pad stack">
    <div class="row"><h2>Workday logins</h2><span class="spacer"></span>
      ${d.accounts.length ? `<button class="btn btn-ghost btn-sm" id="wd-check">${d.checking ? '<span class="spinner"></span> Checking…' : "Check statuses now"}</button>` : ""}</div>
    <p class="muted small" style="margin:0">Some statuses only show inside each company's Workday. Co-op Coach signs in on each sync and updates your tracker. Passwords stay in your ${d.vault_backend === "macOS" ? "Mac's Keychain" : "system's credential store"}.${d.available ? "" : " <b>Install playwright to enable checks.</b>"}</p>
    ${defBox}${defForm}
    ${d.suggestions.length ? `<div class="row small"><span class="muted">You're tracking jobs on:</span>${d.suggestions.map((s) => `<button class="chip" data-sugg="${esc(s.site_url)}" data-co="${esc(s.company)}">+ ${esc(s.company)}</button>`).join("")}</div>` : ""}
    ${d.accounts.length ? `<div class="table-wrap"><table><thead><tr><th>Company</th><th>Login</th><th>Last check</th><th></th></tr></thead><tbody>
      ${d.accounts.map((a) => `<tr>
        <td><b>${esc(a.company)}</b><div class="muted small">${esc(a.site_url.replace(/^https:\/\//, ""))}</div></td>
        <td class="small">${esc(a.username)}${a.uses_default ? '<div class="muted small">shared login</div>' : ""}</td>
        <td class="small">${a.needs_manual ? '<span class="tag warn">needs you</span> ' : ""}${esc((a.last_status || "not checked yet").replace(/^(ok|error|needs_manual): /, ""))}${a.last_check ? ` <span class="muted">· ${ago(a.last_check)}</span>` : ""}</td>
        <td style="text-align:right;white-space:nowrap">
          ${a.needs_manual || a.signin === "running" ? `<button class="btn btn-sm" data-signin="${a.id}">${a.signin === "running" ? '<span class="spinner"></span> Waiting for you…' : "Sign in yourself"}</button>` : ""}
          <button class="btn btn-ghost btn-sm" data-copy="${a.id}" title="Copy password">Copy password</button>
          <button class="btn btn-ghost btn-sm" data-del-wd="${a.id}">Remove</button></td></tr>`).join("")}
    </tbody></table></div>` : ""}
    <details class="connect"><summary>＋ ${def ? "Add a Workday site by hand (or one with a different password)" : "Save a login for one company"}</summary>
      <form class="stack" id="wd-form">
        <input type="url" id="wd-url" placeholder="Company careers link, e.g. https://rbc.wd3.myworkdayjobs.com/RBCGLOBAL1" required>
        ${def ? `<label class="row small"><input type="checkbox" id="wd-shared" checked> Use my shared Workday login (${esc(def)})</label>` : ""}
        <div class="row"><input type="text" id="wd-co" placeholder="Company name" style="flex:1"><input type="email" id="wd-user" placeholder="Login email" required style="flex:1.4"></div>
        <div class="pw-row"><input type="password" id="wd-pass" placeholder="Password" required autocomplete="new-password">
          <button type="button" class="btn btn-ghost btn-sm" id="wd-gen" title="Make a strong password for a new account">Generate</button>
          <button type="button" class="btn btn-ghost btn-sm" id="wd-show">Show</button></div>
        <button class="btn">Save login</button>
      </form></details>
  </div>`;
  const shared = () => !!$("#wd-shared")?.checked;
  const syncShared = () => { $("#wd-user").classList.toggle("hidden", shared()); $("#wd-pass").closest(".pw-row").classList.toggle("hidden", shared()); $("#wd-user").required = !shared(); $("#wd-pass").required = !shared(); };
  $("#wd-shared")?.addEventListener("change", syncShared);
  if (def) syncShared();
  $("#wd-def-gen").addEventListener("click", async () => {
    const r = await api("/api/portals/generate-password", { method: "POST" });
    $("#wd-def-pass").value = r.password; $("#wd-def-pass").type = "text"; $("#wd-def-show").textContent = "Hide";
    try { await navigator.clipboard.writeText(r.password); toast("Strong password generated and copied. Use it on every Workday site"); } catch { toast("Strong password generated"); }
  });
  $("#wd-def-show").addEventListener("click", () => { const p = $("#wd-def-pass"); p.type = p.type === "password" ? "text" : "password"; $("#wd-def-show").textContent = p.type === "password" ? "Show" : "Hide"; });
  $("#wd-def-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/portals/default", { method: "PUT", json: { profile_id: S.pid, username: $("#wd-def-user").value, password: $("#wd-def-pass").value } });
      toast(r.added ? `Saved. Added ${r.added} Workday site${r.added === 1 ? "" : "s"} from jobs you've applied to` : "Saved to your Keychain. Workday jobs you apply to from now on get checked automatically", 6000);
      renderWorkdayCard();
    } catch (err) { toast(err.message, 6000); }
  });
  $("#wd-def-edit")?.addEventListener("click", () => { $("#wd-def-form").classList.remove("hidden"); $("#wd-def-pass").focus(); });
  $("#wd-def-cancel")?.addEventListener("click", () => $("#wd-def-form").classList.add("hidden"));
  $("#wd-def-off")?.addEventListener("click", async () => {
    if (!confirm("Turn off the shared Workday login? Its password is deleted from your Keychain and sites using it stop being checked.")) return;
    await api(`/api/portals/default?profile_id=${S.pid}`, { method: "DELETE" }); renderWorkdayCard();
  });
  $("#wd-def-copy")?.addEventListener("click", async () => {
    let code = "";
    if (profile().has_passcode) {
      code = await askPasscode("Confirm it's you", "Enter your passcode to copy your Workday password.");
      if (code === null) return;
    }
    try {
      const r = await api(`/api/portals/default/password?profile_id=${S.pid}`, { headers: { "X-Passcode": code } });
      await navigator.clipboard.writeText(r.password); toast("Workday password copied. Paste it when the company's site asks");
    } catch (err) { toast(err.message, 5000); }
  });
  $("#wd-check")?.addEventListener("click", async () => {
    await api("/api/portals/check", { method: "POST", json: { profile_id: S.pid } });
    toast("Signing in to each Workday site in the background…"); pollWorkday();
  });
  box.querySelectorAll("[data-sugg]").forEach((b) => b.addEventListener("click", () => {
    box.querySelector("details.connect").open = true; $("#wd-url").value = b.dataset.sugg; $("#wd-co").value = b.dataset.co; $("#wd-user").focus();
  }));
  $("#wd-gen").addEventListener("click", async () => {
    const r = await api("/api/portals/generate-password", { method: "POST" });
    $("#wd-pass").value = r.password; $("#wd-pass").type = "text"; $("#wd-show").textContent = "Hide";
    try { await navigator.clipboard.writeText(r.password); toast("Strong password generated and copied"); } catch { toast("Strong password generated"); }
  });
  $("#wd-show").addEventListener("click", () => { const p = $("#wd-pass"); p.type = p.type === "password" ? "text" : "password"; $("#wd-show").textContent = p.type === "password" ? "Show" : "Hide"; });
  $("#wd-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/portals", { method: "POST", json: { profile_id: S.pid, url: $("#wd-url").value, company: $("#wd-co").value, username: $("#wd-user").value, password: $("#wd-pass").value, use_default: shared() } });
      toast("Login saved to your Keychain"); renderWorkdayCard();
    } catch (err) { toast(err.message, 6000); }
  });
  box.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const p = profile();
    let code = "";
    if (p.has_passcode) {
      code = await askPasscode("Confirm it's you", "Enter your passcode to copy this saved password.");
      if (code === null) return;
    }
    try {
      const r = await api(`/api/portals/${b.dataset.copy}/password`, { headers: { "X-Passcode": code } });
      await navigator.clipboard.writeText(r.password); toast("Password copied");
    } catch (err) { toast(err.message, 5000); }
  }));
  box.querySelectorAll("[data-del-wd]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Remove this Workday site? It won't be checked any more (a site-specific password is deleted from your Keychain; your shared login stays).")) return;
    await api(`/api/portals/${b.dataset.delWd}`, { method: "DELETE" }); renderWorkdayCard();
  }));
  box.querySelectorAll("[data-signin]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/portals/${b.dataset.signin}/signin`, { method: "POST" });
    toast("A Chrome window is opening. Sign in there (solve any CAPTCHA), then come back", 6000);
    pollWorkday();
  }));
}

function pollWorkday() {
  clearTimeout(pollWorkday._t);
  pollWorkday._t = setTimeout(async () => {
    if (!["tracker", "settings"].includes(S.tab)) return;
    const d = await api(`/api/portals?profile_id=${S.pid}`).catch(() => null);
    if (d && (d.checking || d.accounts.some((a) => a.signin === "running"))) { renderWorkdayCard(); pollWorkday(); }
    else { await loadState(); render(); }
  }, 4000);
}

// ---------- chat ----------
function initChat() {
  if (initChat.done) return;
  initChat.done = true;
  const fab = $("#chat-fab"), panel = $("#chat-panel");
  fab.classList.remove("hidden");
  if (!LS.get("chat.seen", false)) $("#chat-dot").classList.remove("hidden");
  const open = () => { panel.classList.remove("hidden"); $("#chat-dot").classList.add("hidden"); LS.set("chat.seen", true); drawChat(); $("#chat-input").focus(); };
  fab.addEventListener("click", () => (panel.classList.contains("hidden") ? open() : panel.classList.add("hidden")));
  $("#chat-close").addEventListener("click", () => panel.classList.add("hidden"));
  const inp = $("#chat-input");
  const grow = () => { inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 180) + "px"; };
  $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); const v = inp.value.trim(); if (v) { sendChat(v); grow(); } });
  inp.addEventListener("input", grow);
  inp.addEventListener("keydown", (e) => {  // Enter sends; Shift+Enter starts a new line (pasted emails keep theirs)
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#chat-form").requestSubmit(); }
  });
}

function chatLog() { return LS.get(`chat.${S.pid}`, []); }
function linkify(t) { return esc(t).replace(/(https?:\/\/[^\s<]+)/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u.length > 48 ? u.slice(0, 45) + "…" : u}</a>`); }

// ---------- 📝 application kit ----------
async function openKit(jobId) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide"><div class="empty"><span class="spinner"></span> Preparing your application kit: answers, tailored resume, cover letter…</div></div></div>`;
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) root.innerHTML = ""; });
  let k;
  try { k = await api(`/api/apply/${encodeURIComponent(jobId)}/kit?profile_id=${S.pid}`); }
  catch (e) { root.innerHTML = ""; return toast(e.message, 6000); }
  const j = k.job, modal = root.querySelector(".modal");
  const qrow = (q, n) => `<div class="kit-q ${q.needs_you ? "needs" : ""}">
      <div class="row"><b class="small">${esc(q.label)}${q.required ? ' <span class="muted">*</span>' : ""}</b><span class="spacer"></span>
        ${q.answer && !q.answer.startsWith("(") ? `<button class="icon-btn" data-copy-q="${n}" title="Copy">⧉</button>` : ""}</div>
      ${q.answer ? `<div class="kit-a">${esc(q.answer).replace(/\n/g, "<br>")}</div>` : `<div class="muted small">${q.options?.length ? `Options: ${esc(q.options.slice(0, 6).join(" / "))}` : "Answer this one yourself"}</div>`}
      ${q.note ? `<div class="small" style="color:var(--warn)">${esc(q.note)}</div>` : ""}</div>`;
  modal.innerHTML = `
    <div class="row"><div><div class="muted small">Application kit · ${esc({ other: "company careers site", greenhouse: "Greenhouse form", lever: "Lever form", ashby: "Ashby form", workday: "Workday" }[j.ats] || j.ats)}</div><h2>${esc(j.company)} · ${esc(j.title)}</h2></div><span class="spacer"></span>
      <button class="icon-btn" id="kit-close" aria-label="Close">✕</button></div>
    ${j.eligibility === "no" ? `<div class="banner">🚫 <b>This posting says you can't apply:</b><span>${esc((j.who_can_apply || []).join(" · "))}</span></div>` : ""}
    <div class="row" style="flex-wrap:wrap;gap:8px">
      <button class="btn" id="kit-fill" title="Opens the form in Chrome and fills what it can. You review and press Submit.">Fill in Chrome</button>
      ${S.app.claude_code?.installed ? `<button class="btn btn-ghost" data-kit-via="claude_code" title="Opens Claude Code in Terminal with this kit: it fills the form with Claude in Chrome while you watch, and stops before Submit">Claude Code</button>` : ""}
      ${S.app.codex?.installed ? `<button class="btn btn-ghost" data-kit-via="codex" title="Opens ChatGPT (Codex) in Terminal with this kit: it fills the form in Chrome while you watch, and stops before Submit">ChatGPT</button>
        <button class="btn btn-ghost" data-kit-via="atlas" title="Opens the form in ChatGPT Atlas with the instructions on your clipboard">Open in Atlas</button>` : ""}
      <a class="btn btn-ghost" href="${safeUrl(j.apply_url)}" target="_blank" rel="noopener">Open the form ${extIcon}</a>
      <a class="btn btn-ghost" href="${k.files.resume_pdf}" target="_blank">⬇ Tailored resume</a>
      ${k.files.letter_pdf ? `<a class="btn btn-ghost" href="${k.files.letter_pdf}" target="_blank">⬇ Cover letter</a>` : ""}
      <button class="btn btn-ghost" id="kit-agent" title="Copy instructions for ChatGPT Atlas, Claude in Chrome or another browser agent">Copy for an AI agent</button>
      <span class="spacer"></span><button class="btn btn-ghost" id="kit-done">✓ I submitted it</button></div>
    <div class="muted small" id="kit-status">Nothing here presses Submit or touches CAPTCHAs: you review and submit. Answers <span style="color:var(--warn)">in orange</span> need you.</div>
    <div class="kit-grid">${k.questions.map(qrow).join("")}</div>`;
  $("#kit-close").addEventListener("click", () => (root.innerHTML = ""));
  modal.querySelectorAll("[data-copy-q]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(k.questions[Number(b.dataset.copyQ)].answer); toast("Copied"); } catch { toast("Couldn't copy"); }
  }));
  $("#kit-done").addEventListener("click", async () => {
    await api("/api/agent/tool", { method: "POST", json: { profile_id: S.pid, name: "track", args: { job_id: j.id, status: "applied" } } });
    toast("Marked applied. Good luck!"); root.innerHTML = ""; loadState();
  });
  modal.querySelectorAll("[data-kit-via]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const r = await api(`/api/apply/${encodeURIComponent(j.id)}/open`, { method: "POST", json: { profile_id: S.pid, via: b.dataset.kitVia } });
      $("#kit-status").textContent = r.message || "Opened. You press Submit.";
    } catch (e) { toast(e.message, 6000); }
  }));
  $("#kit-agent").addEventListener("click", async () => {
    const r = await api(`/api/apply/${encodeURIComponent(j.id)}/agent-prompt?profile_id=${S.pid}`);
    try { await navigator.clipboard.writeText(r.prompt); } catch { /* shown below */ }
    $("#kit-status").innerHTML = `<b>Copied.</b> Paste it into ChatGPT Atlas (agent mode), Claude in Chrome or another browser agent. It fills the form and stops before Submit.<details style="margin-top:6px"><summary>See what was copied</summary><pre class="kit-pre">${esc(r.prompt)}</pre></details>`;
  });
  $("#kit-fill").addEventListener("click", async () => {
    try { await api(`/api/apply/${encodeURIComponent(j.id)}/fill`, { method: "POST", json: { profile_id: S.pid } }); }
    catch (e) { return toast(e.message, 6000); }
    $("#kit-status").innerHTML = '<span class="spinner"></span> Opening the form in Chrome…';
    const poll = async () => {
      const st = await api(`/api/apply/fill-state?profile_id=${S.pid}`).catch(() => ({ state: "error", message: "lost track" }));
      if (!$("#kit-status")) return;
      $("#kit-status").innerHTML = st.state === "running" ? `<span class="spinner"></span> ${esc(st.message || "Working…")}` : `${st.state === "error" ? "⚠️" : "✅"} ${esc(st.message || "")}`;
      if (st.state === "running" || st.state === "open") setTimeout(poll, 2500);
    };
    setTimeout(poll, 2000);
  });
}

function slimCard(c) {  // keep what the chat history needs (it lives in this browser's storage)
  if (c.type === "jobs") return { type: "jobs", jobs: (c.jobs || []).slice(0, 8).map((j) => ({ id: j.id, company: j.company, title: j.title, score: j.score, where: (j.locations || []).slice(0, 2).join(" · "), eligibility: j.eligibility, url: j.url, status: j.app_status, twin: j.twin ? { url: j.twin.url, where: j.twin.where } : null })) };
  if (c.type === "events") return { type: "events", events: (c.events || []).slice(0, 8).map((e) => ({ id: e.id, title: e.title, start_ts: e.start_ts, all_day: e.all_day, region: e.region, url: e.url, mark: e.mark })) };
  return c;
}

function chatCard(c, mi, ci) {
  if (c.type === "jobs") return c.jobs.length ? `<div class="chat-card">${c.jobs.map((j) => `<div class="cc-row">
      <span class="cc-score">${j.score}</span>
      <div style="min-width:0;flex:1"><div class="cc-title"><a href="${safeUrl(j.url)}" target="_blank" rel="noopener">${esc(j.company)} · ${esc(j.title)}</a></div>
        <div class="muted small">${esc(j.where || "")}${j.eligibility === "no" ? ' · <span class="tag bad">can\'t apply</span>' : j.eligibility === "fit" ? ' · <span class="tag good">open to you</span>' : ""}${j.status ? ` · <span class="tag good">${esc(STATUS_LABEL[j.status] || j.status)}</span>` : ""}</div></div>
      <div class="cc-btns"><button class="icon-btn" data-cc="star" data-job="${esc(j.id)}" title="Star">⭐</button>
        <button class="icon-btn" data-cc="applied" data-job="${esc(j.id)}" title="Mark applied">✓</button>
        <button class="icon-btn" data-cc="kit" data-job="${esc(j.id)}" title="Application kit">📝</button></div></div>`).join("")}</div>` : "";
  if (c.type === "events") return c.events.length ? `<div class="chat-card">${c.events.map((e) => `<div class="cc-row">
      <div style="min-width:0;flex:1"><div class="cc-title">${e.url ? `<a href="${safeUrl(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}</div>
        <div class="muted small">${new Date(e.start_ts * 1000).toLocaleString(undefined, e.all_day ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · ${esc(e.region || "")}</div></div>
      <div class="cc-btns"><button class="icon-btn ${e.mark ? "on" : ""}" data-cc="save-event" data-ev="${esc(e.id)}" title="Save">⭐</button></div></div>`).join("")}</div>` : "";
  if (c.type === "letter") return `<div class="chat-card"><button class="btn btn-sm" data-cc="package" data-job="${esc(c.job_id)}">📦 Open the letter + package</button></div>`;
  if (c.type === "kit") return `<div class="chat-card"><button class="btn btn-sm" data-cc="kit" data-job="${esc(c.job_id)}">📝 Open the application kit</button></div>`;
  if (c.type === "email") {
    const field = c.status === "oa" ? "oa_due_at" : "interview_at";
    return `<div class="chat-card">
      <div class="row" style="gap:6px;flex-wrap:wrap"><b>${esc(c.title)}</b><span class="tag good">${esc(STATUS_LABEL[c.status] || c.status)}</span>${c.was && c.was !== c.status ? `<span class="muted small">was ${esc(STATUS_LABEL[c.was] || c.was)}</span>` : ""}</div>
      <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">
        ${c.book ? `<a class="btn btn-sm" href="${safeUrl(c.book)}" target="_blank" rel="noopener">📅 Book a time ${extIcon}</a>` : ""}
        ${c.assessment ? `<a class="btn btn-sm" href="${safeUrl(c.assessment)}" target="_blank" rel="noopener">🧪 Start the assessment ${extIcon}</a>` : ""}
        ${c.video ? `<a class="btn btn-ghost btn-sm" href="${safeUrl(c.video)}" target="_blank" rel="noopener">🎥 Call link ${extIcon}</a>` : ""}
        ${["interviewing", "oa"].includes(c.status) ? `<label class="small row" style="gap:4px" title="Goes on your calendar link with a reminder">${c.status === "oa" ? "Due" : "Interview"}
          <input type="datetime-local" data-cc-date="${field}" data-job="${esc(c.job_id)}" value="${localInput(c[field])}" style="width:auto"></label>` : ""}
        ${c.prep ? `<button class="btn btn-ghost btn-sm" data-cc="drafts">📄 Prep sheet</button>` : ""}
        <button class="btn btn-ghost btn-sm" data-cc="apps">Applications</button>
      </div></div>`;
  }
  if (c.type === "doc") return `<div class="chat-card"><button class="btn btn-sm" data-cc="doc" data-doc="${esc(String(c.doc_id))}">📄 ${esc(c.title)}</button></div>`;
  if (c.type === "review") return `<div class="chat-card"><button class="btn btn-sm" data-cc="studio">🧠 See the full review in Resume Studio</button></div>`;
  return "";
}

function bindCardDates(el) {  // the interview time / deadline box on an email card puts it on the calendar
  el.querySelectorAll("[data-cc-date]").forEach((inp) => inp.addEventListener("change", async () => {
    const ts = inp.value ? Math.floor(new Date(inp.value).getTime() / 1000) : null;
    try {
      await api("/api/applications/dates", { method: "PUT", json: { profile_id: S.pid, job_id: inp.dataset.job, [inp.dataset.ccDate]: ts } });
      toast(ts ? "On your calendar, with a reminder" : "Removed from your calendar");
    } catch (e) { toast(e.message, 5000); }
  }));
}

function drawChat() {
  const p = profile();
  $("#chat-mode").innerHTML = S.app.ai_enabled ? `Agent · ${esc(S.app.ai_label)} · <a href="#" id="chat-connect">change</a>` : "Quick answers (add a free Groq key for the agent)";
  const log = chatLog();
  const greet = { role: "assistant", content: S.app.ai_enabled
    ? `Hey ${p?.name || "there"}! I can do things, not just answer: find roles, prepare applications, track a recruiter email you paste here, research a company, draft replies and networking messages, set reminders, run mock interviews, fix your resume, and run Autopilot.`
    : `Hey ${p?.name || "there"}! I can tell you what to apply to, what's new today, your application stats, upcoming events, or how to level up your resume.` };
  $("#chat-log").innerHTML = [greet, ...log].map((m, mi) => {
    if (m.role === "user") return `<div class="msg user">${esc(m.content)}</div>`;
    const steps = (m.steps || []).map((st) => `<div class="step ${st.ok ? "" : "bad"}">${st.ok ? "⚙️" : "⚠️"} ${esc(st.summary)}</div>`).join("");
    const undo = (m.undo || []).length ? `<button class="chip" data-undo="${mi - 1}" ${m.undone ? "disabled" : ""}>${m.undone ? "Undone" : "↩ Undo"}</button>` : "";
    return `<div class="msg bot">${steps ? `<div class="steps-log">${steps}</div>` : ""}${linkify(m.content).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])\*([^*\n]+?)\*(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>").replace(/\n/g, "<br>")}${(m.cards || []).map((c, ci) => chatCard(c, mi, ci)).join("")}${undo ? `<div style="margin-top:6px">${undo}</div>` : ""}</div>`;
  }).join("");
  $("#chat-suggest").innerHTML = (log.length ? [] : S.app.ai_enabled
    ? ["What should I do today?", "Prepare my top 3 applications", "Best software co-ops in Toronto", "Hackathons this month", "Track a recruiter email…", "Research a company…", "Mock interview me", "Remind me…", "Fix my resume"]
    : ["What should I apply to?", "Anything new today?", "How many interviews?", "Resume tips", "Events this week"])
    .map((s) => `<button class="chip" data-ask="${esc(s)}">${esc(s)}</button>`).join("");
  const starts = { "Track a recruiter email…": "Track this email:\n\n", "Research a company…": "Research ", "Remind me…": "Remind me " };
  document.querySelectorAll("[data-ask]").forEach((b) => b.addEventListener("click", () => {
    const pre = starts[b.dataset.ask];
    if (!pre) return sendChat(b.dataset.ask);
    const inp = $("#chat-input"); inp.value = pre; inp.focus(); inp.dispatchEvent(new Event("input"));  // they finish it
  }));
  $("#chat-connect")?.addEventListener("click", (e) => { e.preventDefault(); $("#chat-panel").classList.add("hidden"); openSettings("ai"); });
  const tool = (name, args) => api("/api/agent/tool", { method: "POST", json: { profile_id: S.pid, name, args } });
  $("#chat-log").querySelectorAll("[data-cc]").forEach((b) => b.addEventListener("click", async () => {
    const act = b.dataset.cc, job = b.dataset.job;
    try {
      if (act === "star") { await tool("star", { job_id: job }); toast("Starred"); }
      else if (act === "applied") { await tool("track", { job_id: job, status: "applied" }); toast("Marked applied"); }
      else if (act === "kit") openKit(job);
      else if (act === "package") openPackage(job);
      else if (act === "studio") { LS.set("docsub", "studio"); switchTab("documents"); }
      else if (act === "doc") { try { viewDraft(await api(`/api/drafts/${b.dataset.doc}?profile_id=${S.pid}`)); } catch { toast("That draft was deleted"); } }
      else if (act === "apps") { $("#chat-panel").classList.add("hidden"); switchTab("tracker"); }
      else if (act === "drafts") { $("#chat-panel").classList.add("hidden"); switchTab("autopilot"); }
      else if (act === "save-event") { await tool("save_event", { event_id: b.dataset.ev, mark: b.classList.contains("on") ? "none" : "interested" }); b.classList.toggle("on"); toast(b.classList.contains("on") ? "Saved to My events" : "Removed"); }
    } catch (e) { toast(e.message, 5000); }
  }));
  bindCardDates($("#chat-log"));
  $("#chat-log").querySelectorAll("[data-undo]").forEach((b) => b.addEventListener("click", async () => {
    const all = chatLog(), m = all[Number(b.dataset.undo)];
    if (!m || m.undone) return;
    for (const u of [...(m.undo || [])].reverse()) await tool(u.tool, u.args).catch(() => {});
    m.undone = true; LS.set(`chat.${S.pid}`, all); drawChat(); toast("Undone");
  }));
  const el = $("#chat-log"); el.scrollTop = el.scrollHeight;
}

async function sendChat(text) {
  const log = chatLog();
  log.push({ role: "user", content: text });
  LS.set(`chat.${S.pid}`, log.slice(-30));
  $("#chat-input").value = "";
  drawChat();
  $("#chat-log").insertAdjacentHTML("beforeend", `<div class="msg bot" id="typing"><span class="spinner"></span> Working on it…</div>`);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  try {
    const r = await api("/api/chat", { method: "POST", json: { profile_id: S.pid, messages: log.filter((m) => m.role).slice(-12).map((m) => ({ role: m.role, content: m.content })) } });
    log.push({ role: "assistant", content: r.reply, steps: r.steps || [], cards: (r.cards || []).map(slimCard), undo: r.undo || [] });
    if ((r.steps || []).some((s) => s.ok && /starred|→|preferences|event →/.test(s.summary))) { loadState().then(() => S.tab === "matches" && refreshJobs?.()); }
  } catch (e) { log.push({ role: "assistant", content: `Sorry, something went wrong: ${e.message}` }); }
  LS.set(`chat.${S.pid}`, log.slice(-30));
  drawChat();
}
