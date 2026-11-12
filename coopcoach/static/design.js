/* Resume Design editor: controls on the left, the real resume page on the right. Every control changes one theme key;
   the key becomes CSS variables (the same mapping as resume_theme.css_vars on the server) set straight on the preview,
   so tweaks show instantly with no server round trip. Saving is debounced. Loaded after docs.js. */

const TE = { schema: null, st: null, past: [], future: [], saveT: null, coalesceT: null, themes: {}, saving: null };
// Pinned, loaded only when the editor opens. Without them (offline) it falls back to <input type=color> and a plain mix.
const TE_CULORI = "https://cdn.jsdelivr.net/npm/culori@4.0.2/bundled/culori.min.js";
const TE_PICKER = "https://cdn.jsdelivr.net/npm/vanilla-colorful@0.7.2/hex-color-picker.js";
// Changing these rebuilds the page on the server (they change the HTML, not just how it looks).
const TE_STRUCTURAL = new Set(["columns", "contact", "link_icons", "company_first", "one_page"]);

function teLoadLibs() {
  if (TE.libs) return TE.libs;
  const culori = window.culori ? Promise.resolve() : new Promise((ok) => {
    const s = document.createElement("script"); s.src = TE_CULORI; s.onload = ok; s.onerror = ok; document.head.appendChild(s);
  });
  const picker = customElements.get("hex-color-picker") ? Promise.resolve() : import(TE_PICKER).catch(() => null);
  TE.libs = Promise.all([culori, picker]);
  return TE.libs;
}

// ---------- colour ----------
function teMix(hex, t) {  // toward white in OKLab, like resume_theme.mix_white
  if (!t) return hex.toLowerCase();
  if (window.culori) return culori.formatHex(culori.interpolate([hex, "#ffffff"], "oklab")(t));
  const n = (i) => parseInt(hex.slice(i, i + 2), 16), m = (c) => Math.round(c + (255 - c) * t).toString(16).padStart(2, "0");
  return "#" + m(n(1)) + m(n(3)) + m(n(5));
}
function teContrast(hex) {
  if (window.culori) return culori.wcagContrast(hex, "#ffffff");
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const L = 0.2126 * lin(parseInt(hex.slice(1, 3), 16)) + 0.7152 * lin(parseInt(hex.slice(3, 5), 16)) + 0.0722 * lin(parseInt(hex.slice(5, 7), 16));
  return 1.05 / (L + 0.05);
}

// ---------- the variables (mirror of resume_theme.css_vars) ----------
function teVars(st) {
  const S_ = TE.schema, F = S_.fonts, serif = F[st.body_font].kind === "serif", right = st.date_align === "right";
  return {
    "--accent": st.accent, "--link": st.link_color, "--rule": teMix(st.accent, st.rule_tint),
    "--font-h": F[st.heading_font].css, "--font-b": F[st.body_font].css,
    "--base": `${st.body_size}pt`, "--name": `${st.name_size}pt`, "--t-lh": String(st.line_height),
    "--t-margin": `${st.margin}in`, "--t-sec": String(st.section_space), "--t-sp": String(S_.density[st.density]),
    "--bullet": `"${S_.bullets[st.bullet]}"`, "--h-align": st.header_align,
    "--d-just": right ? "space-between" : "flex-start", "--d-sep": right ? '""' : '"·"', "--d-gap": right ? "14px" : ".4em",
    "--sub-style": serif ? "italic" : "normal", "--sub-op": serif ? "1" : ".85",
    ...S_.heading_vars[st.heading_style],
  };
}

function teApplyLive() {
  const fr = $("#te-frame"), doc = fr && fr.contentDocument;
  if (!doc || !doc.documentElement) return;
  const root = doc.documentElement;
  for (const [k, v] of Object.entries(teVars(TE.st))) root.style.setProperty(k, v);
  const w = fr.contentWindow;
  if (TE.st.one_page && w.__fitNow) w.__fitNow();
  teFitLine();
}

async function teRenderPage() {
  const html = await fetch("/api/resume/preview", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume: D.resume, template: D.template }) }).then((r) => r.text());
  const fr = $("#te-frame");
  if (!fr) return;
  fr.onload = () => { teApplyLive(); setTimeout(teFitLine, 80); };
  fr.srcdoc = html;
}

