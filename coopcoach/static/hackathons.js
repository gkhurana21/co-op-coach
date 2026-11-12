// ---------- Hackathon packages: apply to attend, and the Devpost write-up afterwards ----------
// The key is the hackathon's name as a slug ("hack-the-north"), or "ev-<event id>" for one that isn't on the major list.

const hackSlug = (name) => (name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
const hackKeyOf = (e) => (e.major ? hackSlug(e.major) : `ev-${e.id}`);
const words = (t) => ((t || "").trim().match(/\S+/g) || []).length;

function hackDates(h) {
  if (!h.start_ts) return h.when ? `Usually ${h.when}` : "";
  const o = { month: "short", day: "numeric" }, a = new Date(h.start_ts * 1000), b = new Date((h.end_ts || h.start_ts) * 1000);
  return a.toDateString() === b.toDateString() ? a.toLocaleDateString(undefined, o) : `${a.toLocaleDateString(undefined, o)} – ${b.toLocaleDateString(undefined, o)}`;
}

async function copyText(text, what = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(what); } catch { toast("Couldn't copy: select it and copy instead"); }
}

function hackModal(loading) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide"><div class="empty"><span class="spinner"></span> ${esc(loading)}</div></div></div>`;
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) root.innerHTML = ""; });
  return root.querySelector(".modal");
}

function pollFill(el) {
  const poll = async () => {
    const st = await api(`/api/apply/fill-state?profile_id=${S.pid}`).catch(() => ({ state: "error", message: "lost track of the window" }));
    if (!el.isConnected) return;
    el.innerHTML = st.state === "running" ? `<span class="spinner"></span> ${esc(st.message || "Working…")}` : `${st.state === "error" ? "⚠️" : "✅"} ${esc(st.message || "")}`;
    if (st.state === "running" || st.state === "open") setTimeout(poll, 2500);
  };
  setTimeout(poll, 1500);
}

