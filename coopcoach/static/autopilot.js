/* Today: Jarvis's home. Autopilot (how many to prepare, Jarvis's plan as a sentence you can click, presets, "Tell
   Jarvis"), what's ready to apply and the apply session, what needs you, this week, history, and all its settings. */

const SOURCE_LABEL = { autopilot: "Autopilot", claude_code: "Claude Code", codex: "ChatGPT", mcp: "your AI", chat: "chat", card: "you", app: "you", undo: "undo" };
const FILL_LABEL = { chrome: "Fill in Chrome", claude_code: "Claude Code", codex: "ChatGPT (Codex)", atlas: "ChatGPT Atlas", manual: "By hand" };
const KIND_WORD = { interview: "interview", assessment: "assessment", recruiter_call: "recruiter call", coffee_chat: "coffee chat", info_session: "info session", career_fair: "career fair" };
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const AP = { data: null };

async function renderAutopilot() {
  const main = $("#main");
  const arriving = S.arrive;  // coming to Today, as opposed to a background refresh of it
  S.arrive = false;
  if (renderAutopilot.pid !== S.pid || (arriving && !main.querySelector(".ph-today"))) main.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  renderAutopilot.pid = S.pid;
  const boot = arriving ? jarvisBootStart() : null;
  let d, game;
  try { [d, game] = await Promise.all([api(`/api/autopilot?profile_id=${S.pid}`), loadPath().catch(() => null)]); }
  catch (e) { boot?.end(); main.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (S.tab !== "autopilot") { boot?.end(); return; }
  AP.data = d;
  const st = d.settings, run = d.state, last = d.last_run, w = d.week;
  const ready = d.queue.filter((q) => q.status === "ready" || q.status === "opened");
  const done = d.queue.filter((q) => !(q.status === "ready" || q.status === "opened"));
  setReadyCount(ready.length);
  watchPolish(ready);
  AP.count = AP.count || st.per_day;
  const p = profile();
  const hour = new Date().getHours();
  const hello = `${hour < 5 ? "Up late" : hour < 12 ? "Morning" : hour < 18 ? "Afternoon" : "Evening"}, <em>${esc((p?.name || "there").split(" ")[0])}</em>.`;
  const fresh = last && last.finished_at && Date.now() / 1000 - last.finished_at < 600;
  const mood = run.running ? "thinking" : last?.error && !last?.queued ? "oops" : fresh && last.queued ? "cheer" : "hi";
  const when = (ts) => new Date(ts * 1000).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
  const status = !st.on ? "Autopilot is off: Jarvis works when you press Prepare."
    : d.paused ? `Paused until ${esc(st.paused_until)}.` : `Next run ${d.next_run ? esc(when(d.next_run)) : "—"}.`;
  const first = ready[0];
  const n = (v) => `<span class="n" data-n="${Number(v) || 0}">${Number(v || 0).toLocaleString()}</span>`;
  const soon = (d.upcoming || []).find((u) => u.kind !== "book" && u.kind !== "block"), book = (d.upcoming || []).find((u) => u.kind === "book");
  const said = [
    ready.length ? `${n(ready.length)} application${ready.length === 1 ? " is" : "s are"} ready` : "",
    S.app.counts.new_24h ? `${n(S.app.counts.new_24h)} posting${S.app.counts.new_24h === 1 ? "" : "s"} opened today` : "",
    soon ? `<span class="act">your ${esc(soon.company)} ${esc((soon.label || KIND_WORD[soon.kind] || soon.kind).toLowerCase())} is ${esc(when(soon.at))}</span>` : "",
    book ? `<span class="act">${esc(book.company || book.label)} wants you to book a time</span>` : "",
    d.drafts.length ? `${n(d.drafts.length)} draft${d.drafts.length === 1 ? "" : "s"} wait${d.drafts.length === 1 ? "s" : ""} for you` : "",
  ].filter(Boolean);
  const sentence = said.length ? `${said.length > 1 ? `${said.slice(0, -1).join(", ")} and ${said[said.length - 1]}` : said[0]}.`
    : "Nothing urgent. A good day to get a few applications out.";
  const sentenceCap = sentence.replace(/^(<span[^>]*>)?([a-z])/, (m, tag, ch) => (tag || "") + ch.toUpperCase());
  const now = new Date();
  const stamp = `${now.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} · ${now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
  const as = p?.app_stats || {};
  const stages = [["applied", "applied"], ["oa", "oa"], ["interviewing", "interview"], ["offer", "offer"]];
  const pipeTotal = stages.reduce((t, [k]) => t + (as[k] || 0), 0);
  const weekPct = w.goal ? Math.min(100, Math.round((100 * w.applied) / w.goal)) : 0;
  const coding = game?.coding;
  const coming = [
    ...(d.upcoming || []).map((u) => ({ at: u.at, mine: u.kind !== "book", act: u.kind === "book" || u.kind === "assessment",
      html: u.kind === "block" ? esc(u.title) : u.kind === "book" ? `Book a time with ${u.url ? `<a href="${safeUrl(u.url)}" target="_blank" rel="noopener">${esc(u.company || u.label)}</a>` : esc(u.company || u.label)}`
        : `<b>${esc(u.company)}</b> ${esc((u.label || KIND_WORD[u.kind] || u.kind).toLowerCase())}` })),
    ...d.events.slice(0, 4).map((e) => ({ at: e.start_ts, html: e.url ? `<a href="${safeUrl(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title) })),
  ].sort((x, y) => (x.at || 9e12) - (y.at || 9e12)).slice(0, 6);
  const tick = (ts) => ts ? new Date(ts * 1000).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }) : "to book";
  const progressLine = run.running ? `<span class="spinner"></span> ${esc(run.step || "Working…")}` : last ? `Last run ${ago(last.finished_at || last.started_at)} · prepared ${last.queued}${last.error ? ` · <span style="color:var(--warn)">${esc(last.error)}</span>` : ""} · ${status}` : status;
  const goControls = `<div class="nm-go">
          <div class="stepper" title="How many to prepare this time">
            <button type="button" class="icon-btn" id="ap-minus" aria-label="Fewer">−</button>
            <input type="number" id="ap-count" min="1" max="${d.max_per_run}" value="${AP.count}" aria-label="How many">
            <button type="button" class="icon-btn" id="ap-plus" aria-label="More">+</button>
          </div>
          <button class="btn ${first ? "btn-ghost" : "btn-xl"}" id="ap-run" ${run.running ? "disabled" : ""}>${run.running ? '<span class="spinner"></span> Jarvis is on it…' : `Prepare ${AP.count} now`}</button>
        </div>`;
  const fw = st.fill_with || "chrome";
  main.innerHTML = `<div class="ph-today">
    <section class="ph-head">
      <div>
        <div class="ph-eyebrow"><span class="live-dot"></span><span class="tel">${esc(stamp)} · brain: ${esc(S.app.ai_label || "built-in rules")}</span></div>
        <h1 class="ph-hello">${hello}</h1>
        <p class="ph-sentence">${sentenceCap}</p>
      </div>
      <div class="ph-head-jarvis today-jarvis">${jarvisSvg(mood, 128, { label: "Jarvis" })}</div>
    </section>

    <section class="next-move ${first ? "ap-row" : ""}" ${first ? `data-q="${esc(first.job_id)}"` : ""}>
      <div style="min-width:0">
        <div class="tel">${first ? `Next move · ready to apply${ready.length > 1 ? ` · 1 of ${ready.length}` : ""}` : "Next move · prepare applications"}</div>
        ${first ? `<div class="nm-title"><a href="${safeUrl(first.url)}" target="_blank" rel="noopener">${esc(first.title)}</a></div>
          <div class="nm-meta">${[first.company, first.where, `prepared ${ago(first.created_at)}`].filter(Boolean).map(esc).join(" · ")}</div>
          ${first.why ? `<div class="nm-why">${esc(first.why)}</div>` : ""}`
          : `<div class="nm-title">Let Jarvis find your best ${AP.count}.</div><div class="nm-meta">ranked for you · tailored resume + letter + answers · you press submit</div>`}
        <div class="nm-progress" id="ap-progress" style="margin-top:14px">${progressLine}</div>
      </div>
      <div class="nm-side">
        ${first ? `<div class="row">${scoreRing(first.score)}</div>
          <div class="nm-go"><button class="btn btn-xl" data-qa="kit">Review & apply</button>
            <button class="btn btn-ghost" data-qa="open" title="Open it with ${esc(FILL_LABEL[fw])}. You press Submit.">${esc(FILL_LABEL[fw])}</button></div>
          ${ready.length > 1 ? `<button class="btn btn-ghost btn-sm" id="ap-session">Apply to all ${ready.length} →</button>` : ""}` : ""}
        ${goControls}
      </div>
    </section>

    <section class="tele" aria-label="Telemetry">
      <button class="tele-cell" id="tele-apps" title="Your applications">
        <span class="tel">Pipeline</span>
        <span class="tele-v"><span data-n="${as.applied_total || 0}">${as.applied_total || 0}</span><small>applied</small></span>
        <span class="tele-sub">${as.in_progress || 0} in progress · ${as.offer || 0} offer${as.offer === 1 ? "" : "s"}${as.stale ? ` · <span style="color:var(--signal)">${as.stale} quiet 3+ wks</span>` : ""}</span>
        <span class="funnel">${stages.map(([k]) => `<i class="${k === "offer" || k === "interviewing" ? "hot" : "on"}" style="flex:${Math.max(1, as[k] || 0)}${!(as[k]) ? ";opacity:.25" : ""}" title="${k}: ${as[k] || 0}"></i>`).join("")}${pipeTotal ? "" : '<i style="flex:1"></i>'}</span>
      </button>
      <button class="tele-cell" id="ap-set-goal-cell" title="Weekly goal">
        <span class="tel">This week</span>
        <span class="tele-v"><span data-n="${w.applied || 0}">${w.applied || 0}</span><small>${w.goal ? `of ${w.goal}` : "applied"}</small></span>
        <span class="tele-sub">${w.goal ? `${w.ready} ready · ${w.days_left} day${w.days_left === 1 ? "" : "s"} left` : "no weekly goal yet: set one"}</span>
        <span class="tele-bar"><i data-w="${weekPct}"></i></span>
      </button>
      <button class="tele-cell" id="ap-path" title="Your Path: streak, XP and NeetCode">
        <span class="tel">NeetCode · streak</span>
        <span class="tele-v"><span data-n="${coding?.solved || 0}">${coding?.solved || 0}</span><small>/ ${coding?.total || 150}</small></span>
        <span class="tele-sub">${game ? `${game.streak ? `🔥 ${game.streak}-day streak` : "no streak yet"} · ${game.today}/${game.goal} XP today${coding?.reviews_due ? ` · ${coding.reviews_due} to review` : ""}` : "Path loading"}</span>
        <span class="tele-bar"><i data-w="${coding ? Math.round((100 * coding.solved) / (coding.total || 150)) : 0}"></i></span>
      </button>
    </section>

    <section class="ph-cols">
      <div>
        <div class="ph-block-h"><h2>Coming up</h2><span class="spacer"></span><a href="#" class="tel" id="ap-events">calendar →</a></div>
        <div class="card card-pad timeline">${coming.length ? coming.map((c) => `<div class="tl-item ${c.mine ? "mine" : ""} ${c.act ? "act" : ""}"><span class="tl-when">${esc(tick(c.at))}</span><span class="tl-dot"></span><span class="tl-what">${c.html}</span></div>`).join("")
          : '<div class="tl-empty">A clear week. Interviews and OAs from your inbox land here.</div>'}</div>
      </div>
      <div>
        <div class="ph-block-h"><h2>Needs you</h2><span class="tel">${d.drafts.length || ""}</span></div>
        <div class="card card-pad stack">
        ${d.drafts.length ? d.drafts.slice(0, 5).map((x) => `<div class="draft-row"><span class="tag ${x.kind === "prep" ? "good" : ""}">${{ followup: "Follow-up", thankyou: "Thank-you", prep: "Prep", research: "Research", message: "Message", weakspots: "Weak spots", workbook: "Workbook" }[x.kind]}</span>
          <span class="small" style="flex:1;min-width:0">${esc(x.title)}</span>
          <button class="icon-btn" data-draft-view="${x.id}" title="Open">↗</button><button class="icon-btn" data-draft-copy="${x.id}" title="Copy">⧉</button><button class="icon-btn" data-draft-done="${x.id}" title="Done">✓</button></div>`).join("")
          : '<div class="tl-empty">All clear. Follow-ups, thank-yous and interview prep land here.</div>'}
        </div>
      </div>
    </section>

    <section class="card plan">
      <div class="plan-head"><h3>Jarvis's plan</h3><span class="spacer"></span>
        <label class="switch" title="Run on its own, on schedule"><input type="checkbox" id="ap-on" ${st.on ? "checked" : ""}><span></span>${st.on ? "Autopilot on" : "Autopilot off"}</label></div>
      <p class="plan-sentence" id="plan-sentence">${planSentence(st, d)}</p>
      <div class="chips presets">${PRESETS.map((x, i) => `<button class="chip" data-preset="${i}" title="${esc(x.hint)}">${x.label}</button>`).join("")}</div>
      <form class="tell" id="ap-tell"><input type="text" id="ap-tell-text" maxlength="500" placeholder="Tell Jarvis: “15 a day, only startups in Toronto, skip Amazon”" autocomplete="off"><button class="btn btn-sm">Tell Jarvis</button></form>
      <div class="plan-links small"><a href="#" id="ap-preview">Preview picks</a><a href="#" id="ap-settings">All settings</a><a href="#" id="ap-engine">Brain: ${esc(S.app.ai_label || "built-in rules")}</a></div>
    </section>

    <div class="section-title"><h2>Ready to apply</h2><span class="tel">${ready.length}</span></div>
    <div class="card joblist">${ready.length ? ready.map(queueRow).join("") : `<div class="empty">Nothing waiting. Pick a number above and press Prepare.</div>`}</div>
    ${done.length ? `<details class="small" style="margin-top:8px"><summary class="muted" style="cursor:pointer">Done, skipped or closed (${done.length})</summary><div class="card joblist" style="margin-top:8px">${done.map(queueRow).join("")}</div></details>` : ""}

    <details class="card card-pad" style="margin-top:16px"><summary style="cursor:pointer"><b>History & activity</b> <span class="muted small">every run, and everything the AI changed (with Undo)</span></summary>
      ${last && last.brief ? `<div style="margin-top:10px">${briefBlock(last.brief)}</div>` : ""}
      <div class="grid-2" style="margin-top:12px">
        <div class="stack">${d.history.map((h) => `<details class="small"><summary style="cursor:pointer">${new Date(h.started_at * 1000).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · ${esc(h.trigger)} · prepared ${h.queued || 0}${h.error ? " · ⚠️" : ""}</summary><div class="ap-brief" style="margin:6px 0 0 12px">${esc(h.brief || h.error || "").replace(/\n/g, "<br>")}</div></details>`).join("") || '<div class="muted small">No runs yet.</div>'}</div>
        <div>${d.activity.length ? `<table class="ap-activity"><tbody>${d.activity.map((a) => `<tr>
            <td class="muted small" style="white-space:nowrap">${esc(ago(a.ts))}</td><td class="small" style="white-space:nowrap">${esc(SOURCE_LABEL[a.source] || a.source)}</td>
            <td class="small">${a.ok ? "" : "⚠️ "}${esc(a.summary || a.tool)}</td>
            <td style="text-align:right">${a.can_undo ? `<button class="btn btn-ghost btn-sm" data-undo-act="${a.id}">Undo</button>` : a.undone ? '<span class="muted small">undone</span>' : ""}</td></tr>`).join("")}</tbody></table>`
          : `<div class="muted small">Nothing yet.</div>`}</div>
      </div></details></div>`;

  const setCount = (n) => {
    AP.count = Math.max(1, Math.min(d.max_per_run, Number(n) || 1));
    $("#ap-count").value = AP.count;
    if (!AP.data.state.running) $("#ap-run").textContent = `Prepare ${AP.count} now`;
  };
  $("#ap-minus").addEventListener("click", () => setCount(AP.count - (AP.count > 10 ? 5 : 1)));
  $("#ap-plus").addEventListener("click", () => setCount(AP.count + (AP.count >= 10 ? 5 : 1)));
  $("#ap-count").addEventListener("change", (e) => setCount(e.target.value));
  $("#ap-on").addEventListener("change", (e) => saveAP({ on: e.target.checked }).then(renderAutopilot));
  $("#ap-settings").addEventListener("click", (e) => { e.preventDefault(); openAPSettings(); });
  $("#ap-set-goal")?.addEventListener("click", (e) => { e.preventDefault(); openAPSettings(); });
  $("#ap-set-goal-cell")?.addEventListener("click", () => openAPSettings());
  $("#tele-apps")?.addEventListener("click", () => goView("tracker"));
  const fillBars = () => main.querySelectorAll(".tele-bar i[data-w]").forEach((i) => { i.style.width = `${i.dataset.w}%`; });
  const reveal = () => {  // once per arrival: the page rises in, numbers roll up, bars fill
    main.querySelector(".ph-today")?.classList.add("arrive");
    jarvisCount(main);
    requestAnimationFrame(fillBars);
  };
  jarvisSay(run.running ? (run.step || "preparing applications").toLowerCase() : null, { key: "autopilot" });
  if (!arriving) fillBars();
  else if (boot) boot.say([`${(S.app.counts.active || 0).toLocaleString()} postings watched`, `${S.app.counts.new_24h || 0} new today`, `${ready.length} ready to apply`, soon ? `${soon.company} ${(soon.label || KIND_WORD[soon.kind] || soon.kind).toLowerCase()} ${when(soon.at)}` : "calendar clear"], hello.replace(/<[^>]+>/g, ""), reveal);
  else reveal();
  $("#ap-engine").addEventListener("click", (e) => { e.preventDefault(); openSettings("ai"); });
  $("#ap-events").addEventListener("click", (e) => { e.preventDefault(); switchTab("events"); });
  $("#ap-preview").addEventListener("click", (e) => { e.preventDefault(); openPreview(); });
  $("#ap-session")?.addEventListener("click", () => applySession(ready));
  $("#ap-path")?.addEventListener("click", () => goView("path"));
  main.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", async () => {
    const x = PRESETS[Number(b.dataset.preset)];
    await saveAP(x.change);
    if (x.change.per_day) AP.count = x.change.per_day;
    toast(`Plan: ${x.label}`);
    renderAutopilot();
  }));
  $("#ap-tell").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#ap-tell-text").value.trim();
    if (!text) return;
    const btn = e.target.querySelector("button");
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
    try {
      const r = await api("/api/autopilot/tell", { method: "POST", json: { profile_id: S.pid, text } });
      const n = Object.keys(r.changed).length;
      toast(n ? `Jarvis updated ${n} thing${n === 1 ? "" : "s"} in the plan` : "Jarvis's plan already says that", 4000);
      if (r.changed.per_day) AP.count = r.changed.per_day;
      renderAutopilot();
    } catch (err) { toast(err.message, 6000); btn.disabled = false; btn.textContent = "Tell Jarvis"; }
  });
  bindPlanChips($("#plan-sentence"));
  $("#ap-run").addEventListener("click", async () => {
    $("#ap-run").disabled = true; $("#ap-run").innerHTML = '<span class="spinner"></span> Jarvis is on it…';
    $(".today-jarvis").innerHTML = jarvisSvg("thinking", 128, { label: "Jarvis thinking" });
    jarvisSay(`picking your best ${AP.count}`, { key: "autopilot" });
    $("#ap-progress").innerHTML = `<span class="spinner"></span> Looking for your best ${AP.count} matches…`;
    try { await api("/api/autopilot/run", { method: "POST", json: { profile_id: S.pid, count: AP.count } }); }
    catch (e) { toast(e.message, 5000); return renderAutopilot(); }
    pollAutopilot.watching = true;
    pollAutopilot(800);
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

// ---------- Jarvis's plan: the settings as one sentence, each part clickable ----------
const PRESETS = [
  { label: "😌 Chill · 3 a day", hint: "A few strong ones, weekdays", change: { per_day: 3, days: [0, 1, 2, 3, 4], min_score: 75 } },
  { label: "🚶 Steady · 10 a day", hint: "A solid daily habit", change: { per_day: 10, days: [0, 1, 2, 3, 4, 5, 6], min_score: 70 } },
  { label: "🔥 All-in · 25 a day", hint: "Peak season: cast a wide net", change: { per_day: 25, days: [0, 1, 2, 3, 4, 5, 6], min_score: 60 } },
  { label: "🚀 Startups", hint: "YC and other startups only", change: { tiers: ["startup", "yc"] } },
  { label: "📈 Quant hunt", hint: "Quant & trading roles at quant firms", change: { categories: ["Quant"], tiers: ["quant"] } },
  { label: "🏠 Remote", hint: "Remote roles only", change: { locations: ["Remote"] } },
  { label: "↺ Any company", hint: "Clear the company and place filters", change: { tiers: [], locations: [], categories: [] } },
];
const ELIG_TEXT = { fit: "clearly open to me", open: "open to me or not stated", any: "even ones to check" };
const POSTED_TEXT = { 0: "any time", 3: "the last 3 days", 7: "the last week", 14: "the last 2 weeks", 30: "the last month" };

function planSentence(st, d) {
  const chip = (k, text, title) => `<button type="button" class="plan-chip" data-plan="${k}" title="${esc(title)}">${text}</button>`;
  const days = st.days.length === 7 ? "every day" : st.days.join() === "0,1,2,3,4" ? "every weekday" : st.days.map((x) => DAY_NAMES[x]).join(", ");
  const list = (xs, none) => (xs.length ? esc(xs.join(", ")) : none);
  const tiers = st.tiers?.length ? st.tiers.map((t) => d.tiers[t] || t).join(", ") : "any kind of company";
  // "in Toronto or remote", "that are remote", "anywhere I'd go"
  const places = st.locations.filter((x) => x.toLowerCase() !== "remote"), remote = places.length < st.locations.length;
  const where = places.length ? esc(places.join(", ")) + (remote ? " or remote" : "") : remote ? "remote" : "anywhere I'd go";
  const lead = places.length ? "in " : remote ? "that are " : "";
  return `${chip("days", esc(days[0].toUpperCase() + days.slice(1)), "Which days")} at ${chip("time", esc(st.time), "What time")}, prepare
    ${chip("per_day", `${st.per_day} application${st.per_day === 1 ? "" : "s"}`, "How many each run")} for
    ${chip("categories", list(st.categories, "my target roles"), "Which roles")} ${lead}${chip("locations", where, "Where")},
    at ${chip("tiers", esc(tiers), "What kind of company")}, scoring ${chip("min_score", `${st.min_score}+`, "Lowest match score")},
    ${chip("eligibility", ELIG_TEXT[st.eligibility] || st.eligibility, "Who it's open to")}, posted ${chip("posted_within", POSTED_TEXT[st.posted_within] || `the last ${st.posted_within} days`, "How recent")}${st.blocklist.length ? `, never ${chip("blocklist", list(st.blocklist, ""), "Companies to skip")}` : ""},
    ${chip("letter", st.letter ? "with cover letters" : "without cover letters", "Cover letters")}.`;
}

function bindPlanChips(root) {
  root.querySelectorAll("[data-plan]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); planPop(b); }));
}

function planPop(anchor) {
  const k = anchor.dataset.plan, st = AP.data.settings, d = AP.data;
  document.querySelector(".plan-pop")?.remove();
  const chips = (items, on, attr) => `<div class="chips">${items.map(([v, l]) => `<button type="button" class="chip ${on(v) ? "on" : ""}" ${attr}="${esc(v)}">${esc(l)}</button>`).join("")}</div>`;
  const body = {
    days: `${chips(DAY_NAMES.map((n, i) => [i, n]), (v) => st.days.includes(Number(v)), "data-day")}<div class="row small"><a href="#" data-days="0,1,2,3,4">Weekdays</a><a href="#" data-days="0,1,2,3,4,5,6">Every day</a></div>`,
    time: `<input type="time" id="pp-time" value="${esc(st.time)}">`,
    per_day: `<div class="row"><input type="range" id="pp-n" min="1" max="${d.max_per_run}" value="${st.per_day}"><b id="pp-nv">${st.per_day}</b></div><div class="muted small">Up to ${d.max_per_run} a run. With your AI, it hand-picks the best 8; the rest go by match score.</div>`,
    categories: chips(S.app.categories.filter((c) => c !== "Other").map((c) => [c, c]), (v) => st.categories.includes(v), "data-cat") + '<div class="muted small">None picked = your profile\'s targets.</div>',
    locations: `<input type="text" id="pp-places" value="${esc(st.locations.join(", "))}" placeholder="Toronto, Waterloo, Remote"><div class="muted small">Comma separated. Empty = anywhere you'd go.</div>`,
    tiers: chips(Object.entries(d.tiers).filter(([t]) => t !== "other"), (v) => (st.tiers || []).includes(v), "data-tier") + '<div class="muted small">None picked = any kind of company.</div>',
    min_score: `<div class="row"><input type="range" id="pp-min" min="40" max="95" step="5" value="${st.min_score}"><b id="pp-minv">${st.min_score}</b></div>`,
    eligibility: chips(Object.entries(ELIG_TEXT), (v) => st.eligibility === v, "data-elig"),
    posted_within: chips(Object.entries(POSTED_TEXT), (v) => Number(v) === st.posted_within, "data-posted"),
    blocklist: `<input type="text" id="pp-block" value="${esc(st.blocklist.join(", "))}"><div class="muted small">Companies Jarvis never picks, comma separated.</div>`,
    letter: chips([["1", "With cover letters"], ["0", "Without"]], (v) => (v === "1") === !!st.letter, "data-letter"),
  }[k];
  const pop = document.createElement("div");
  pop.className = "plan-pop card";
  pop.innerHTML = `<div class="stack">${body}<div class="row"><span class="spacer"></span><button type="button" class="btn btn-sm" id="pp-save">Done</button></div></div>`;
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${window.scrollY + r.bottom + 6}px`;
  pop.style.left = `${Math.max(12, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 12))}px`;
  pop.querySelectorAll("[data-day],[data-cat],[data-tier]").forEach((b) => b.addEventListener("click", () => b.classList.toggle("on")));
  pop.querySelectorAll("[data-elig],[data-posted],[data-letter]").forEach((b) => b.addEventListener("click", () => {
    b.parentElement.querySelectorAll(".chip").forEach((x) => x.classList.remove("on")); b.classList.add("on");
  }));
  pop.querySelectorAll("[data-days]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault(); const on = a.dataset.days.split(",");
    pop.querySelectorAll("[data-day]").forEach((b) => b.classList.toggle("on", on.includes(b.dataset.day)));
  }));
  pop.querySelector("#pp-n")?.addEventListener("input", (e) => ($("#pp-nv").textContent = e.target.value));
  pop.querySelector("#pp-min")?.addEventListener("input", (e) => ($("#pp-minv").textContent = e.target.value));
  const close = () => { pop.remove(); document.removeEventListener("click", outside); };
  const outside = (e) => { if (!pop.contains(e.target)) close(); };
  setTimeout(() => document.addEventListener("click", outside), 0);
  pop.querySelector("#pp-save").addEventListener("click", async () => {
    const on = (attr) => [...pop.querySelectorAll(`[${attr}].on`)].map((b) => b.getAttribute(attr));
    const words = (id) => pop.querySelector(id).value.split(",").map((x) => x.trim()).filter(Boolean);
    const change = {
      days: () => ({ days: on("data-day").map(Number) }), time: () => ({ time: pop.querySelector("#pp-time").value }),
      per_day: () => ({ per_day: Number(pop.querySelector("#pp-n").value) }), categories: () => ({ categories: on("data-cat") }),
      locations: () => ({ locations: words("#pp-places") }), tiers: () => ({ tiers: on("data-tier") }),
      min_score: () => ({ min_score: Number(pop.querySelector("#pp-min").value) }), eligibility: () => ({ eligibility: on("data-elig")[0] || "any" }),
      posted_within: () => ({ posted_within: Number(on("data-posted")[0] || 0) }), blocklist: () => ({ blocklist: words("#pp-block") }),
      letter: () => ({ letter: on("data-letter")[0] === "1" }),
    }[k]();
    close();
    await saveAP(change);
    if (change.per_day) AP.count = change.per_day;
    renderAutopilot();
  });
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
      ${live && ["pending", "running"].includes(q.polish) ? `<div class="muted small ap-polish" title="You can apply now with the quick version, or wait a moment for the AI's.">Writing the letter and answers…</div>` : ""}
      ${live && q.polish === "failed" ? `<div class="muted small">The AI couldn't finish this one, so it has the quick letter and answers.</div>` : ""}
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
  const reasons = AP.data?.skip_reasons || { location: "Wrong place", company: "Not that company", role: "Wrong kind of role", senior: "Too senior", term: "Wrong term", other: "Something else" };
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
      <div class="row"><span class="small">Prepare up to</span><input type="number" min="1" max="${AP.data.max_per_run}" data-k="per_day" data-num="1" value="${st.per_day}" style="width:72px"><span class="small">per run, scoring at least</span>${sel("min_score", [50, 60, 65, 70, 75, 80, 85].map((n) => [n, n]))}</div>
      <div class="row"><span class="small">Weekly goal</span><input type="number" min="0" max="300" data-k="weekly_goal" data-num="1" value="${st.weekly_goal || 0}" style="width:80px"><span class="muted small">applications (0 = off). Autopilot paces itself to hit it.</span></div>
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
      ${hidden("events") ? "" : `<div class="row">${chk("auto_events", "Save events from companies I follow and hackathons near me")}</div>`}
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

// the letter and answers finish behind the list: take the note off each row as it's done, leaving the rest of the page alone
function watchPolish(rows) {
  clearTimeout(watchPolish._t);
  if (!rows.some((q) => ["pending", "running"].includes(q.polish))) return;
  watchPolish._t = setTimeout(async () => {
    if (S.tab !== "autopilot") return;
    const d = await api(`/api/autopilot?profile_id=${S.pid}`).catch(() => null);
    if (!d || S.tab !== "autopilot") return;
    const live = d.queue.filter((q) => q.status === "ready" || q.status === "opened");
    for (const q of live) {
      const note = document.querySelector(`[data-q="${CSS.escape(q.job_id)}"] .ap-polish`);
      if (!note || ["pending", "running"].includes(q.polish)) continue;
      if (q.polish === "failed") { note.classList.remove("ap-polish"); note.textContent = "The AI couldn't finish this one, so it has the quick letter and answers."; }
      else note.remove();
    }
    watchPolish(live);
  }, 4000);
}

function pollAutopilot(wait = 1200) {
  clearTimeout(pollAutopilot._t);
  pollAutopilot._t = setTimeout(async () => {
    if (S.tab !== "autopilot") return;
    const d = await api(`/api/autopilot?profile_id=${S.pid}`).catch(() => null);
    if (!d) return;
    if (d.state.running) { const p = $("#ap-progress"); if (p) p.innerHTML = `<span class="spinner"></span> ${esc(d.state.step || "Working…")}`; return pollAutopilot(); }
    await renderAutopilot();
    if (pollAutopilot.watching) {  // the run the person started just finished: say how it went
      pollAutopilot.watching = false;
      const last = d.last_run || {};
      if (last.error && !last.queued) toast(`Jarvis hit a problem: ${last.error}`, 7000);
      else if (last.queued) { toast(`Jarvis prepared ${last.queued} application${last.queued === 1 ? "" : "s"}: review and submit when you're ready`, 6000); jarvisConfetti($("#main")); }
      else toast("Nothing new fits Jarvis's plan right now: loosen it a little (score, places or kind of company)", 6000);
    }
  }, wait);
}