function teFitLine() {
  const fr = $("#te-frame"), line = $("#te-fit");
  if (!fr || !line) return;
  const f = fr.contentWindow && fr.contentWindow.__fit;
  if (!TE.st.one_page) { line.innerHTML = '<span class="muted">One-page fitting is off.</span>'; return; }
  if (!f) { line.textContent = ""; return; }
  D.fitLevel = f.level;
  line.innerHTML = f.fits ? `<span class="tag good">✓ Fits on one page</span> <span class="muted">body ${f.pt}pt${f.level ? ", squeezed a little to fit" : ""}</span>`
    : `<span class="tag warn">${f.pages} pages even at the smallest size</span> <span class="muted">cut about ${Math.ceil(f.over / 15)} lines</span>`;
}

function teScale() {
  const stage = $("#te-stage"), paper = $("#te-paper"), fr = $("#te-frame");
  if (!stage || !fr) return;
  const top = stage.getBoundingClientRect().top;
  const h = Math.max(320, window.innerHeight - Math.max(top, 70) - 70);
  const s = Math.min(stage.clientWidth / 816, h / 1056);
  fr.style.transform = `scale(${s})`;
  paper.style.width = `${816 * s}px`; paper.style.height = `${1056 * s}px`;
}

// ---------- state: change, undo, save ----------
function teSnapshot() { return JSON.stringify(TE.st); }
function tePush(coalesce = false) {  // remember the look before a change (one slider drag counts as one change)
  const quiet = coalesce && TE.coalesceT;
  clearTimeout(TE.coalesceT); TE.coalesceT = coalesce ? setTimeout(() => (TE.coalesceT = null), 700) : null;
  if (quiet) return;
  TE.past.push(teSnapshot());
  if (TE.past.length > 80) TE.past.shift();
  TE.future = [];
}

function teSet(patch, { coalesce = false, redraw = true, push = true } = {}) {
  if (push) tePush(coalesce);
  const structural = Object.keys(patch).some((k) => TE_STRUCTURAL.has(k) && patch[k] !== TE.st[k]);
  Object.assign(TE.st, patch);
  teAfterChange(structural, redraw);
}

function teAfterChange(structural, redraw = true) {
  if (structural) teRenderPage(); else teApplyLive();
  if (redraw) teDrawControls(); else teDrawMeter();
  teQueueSave();
}

function teUndo(dir) {
  const from = dir < 0 ? TE.past : TE.future, to = dir < 0 ? TE.future : TE.past;
  if (!from.length) return;
  to.push(teSnapshot());
  const was = { ...TE.st }, next = JSON.parse(from.pop());
  Object.keys(TE.st).forEach((k) => delete TE.st[k]); Object.assign(TE.st, next);
  teAfterChange([...TE_STRUCTURAL].some((k) => next[k] !== was[k]));
}

function teQueueSave() {
  const s = $("#te-save"); if (s) s.textContent = "Saving…";
  clearTimeout(TE.saveT);
  TE.saveT = setTimeout(teSaveNow, 700);
}
async function teSaveNow() {
  clearTimeout(TE.saveT); TE.saveT = null;
  TE.saving = api("/api/resume", { method: "PUT", json: { profile_id: S.pid, resume: D.resume, template: D.template } });
  try { await TE.saving; const s = $("#te-save"); if (s) s.textContent = "Saved ✓"; }
  catch (e) { toast(e.message, 5000); }
  TE.saving = null;
}

