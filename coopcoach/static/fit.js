/* Resume Studio's market-fit card: each skill a market asks for, checked against the resume, with one action each. The
   market is a role category or any track (the same engine and numbers as the track's Market page). Loaded before
   app.js; uses its helpers ($, api, esc, toast, S, LS...) and docs.js's D, drawEditor and changed at call time. */

function categoryOptions(sel, targetsLabel) {
  return `<option value="" ${!sel ? "selected" : ""}>Your targets${targetsLabel ? ` (${esc(targetsLabel)})` : ""}</option>` +
    S.app.categories.filter((c) => c !== "Other").map((c) => `<option ${sel === c ? "selected" : ""}>${esc(c)}</option>`).join("");
}

async function drawFit() {
  const card = $("#fit-card");
  if (!card || !D.resume) return;
  const pick = LS.get("fitcat", "");  // a role category ("Quant"), or a track ("track:co:quant-firms")
  const track = pick.startsWith("track:") ? pick.slice(6) : "", cat = track ? "" : pick;
  if (!card.innerHTML) card.innerHTML = '<div class="muted small"><span class="spinner"></span> Checking your resume against your market…</div>';
  let f;
  try { f = await api("/api/market/fit", { method: "POST", json: { profile_id: S.pid, resume: D.resume, category: cat, track } }); }
  catch (e) { card.innerHTML = `<div class="muted small">${esc(e.message)}</div>`; return; }
  if (!$("#fit-card")) return;
  const m = f.market, rows = f.skills || [];
  const shows = rows.filter((r) => r.status === "listed" || r.status === "surface").length;
  const tracksOpts = await (typeof trackOptions === "function" ? trackOptions(track).catch(() => "") : "");
  card.innerHTML = `
    <div class="row"><h3 style="margin:0">📈 Market fit</h3><span class="spacer"></span>
      <select id="fit-cat" class="btn-sm" title="Which market">${categoryOptions(cat, cat || track ? "" : m.label)}${tracksOpts.replace(/value="/g, 'value="track:')}</select></div>
    ${!m.size ? '<div class="muted small">No postings in this market yet.</div>' : `
    <div class="small">Your resume shows <b>${shows}</b> of the <b>${rows.length}</b> skills ${track ? `the ${m.size} ${esc(m.label)} postings with full text` : `your top ${m.size} ${esc(m.label)} postings`} ask for most${f.coverage != null ? `, covering <b>${f.coverage}%</b> of what they ask` : ""}.${track ? " The same numbers as that track's Market." : ""}</div>
    <div class="fit-list">${rows.map((r, i) => `<div class="fit-row ${r.status}">
      <div><b>${esc(r.skill)}</b> <span class="muted small">${r.count}/${m.size}</span>
        <div class="muted small">${r.status === "surface" ? `In ${esc(r.where || "your bullets")}, but not in your Skills line` : esc(r.label)}</div></div>
      <div class="fit-act">${r.status === "listed" ? '<span class="tag good">✓</span>'
        : r.status === "surface" || r.status === "known" ? `<button class="btn btn-sm" data-fit-add="${i}">Add "${esc(r.add_as)}" to Skills</button>`
        : `<button class="btn btn-ghost btn-sm" data-fit-have="${i}">I have this</button><button class="btn btn-ghost btn-sm" data-fit-plan="${i}">Plan a project</button>`}</div>
      <div class="fit-more" id="fit-more-${i}"></div>
    </div>`).join("")}</div>
    <div class="muted small">Nothing is added for you. Only list what you could talk about in an interview.</div>`}`;
  $("#fit-cat")?.addEventListener("change", (e) => { LS.set("fitcat", e.target.value); card.innerHTML = ""; drawFit(); });
  const add = async (r, text) => {
    const out = await api("/api/market/add-skill", { method: "POST", json: { profile_id: S.pid, resume: D.resume, text, skill: r.skill } });
    D.resume = out.resume; drawEditor(); changed(true);
    toast(`Added ${text} to ${out.line || "Skills"}`);
    drawFit();
  };
  card.querySelectorAll("[data-fit-add]").forEach((b) => b.addEventListener("click", () => { const r = rows[Number(b.dataset.fitAdd)]; add(r, r.add_as).catch((e) => toast(e.message)); }));
  card.querySelectorAll("[data-fit-have]").forEach((b) => b.addEventListener("click", () => {
    const i = Number(b.dataset.fitHave), r = rows[i], box = $(`#fit-more-${i}`);
    box.innerHTML = `<form class="row"><input type="text" value="${esc(r.add_as)}" style="flex:1" maxlength="40"><button class="btn btn-sm">Add</button></form>
      <div class="muted small">Postings write it as ${esc(r.forms.join(", ") || r.skill)}. Then show it in a bullet too: a skill only in the list is easy to doubt.</div>`;
    box.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); const v = box.querySelector("input").value.trim(); if (v) add(r, v).catch((err) => toast(err.message)); });
    box.querySelector("input").focus();
  }));
  card.querySelectorAll("[data-fit-plan]").forEach((b) => b.addEventListener("click", async () => {
    const i = Number(b.dataset.fitPlan), r = rows[i], box = $(`#fit-more-${i}`);
    box.innerHTML = `<span class="spinner"></span> Planning a project for ${esc(r.skill)}…`;
    try {
      const out = await api("/api/market/plan", { method: "POST", json: { profile_id: S.pid, skill: r.skill, category: cat } });
      const p = out.plan;
      box.innerHTML = `<div class="plan"><b>${esc(p.title)}</b><div class="muted small">${esc(p.why)}${out.by !== "rules" ? ` · by ${esc(aiName())}` : ""}</div>
        <ol class="small">${p.steps.map((s) => `<li><b>${esc(s.when)}:</b> ${esc(s.do)}</li>`).join("")}</ol>
        <div class="small"><span class="muted">When it's done, a line like:</span> <i>${esc(p.resume_line)}</i></div></div>`;
    } catch (e) { box.textContent = e.message; }
  }));
}