// --- A. apply to attend ---
async function openHackApply(key, redraft = false) {
  const modal = hackModal(redraft ? "Writing your answers again…" : "Getting your application ready…");
  let k;
  try { k = await api(`/api/hackathons/${encodeURIComponent(key)}/apply?profile_id=${S.pid}${redraft ? "&redraft=true" : ""}`); }
  catch (e) { $("#modal-root").innerHTML = ""; return toast(e.message, 6000); }
  const h = k.hackathon, by = { you: "Your words", ai: `Drafted by ${S.app.ai_label || "AI"} from your resume`, rules: "Starting points from your resume" }[k.drafted_by] || "";
  const group = (g) => k.questions.filter((q) => q.group === g);
  const status = k.status ? `<span class="tag good">${esc(HACK_STATUS[k.status] || k.status)}</span>` : `<button class="btn btn-sm" id="ha-applied">✓ I applied</button>`;
  modal.innerHTML = `
    <div class="row"><div><div class="muted small">Hacker application</div><h2>${esc(h.name)}</h2>
        <div class="muted small">${esc([h.city, hackDates(h)].filter(Boolean).join(" · "))}${h.themes?.length ? ` · ${esc(h.themes.slice(0, 4).join(", "))}` : ""}</div></div>
      <span class="spacer"></span>${status}
      ${k.job.apply_url ? `<a class="btn btn-ghost btn-sm" href="${safeUrl(k.job.apply_url)}" target="_blank" rel="noopener">Open site ${extIcon}</a>` : ""}
      <a class="btn btn-ghost btn-sm" href="/api/hackathons/${encodeURIComponent(key)}/apply/download?profile_id=${S.pid}">⬇ Download (.zip)</a>
      <button class="icon-btn" id="ha-close" aria-label="Close">✕</button></div>
    <div class="grid-2 grid-lead">
      <div class="stack">
        <div class="row"><h3>Short answers</h3><span class="muted small">${esc(by)}</span><span class="spacer"></span><button class="btn btn-ghost btn-sm" id="ha-redraft" title="Write the answers again from your resume (your edits are replaced)">↻ Redraft</button></div>
        ${group("answers").map((q) => `<label class="stack" style="gap:4px"><b class="small">${esc(q.label)}</b>
          ${q.kind === "textarea" ? `<textarea class="letter-area" style="min-height:120px" data-ans="${esc(q.label)}">${esc(q.answer)}</textarea>` : `<input type="text" data-ans="${esc(q.label)}" value="${esc(q.answer)}">`}
          <span class="muted small"><span data-wc="${esc(q.label)}">${words(q.answer)} words</span>${q.note ? ` · ${esc(q.note)}` : ""}</span></label>`).join("")}
        <div class="row"><button class="btn btn-sm" id="ha-save">Save</button><span class="muted small" id="ha-saved"></span><span class="spacer"></span>
          <button class="btn btn-primary btn-sm" id="ha-fill" ${k.job.apply_url ? "" : "disabled"}>✍️ Fill it in Chrome</button></div>
        <div class="small" id="ha-status"></div>
      </div>
      <div class="stack">
        <div class="card card-pad stack" style="box-shadow:none"><h3 style="margin:0">About you</h3>
          ${group("you").map((q) => `<div class="row small" style="gap:8px"><span class="muted" style="min-width:150px">${esc(q.label)}</span><span>${q.kind === "input_file" ? `<a href="${esc(k.files.resume_pdf)}" target="_blank">Your resume (PDF)</a>` : q.answer ? esc(q.answer) : `<span class="muted">${esc(q.note || "missing")}</span>`}</span></div>`).join("")}
          <div class="muted small">From your resume and Settings → Profile.</div></div>
        <div class="card card-pad stack" style="box-shadow:none"><h3 style="margin:0">For the event</h3>
          ${group("event").map((q) => `<label class="row small" style="gap:8px"><span class="muted" style="min-width:150px">${esc(q.label)}</span>
            ${q.options.length ? `<select style="flex:1" data-pref="${esc(q.pref)}"><option value=""></option>${q.options.map((o) => `<option ${o === q.answer ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`
              : `<input type="text" style="flex:1" data-pref="${esc(q.pref)}" value="${esc(q.answer)}" placeholder="${q.pref === "dietary" ? "None" : ""}">`}</label>`).join("")}
          <div class="muted small">Remembered for your next hackathon.</div></div>
        <div class="card card-pad stack" style="box-shadow:none"><h3 style="margin:0">Left for you</h3>
          <ul class="clean small">${k.left_for_you.map((x) => `<li>☐ ${esc(x)}</li>`).join("")}</ul></div>
      </div>
    </div>`;
  const collect = () => {
    const answers = {}, prefs = {};
    modal.querySelectorAll("[data-ans]").forEach((el) => { answers[el.dataset.ans] = el.value; });
    modal.querySelectorAll("[data-pref]").forEach((el) => { prefs[el.dataset.pref] = el.value; });
    return { answers, prefs };
  };
  const save = async () => {
    await api(`/api/hackathons/${encodeURIComponent(key)}/apply`, { method: "PUT", json: { profile_id: S.pid, ...collect() } });
    $("#ha-saved").textContent = "Saved ✓";
  };
  modal.querySelectorAll("[data-ans]").forEach((el) => el.addEventListener("input", () => {
    const wc = modal.querySelector(`[data-wc="${CSS.escape(el.dataset.ans)}"]`); if (wc) wc.textContent = `${words(el.value)} words`;
    $("#ha-saved").textContent = "";
  }));
  modal.querySelectorAll("[data-ans], [data-pref]").forEach((el) => el.addEventListener("change", () => save().catch(() => {})));
  $("#ha-close").addEventListener("click", () => ($("#modal-root").innerHTML = ""));
  $("#ha-save").addEventListener("click", () => save().catch((e) => toast(e.message)));
  $("#ha-redraft").addEventListener("click", () => { if (confirm("Write the short answers again? Your edits to them are replaced.")) openHackApply(key, true); });
  $("#ha-fill").addEventListener("click", async () => {
    await save().catch(() => {});
    $("#ha-status").innerHTML = '<span class="spinner"></span> Opening the site in Chrome…';
    try { await api(`/api/hackathons/${encodeURIComponent(key)}/apply/fill`, { method: "POST", json: { profile_id: S.pid } }); pollFill($("#ha-status")); }
    catch (e) { $("#ha-status").textContent = e.message; }
    toast("Sign in on the site if it asks, open the application: each page you reach gets filled. You press Submit.", 7000);
  });
  $("#ha-applied")?.addEventListener("click", async () => {
    await save().catch(() => {});
    await api(`/api/hackathons/${encodeURIComponent(key)}/track`, { method: "POST", json: { profile_id: S.pid, status: "applied" } });
    toast(`${h.name} is on your tracker. Mark it Accepted when you hear back`);
    openHackApply(key);
  });
}

// --- B. the project, afterwards ---
async function openHackSubmit(key, fresh = false) {
  const modal = hackModal("Opening your write-up…");
  let d;
  try { d = await api(`/api/hackathons/${encodeURIComponent(key)}/submission?profile_id=${S.pid}`); }
  catch (e) { $("#modal-root").innerHTML = ""; return toast(e.message, 6000); }
  const h = d.hackathon || { name: "your hackathon" };
  if (d.submission && !fresh) return drawSubmission(modal, key, d.submission);
  const prev = d.submission || {}, n = prev.notes || {};
  modal.innerHTML = `
    <div class="row"><div><div class="muted small">Devpost submission</div><h2>${esc(h.name)}</h2></div><span class="spacer"></span>
      <button class="icon-btn" id="hs-close" aria-label="Close">✕</button></div>
    <p class="muted" style="margin:0">Paste your project's GitHub link: the write-up comes from its README, what it's built with (its languages and dependency files) and the few lines you add. Nothing is made up: anything only your team knows stays a [bracket] for you.</p>
    <div class="stack" style="max-width:680px">
      <label class="stack" style="gap:4px"><b class="small">GitHub repo (public)</b><input type="url" id="hs-repo" placeholder="https://github.com/you/project" value="${esc(prev.repo || "")}"></label>
      <label class="stack" style="gap:4px"><b class="small">Project name <span class="muted">(optional: taken from the repo)</span></b><input type="text" id="hs-project" value="${esc(prev.project || "")}"></label>
      <label class="stack" style="gap:4px"><b class="small">What it does, in your words <span class="muted">(optional)</span></b><textarea id="hs-built" rows="2">${esc(n.built || "")}</textarea></label>
      <div class="grid-2"><label class="stack" style="gap:4px"><b class="small">A challenge you hit <span class="muted">(optional)</span></b><textarea id="hs-challenge" rows="2">${esc(n.challenge || "")}</textarea></label>
        <label class="stack" style="gap:4px"><b class="small">What you learned <span class="muted">(optional)</span></b><textarea id="hs-learned" rows="2">${esc(n.learned || "")}</textarea></label></div>
      <label class="stack" style="gap:4px"><b class="small">What you built yourself <span class="muted">(for your resume)</span></b><textarea id="hs-mine" rows="2" placeholder="Built the React front end and the matching API">${esc(n.mine || "")}</textarea></label>
      <div class="grid-2"><label class="stack" style="gap:4px"><b class="small">Prize won <span class="muted">(only if you did)</span></b><input type="text" id="hs-prize" value="${esc(n.prize || "")}"></label>
        <label class="stack" style="gap:4px"><b class="small">Demo video link <span class="muted">(optional)</span></b><input type="url" id="hs-video" placeholder="https://youtu.be/…" value="${esc(prev.video || "")}"></label></div>
      <div class="row"><button class="btn btn-primary" id="hs-go">Write it</button><span class="muted small" id="hs-out"></span></div>
    </div>`;
  $("#hs-close").addEventListener("click", () => ($("#modal-root").innerHTML = ""));
  $("#hs-go").addEventListener("click", async () => {
    const btn = $("#hs-go"), val = (id) => $(id).value.trim();
    if (!val("#hs-repo")) return toast("Paste the GitHub link first");
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Reading the repo…';
    try {
      const s = await api(`/api/hackathons/${encodeURIComponent(key)}/submission`, { method: "POST", json: { profile_id: S.pid, repo: val("#hs-repo"),
        project: val("#hs-project"), built: val("#hs-built"), challenge: val("#hs-challenge"), learned: val("#hs-learned"), mine: val("#hs-mine"),
        prize: val("#hs-prize"), video: val("#hs-video") } });
      drawSubmission(modal, key, s);
    } catch (e) { btn.disabled = false; btn.textContent = "Write it"; $("#hs-out").textContent = e.message; }
  });
}

function drawSubmission(modal, key, s) {
  const SECT = [["inspiration", "Inspiration"], ["what", "What it does"], ["how", "How we built it"], ["challenges", "Challenges we ran into"],
    ["proud", "Accomplishments that we're proud of"], ["learned", "What we learned"], ["next", `What's next for ${s.project}`]];
  const e = s.resume_entry || {}, h = s.hackathon || {};
  const by = AI_NAMES[s.by] ? `Written by ${AI_NAMES[s.by]} from the repo and your notes` : "From the README and your notes";
  modal.innerHTML = `
    <div class="row"><div><div class="muted small">Devpost submission${h.name ? ` · ${esc(h.name)}` : ""}</div><h2>${esc(s.project)}</h2>
        <div class="muted small">${esc(by)} · <a href="${safeUrl(s.repo)}" target="_blank" rel="noopener">${esc(s.repo.replace("https://", ""))}</a></div></div><span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" id="hs-edit">↻ Start over</button>
      <a class="btn btn-ghost btn-sm" href="/api/devpost/${s.doc_id}/download?profile_id=${S.pid}">⬇ Download (.md)</a>
      <button class="icon-btn" id="hs-close" aria-label="Close">✕</button></div>
    ${s.brackets ? `<div class="banner" style="margin:0">✍️ <span>Fill ${s.brackets} [bracket${s.brackets > 1 ? "s" : ""}]: only your team knows those.</span></div>` : ""}
    ${s.readme_found ? "" : `<div class="banner" style="margin:0">ℹ️ <span>The repo has no README, so there was little to go on. A short README helps judges too.</span></div>`}
    <div class="grid-2 grid-lead">
      <div class="stack">
        <label class="stack" style="gap:4px"><span class="row"><b class="small">Elevator pitch</b><span class="spacer"></span><span class="muted small" id="hs-tl-n">${(s.tagline || "").length}/200</span><button class="btn btn-ghost btn-sm" data-copy="tagline">Copy</button></span>
          <input type="text" id="hs-tagline" maxlength="200" value="${esc(s.tagline || "")}"></label>
        <div class="row"><h3>About the project</h3><span class="spacer"></span><button class="btn btn-sm" id="hs-copy-all" title="Devpost's About the project field takes markdown">Copy all (markdown)</button></div>
        ${SECT.map(([k, t]) => `<label class="stack" style="gap:4px"><span class="row"><b class="small">${esc(t)}</b><span class="spacer"></span><button class="btn btn-ghost btn-sm" data-copy="${k}">Copy</button></span>
          <textarea class="letter-area" style="min-height:90px" data-sec="${k}">${esc((s.sections || {})[k] || "")}</textarea></label>`).join("")}
        <div class="row"><button class="btn btn-sm" id="hs-save">Save</button><span class="muted small" id="hs-saved"></span><span class="spacer"></span>
          <button class="btn btn-primary btn-sm" id="hs-fill" title="Sign in to Devpost in the window; each page you reach gets filled. You press Submit">✍️ Fill on Devpost</button></div>
        <div class="small" id="hs-status"></div>
      </div>
      <div class="stack">
        <div class="card card-pad stack" style="box-shadow:none"><div class="row"><h3 style="margin:0">Built with</h3><span class="spacer"></span><button class="btn btn-ghost btn-sm" data-copy="built">Copy</button></div>
          <div class="kw">${(s.built_with || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("") || '<span class="muted small">Nothing found: add your tools on Devpost</span>'}</div>
          <div class="muted small">From the repo's languages and dependency files.</div>
          <div class="row small"><b>Try it out:</b> ${(s.links || []).map((u) => `<a href="${safeUrl(u)}" target="_blank" rel="noopener">${esc(u.replace("https://", ""))}</a>`).join(" · ")}</div></div>
        <div class="card card-pad stack" style="box-shadow:none"><div class="row"><h3 style="margin:0">Demo video</h3><span class="spacer"></span><button class="btn btn-ghost btn-sm" data-copy="script">Copy</button></div>
          <pre class="kit-pre" style="white-space:pre-wrap;margin:0">${esc(s.video_script || "")}</pre></div>
        <div class="card card-pad stack" style="box-shadow:none"><h3 style="margin:0">On your resume</h3>
          <div class="small"><b>${esc(e.name || "")}</b>${e.tech ? ` | <i>${esc(e.tech)}</i>` : ""} <span class="muted">${esc(e.dates || "")}</span></div>
          <textarea id="hs-bullets" rows="4" title="One bullet per line">${esc((e.bullets || []).join("\n"))}</textarea>
          <div class="muted small">One bullet per line. Brackets are yours to fill; no numbers were added.</div>
          <button class="btn btn-sm" id="hs-resume">➕ Add to my resume</button></div>
      </div>
    </div>`;
  const current = () => { const sec = {}; modal.querySelectorAll("[data-sec]").forEach((el) => { sec[el.dataset.sec] = el.value; }); return sec; };
  const md = () => SECT.map(([k, t]) => `## ${t}\n${(current()[k] || "").trim()}`).join("\n\n");
  const save = async () => {
    const r = await api(`/api/devpost/${s.doc_id}`, { method: "PUT", json: { profile_id: S.pid, sections: current(), tagline: $("#hs-tagline").value } });
    $("#hs-saved").textContent = r.brackets ? `Saved ✓ · ${r.brackets} [brackets] left` : "Saved ✓ · no brackets left";
  };
  $("#hs-close").addEventListener("click", () => ($("#modal-root").innerHTML = ""));
  $("#hs-edit").addEventListener("click", () => openHackSubmit(key, true));
  $("#hs-tagline").addEventListener("input", (ev) => { $("#hs-tl-n").textContent = `${ev.target.value.length}/200`; });
  modal.querySelectorAll("[data-sec], #hs-tagline").forEach((el) => el.addEventListener("change", () => save().catch(() => {})));
  $("#hs-save").addEventListener("click", () => save().catch((err) => toast(err.message)));
  $("#hs-copy-all").addEventListener("click", () => copyText(md(), "Copied: paste it into About the project"));
  modal.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.copy;
    copyText(k === "tagline" ? $("#hs-tagline").value : k === "built" ? (s.built_with || []).join(", ") : k === "script" ? s.video_script : current()[k]);
  }));
  $("#hs-resume").addEventListener("click", async () => {
    const bullets = $("#hs-bullets").value.split("\n").map((x) => x.trim()).filter(Boolean);
    try {
      const r = await api(`/api/devpost/${s.doc_id}/resume`, { method: "POST", json: { profile_id: S.pid, entry: { bullets } } });
      const t = $("#toast");
      t.innerHTML = `${esc(r.project)} is first in your resume's Projects <button class="chip" id="toast-undo">Undo</button>`;
      t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 8000);
      $("#toast-undo").addEventListener("click", async () => {
        await api("/api/devpost/restore", { method: "POST", json: { profile_id: S.pid, backup_id: r.backup_id } });
        t.classList.remove("show"); toast("Your resume is back as it was");
      });
    } catch (err) { toast(err.message, 5000); }
  });
  $("#hs-fill").addEventListener("click", async () => {
    await save().catch(() => {});
    const url = prompt("Your project's Devpost page (after Start a submission), or leave empty to open Devpost:", s.devpost_url || "") ?? null;
    if (url === null) return;
    $("#hs-status").innerHTML = '<span class="spinner"></span> Opening Devpost in Chrome…';
    try { await api(`/api/devpost/${s.doc_id}/fill`, { method: "POST", json: { profile_id: S.pid, url } }); pollFill($("#hs-status")); }
    catch (err) { $("#hs-status").textContent = err.message; }
  });
}