// ---------- ATS meter (same checks as resume_theme.ats_report) ----------
function teAts(st) {
  const w = [];
  if (st.columns === 2) w.push("Two columns: some ATS read straight across both, mixing your sections.");
  if (teContrast(st.accent) < 4.5) w.push("Accent is too light to read well on white (under 4.5:1).");
  if (teContrast(st.link_color) < 4.5) w.push("Link colour is too light to read well on white.");
  if (st.body_size < 9.5) w.push("Body text under 9.5pt is hard to read once printed.");
  if (st.contact === "emoji") w.push("Emoji in the contact line can come out garbled in ATS.");
  if (st.line_height < 1.08) w.push("Very tight line height: lines run together.");
  if (st.margin < 0.4) w.push("Margins under 0.4in can be cut off when printed.");
  if (st.bullet === "arrow") w.push("Unusual bullet glyphs sometimes come through as odd characters.");
  return { score: Math.max(0, 100 - 14 * w.length), warnings: w };
}
function teDrawMeter() {
  const box = $("#te-meter"); if (!box) return;
  const a = teAts(TE.st), n = Math.round(a.score / 10);
  box.className = `te-meter ${a.score >= 86 ? "ok" : a.score >= 58 ? "meh" : "bad"}`;
  box.innerHTML = `<div class="te-meter-bar">ATS-SAFE [${"█".repeat(n)}${"░".repeat(10 - n)}] ${String(a.score).padStart(3, " ")}</div>`
    + (a.warnings.length ? a.warnings.map((x) => `<div>! ${esc(x)}</div>`).join("") : "<div>✓ one column · readable sizes · dark enough colours</div>");
  const u = $("#te-undo"), r = $("#te-redo");
  if (u) u.disabled = !TE.past.length;
  if (r) r.disabled = !TE.future.length;
}

const TE_FMT = { body_size: (v) => `${(+v).toFixed(1)}pt`, name_size: (v) => `${(+v).toFixed(1)}pt`,
  line_height: (v) => (+v).toFixed(2), margin: (v) => `${(+v).toFixed(2)}in`, section_space: (v) => `${(+v).toFixed(2)}×` };

// ---------- the page ----------
async function renderDesign() {
  const data = await api(`/api/resume?profile_id=${S.pid}`);
  const body = $("#docs-body");
  if (!data.resume) {
    body.innerHTML = `<div class="card empty">Add your resume in <a href="#" id="to-coach">Settings → Profile</a> first.</div>`;
    $("#to-coach").addEventListener("click", (e) => { e.preventDefault(); openSettings("profile"); });
    return;
  }
  D.resume = data.resume; D.template = data.template;
  TE.schema = data.theme; TE.themes = data.themes || {}; TE.st = D.resume.style; TE.past = []; TE.future = [];
  body.innerHTML = `
    <div class="theme-ed">
      <div class="te-controls card card-pad" id="te-controls"></div>
      <div class="te-side">
        <div class="te-stage" id="te-stage"><div class="te-paper" id="te-paper"><iframe id="te-frame" title="Resume preview"></iframe></div></div>
        <div class="te-under small"><span id="te-fit"></span><span class="spacer"></span>
          <a class="btn btn-sm" id="te-pdf" href="#">⬇ PDF</a><a class="btn btn-ghost btn-sm" id="te-docx" href="#">Word</a></div>
      </div>
    </div>`;
  const dl = async (e, fmt) => {
    e.preventDefault();
    if (TE.saveT) await teSaveNow(); else if (TE.saving) await TE.saving;
    const lv = fmt === "docx" && D.fitLevel != null ? `&level=${D.fitLevel}` : "";
    window.open(`/api/resume/export?profile_id=${S.pid}&fmt=${fmt}${lv}`, "_blank", "noopener");
  };
  $("#te-pdf").addEventListener("click", (e) => dl(e, "pdf"));
  $("#te-docx").addEventListener("click", (e) => dl(e, "docx"));
  teDrawControls();
  teRenderPage();
  teScale();
  new ResizeObserver(teScale).observe($("#te-stage"));
  if (!TE.onResize) { TE.onResize = () => teScale(); window.addEventListener("resize", TE.onResize); }
  if (!TE.onKey) {
    TE.onKey = (e) => {
      if (!$("#te-controls") || !(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z") return;
      if (e.target.matches("input[type=text]")) return;
      e.preventDefault(); teUndo(e.shiftKey ? 1 : -1);
    };
    document.addEventListener("keydown", TE.onKey);
  }
  teLoadLibs().then(() => { if ($("#te-controls")) { teDrawControls(); teApplyLive(); } });
}

function teDrawControls() {
  const box = $("#te-controls"); if (!box) return;
  const st = TE.st, S_ = TE.schema;
  const seg = (key, opts, num = false) => `<div class="seg te-seg">${opts.map(([v, l]) =>
    `<button type="button" class="${String(st[key]) === String(v) ? "on" : ""}" data-seg="${key}" data-v="${esc(String(v))}" data-num="${num ? 1 : ""}">${l}</button>`).join("")}</div>`;
  const slider = (key, label, step) => {
    const [lo, hi] = S_.numbers[key];
    return `<label class="te-slider"><span>${label}</span><input type="range" min="${lo}" max="${hi}" step="${step}" value="${st[key]}" data-num-key="${key}">
      <output class="te-mono" id="te-out-${key}">${TE_FMT[key](st[key])}</output></label>`;
  };
  const fonts = (key) => `<div class="te-fonts">${Object.entries(S_.fonts).map(([id, f]) =>
    `<button type="button" class="te-font ${st[key] === id ? "on" : ""}" data-font="${key}|${id}" style="font-family:${esc(f.css)}">
      <span class="te-ag">Ag</span><span class="te-fname">${esc(f.label)}</span></button>`).join("")}</div>`;
  const tints = S_.rule_tints.map((t) => `<button type="button" class="te-tint ${st.rule_tint === t ? "on" : ""}" data-tint="${t}"
      title="${t ? `Heading rule ${Math.round(t * 100)}% lighter` : "Heading rule in the accent itself"}"><i style="background:${teMix(st.accent, t)}"></i>${t ? `${Math.round((1 - t) * 100)}%` : "solid"}</button>`).join("");
  const presetBtns = Object.entries(S_.presets).map(([k, p]) => `<button type="button" class="chip" data-preset="${k}">${esc(p.label)}</button>`).join("");
  const saved = Object.keys(TE.themes);
  const tog = (key, label) => `<label class="switch"><input type="checkbox" data-tog="${key}" ${st[key] ? "checked" : ""}><span></span>${label}</label>`;
  const picker = customElements.get("hex-color-picker")
    ? `<hex-color-picker id="te-picker" color="${st.accent}"></hex-color-picker>` : `<input type="color" id="te-picker-fallback" value="${st.accent}">`;
  box.innerHTML = `
    <div class="row te-top"><h3 style="margin:0">Design</h3><span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" id="te-undo" title="Undo (⌘Z)">↶</button><button class="btn btn-ghost btn-sm" id="te-redo" title="Redo (⇧⌘Z)">↷</button>
      <button class="btn btn-sm" id="te-shuffle" title="A random look that still reads as one design, and stays ATS-safe">🎲 Shuffle tasteful</button>
      <span class="muted small te-mono" id="te-save">${TE.saveT || TE.saving ? "Saving…" : "Saved"}</span></div>
    <div id="te-meter" class="te-meter"></div>
    <section class="te-ai"><div class="filter-label">Describe your look</div>
      <form class="te-ai-form" id="te-ai-form"><input type="text" id="te-ask" maxlength="500" autocomplete="off" value="${esc(TE.ask || "")}" placeholder="e.g. clean and modern, navy headings, bigger name">
        <button class="btn btn-sm" id="te-ai-go">Design it</button></form>
      <div class="te-wrap">${["Clean and modern for big tech", "Traditional for banks", "Bold startup", "Quiet and minimal", "Fit more on the page"].map((x) => `<button type="button" class="chip" data-ask="${esc(x)}">${esc(x)}</button>`).join("")}</div>
      ${TE.aiNote ? `<div class="te-ai-note">${esc(TE.aiNote)} <span class="muted">Not it? Press ↶, or say what to change.</span></div>` : ""}
    </section>
    <section><div class="filter-label">Start from</div><div class="te-wrap">${presetBtns}</div></section>
    <section><div class="filter-label">My themes</div><div class="te-wrap">
      ${saved.map((n) => `<span class="chip te-saved"><button type="button" data-theme="${esc(n)}">${esc(n)}</button><button type="button" class="te-x" data-del="${esc(n)}" title="Delete">×</button></span>`).join("") || '<span class="muted small">None yet.</span>'}
      </div>
      <form class="row te-saveas" id="te-saveas"><input type="text" id="te-name" maxlength="40" placeholder="Name this look, e.g. “Banks”"><button class="btn btn-ghost btn-sm">Save theme</button></form></section>
    <section><div class="filter-label">Name &amp; headings</div>
      <div class="te-color">${picker}
        <div class="stack" style="gap:8px;flex:1;min-width:0">
          <div class="row"><input type="text" id="te-hex" class="te-mono" value="${st.accent}" maxlength="7" style="width:92px">
            <span class="muted small te-mono">${teContrast(st.accent).toFixed(1)}:1 on white</span></div>
          <div class="swatches">${S_.palette.map(([n, c]) => `<button type="button" class="swatch ${st.accent === c ? "on" : ""}" data-acc="${c}" title="${esc(n)}" style="background:${c}"></button>`).join("")}</div>
          <div><div class="muted small">Heading rule tint</div><div class="te-wrap">${tints}</div></div>
        </div></div></section>
    <section><div class="filter-label">Links</div>
      <div class="swatches">${[...S_.palette.map(([n, c]) => [n, c])].map(([n, c]) => `<button type="button" class="swatch ${st.link_color === c ? "on" : ""}" data-link="${c}" title="${esc(n)}" style="background:${c}"></button>`).join("")}
        <label class="swatch te-any ${S_.palette.some(([, c]) => c === st.link_color) ? "" : "on"}" title="Any colour"><input type="color" id="te-link-any" value="${st.link_color}"></label></div>
      <div class="te-wrap" style="margin-top:6px"><button type="button" class="chip" data-link="${st.accent}">Same as headings</button></div></section>
    <section><div class="filter-label">Heading font</div>${fonts("heading_font")}</section>
    <section><div class="filter-label">Body font</div>${fonts("body_font")}</section>
    <section><div class="filter-label">Size & spacing</div>
      ${slider("body_size", "Body text", 0.1)}
      ${slider("name_size", "Your name", 0.5)}
      ${slider("line_height", "Line height", 0.01)}
      ${slider("margin", "Margins", 0.01)}
      ${slider("section_space", "Section gap", 0.05)}
      <div class="te-line"><span>Density</span>${seg("density", Object.keys(S_.density).map((k) => [k, k[0].toUpperCase() + k.slice(1)]))}</div></section>
    <section><div class="filter-label">Layout</div>
      <div class="te-line"><span>Headings</span>${seg("heading_style", [["rule", "<span style='text-decoration:underline'>RULE</span>"], ["caps", "CAPS"], ["smallcaps", "<span style='font-variant:small-caps'>Small caps</span>"], ["none", "Plain"]])}</div>
      <div class="te-line"><span>Bullets</span>${seg("bullet", Object.entries(S_.bullets).map(([k, g]) => [k, `<b>${esc(g)}</b>`]))}</div>
      <div class="te-line"><span>Dates</span>${seg("date_align", Object.entries(S_.date_align))}</div>
      <div class="te-line"><span>Name</span>${seg("header_align", [["center", "Centred"], ["left", "Left"]])}</div>
      <div class="te-line"><span>Columns</span>${seg("columns", [[1, "One"], [2, "Two"]], true)}</div>
      <div class="te-line"><span>Contact line</span>${seg("contact", [["icons", "Icons + labels"], ["emoji", "😀 Emoji + labels"], ["plain", "Plain links"]])}</div>
      <div class="stack" style="gap:8px;margin-top:6px">
        ${tog("one_page", "Fit to one page (shrinks from your sizes only as much as needed)")}
        ${tog("link_icons", "Little link icon next to companies & projects")}
        ${tog("company_first", "Company above job title")}</div></section>
    <p class="muted small te-note">PDF follows this design exactly. Word gets the fonts, colours, sizes, margins and headings, in one column with Word's own bullets. The LaTeX export keeps Jake's layout and takes only your colours.</p>`;

  box.querySelectorAll("[data-seg]").forEach((b) => b.addEventListener("click", () =>
    teSet({ [b.dataset.seg]: b.dataset.num ? Number(b.dataset.v) : b.dataset.v })));
  box.querySelectorAll("[data-font]").forEach((b) => b.addEventListener("click", () => { const [k, id] = b.dataset.font.split("|"); teSet({ [k]: id }); }));
  box.querySelectorAll("[data-num-key]").forEach((i) => i.addEventListener("input", () => {
    const k = i.dataset.numKey; teSet({ [k]: Number(i.value) }, { coalesce: true, redraw: false });
    $(`#te-out-${k}`).textContent = TE_FMT[k](i.value);
  }));
  box.querySelectorAll("[data-tint]").forEach((b) => b.addEventListener("click", () => teSet({ rule_tint: Number(b.dataset.tint) })));
  box.querySelectorAll("[data-tog]").forEach((i) => i.addEventListener("change", () => teSet({ [i.dataset.tog]: i.checked })));
  const setAccent = (c, opts) => {
    const patch = { accent: c };
    if (TE.st.link_color === TE.st.accent) patch.link_color = c;  // links that followed the accent keep following it
    teSet(patch, opts);
  };
  box.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", () => teSet({ link_color: b.dataset.link })));
  $("#te-link-any").addEventListener("input", (e) => teSet({ link_color: e.target.value.toLowerCase() }, { coalesce: true, redraw: false }));
  $("#te-link-any").addEventListener("change", () => teDrawControls());
  const askAI = async (text) => {
    const ask = text.trim();
    if (!ask) return;
    TE.ask = ask;
    const go = $("#te-ai-go");
    go.disabled = true; go.innerHTML = '<span class="spinner"></span> Designing…';
    jarvisSay("designing your resume", { key: "design" });
    try {
      const r = await api("/api/resume/theme/ai", { method: "POST", json: { profile_id: S.pid, ask, style: TE.st } });
      TE.aiNote = r.note;
      teSet(r.look);
    } catch (err) { toast(err.message, 6000); go.disabled = false; go.textContent = "Design it"; }
    finally { jarvisSay(null, { key: "design" }); }
  };
  $("#te-ai-form").addEventListener("submit", (e) => { e.preventDefault(); askAI($("#te-ask").value); });
  box.querySelectorAll("[data-ask]").forEach((b) => b.addEventListener("click", () => { $("#te-ask").value = b.dataset.ask; askAI(b.dataset.ask); }));
  box.querySelectorAll("[data-acc]").forEach((b) => b.addEventListener("click", () => setAccent(b.dataset.acc)));
  const pk = $("#te-picker");
  // one drag on the picker = one undo step: remember the colour when the pointer goes down, not on every move
  pk?.addEventListener("pointerdown", () => { tePush(); TE.dragging = true; });
  if (pk) pk.addEventListener("color-changed", (e) => {
    setAccent(e.detail.value.toLowerCase(), TE.dragging ? { push: false, redraw: false } : { coalesce: true, redraw: false });
    $("#te-hex").value = TE.st.accent;
  });
  $("#te-picker-fallback")?.addEventListener("input", (e) => setAccent(e.target.value, { coalesce: true, redraw: false }));
  $("#te-picker-fallback")?.addEventListener("change", () => teDrawControls());
  if (!TE.onPointerUp) {  // the pointer can be released outside the picker
    TE.onPointerUp = () => { if (TE.dragging) { TE.dragging = false; setTimeout(teDrawControls, 0); } };
    document.addEventListener("pointerup", TE.onPointerUp);
  }
  $("#te-hex").addEventListener("change", (e) => {
    const v = e.target.value.trim().toLowerCase().replace(/^([0-9a-f]{6})$/, "#$1");
    if (/^#[0-9a-f]{6}$/.test(v)) setAccent(v); else { e.target.value = TE.st.accent; toast("Use a hex colour like #1f3a68"); }
  });
  box.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    D.template = b.dataset.preset;
    const p = S_.presets[b.dataset.preset].style;
    teSet(Object.fromEntries(S_.keys.filter((k) => k in p).map((k) => [k, p[k]])));
    toast(`Started from ${S_.presets[b.dataset.preset].label}: change anything`);
  }));
  box.querySelectorAll("[data-theme]").forEach((b) => b.addEventListener("click", () => teSet({ ...TE.themes[b.dataset.theme] })));
  box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Delete the theme “${b.dataset.del}”?`)) return;
    const r = await api(`/api/resume/themes?profile_id=${S.pid}&name=${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
    TE.themes = r.themes; teDrawControls();
  }));
  $("#te-saveas").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#te-name").value.trim(); if (!name) return;
    try {
      const r = await api("/api/resume/themes", { method: "PUT", json: { profile_id: S.pid, name, style: TE.st } });
      TE.themes = r.themes; teDrawControls(); toast(`Saved “${name}”`);
    } catch (err) { toast(err.message, 5000); }
  });
  $("#te-shuffle").addEventListener("click", async () => teSet(await api("/api/resume/theme/shuffle")));
  $("#te-undo").addEventListener("click", () => teUndo(-1));
  $("#te-redo").addEventListener("click", () => teUndo(1));
  teDrawMeter();
}
