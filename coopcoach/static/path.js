/* The Path: a streak with shields, XP against a daily goal, levels with Jarvis names, daily quests, a year of activity, and a
   long trail through the NeetCode 150: every problem a stop, real-work stops in between (applying, what comes back from
   companies, people, events), a chest for each topic, crowns for solving again later, and tests: real problems on LeetCode
   against the clock. Also the list view of the 150 (each try logged with its time and notes, misses brought back for
   review, problems starred for a job, a kind of role or a company), a link to a public LeetCode profile that logs solves
   by itself, and milestones Jarvis jumps for.
   Loaded before app.js; uses its helpers at call time. */

const PATH = { data: null, pid: null, request: 0, refreshing: false, nextRefresh: 0 };

async function loadPath({ quiet = false } = {}) {
  const pid = S.pid, request = ++PATH.request;
  const d = await api(`/api/path?profile_id=${pid}`);
  if (pid !== S.pid || request !== PATH.request) return null;
  const before = PATH.pid === pid ? PATH.data : null;
  PATH.data = d;
  PATH.pid = pid;
  // The daily goal cheers once a day, when XP crosses it (not when the goal itself is lowered).
  const reached = before && before.goal === d.goal && before.today < d.goal && d.today >= d.goal && !goalCheered();
  if (!quiet) celebrate((reached ? [{ title: "Daily goal reached!", local: true }] : []).concat(d.new_milestones));
  return d;
}

function goalCheered() {
  const key = `cc-goal-cheered:${S.pid}`, today = new Date().toDateString();
  try {
    if (localStorage.getItem(key) === today) return true;
    localStorage.setItem(key, today);
  } catch {}
  return false;
}

function pathStrip(d, compact = false) {
  const pct = Math.min(100, Math.round((100 * d.today) / d.goal));
  const lv = d.level, lvPct = lv.to ? Math.round((100 * (lv.xp - lv.from)) / (lv.to - lv.from)) : 100;
  return `<div class="path-strip ${compact ? "compact" : ""}">
    <div class="ps-item" title="Days in a row you hit your daily goal (best: ${d.best_streak}). Seven in a row earn a shield 🛡, which saves the streak on a missed day."><span class="ps-flame ${d.streak ? "lit" : ""}">🔥</span><b>${d.streak}</b><span class="muted small">day streak${d.shields ? ` ${"🛡".repeat(d.shields)}` : ""}</span></div>
    <div class="ps-item" title="XP today against your daily goal">${xpRing(pct)}<b>${d.today}</b><span class="muted small">/ ${d.goal} XP today</span></div>
    <div class="ps-item ps-level" title="${lv.to ? `${lv.to - lv.xp} XP to level ${lv.level + 1} (${esc(lv.next_name)})` : "Top level"}">
      <span class="ps-badge">${lv.level}</span><div><b>${esc(lv.name)}</b><div class="ps-bar"><i style="width:${lvPct}%"></i></div></div></div>
  </div>`;
}

function xpRing(pct) {
  const r = 13, c = 2 * Math.PI * r;
  return `<svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true"><circle cx="16" cy="16" r="${r}" fill="none" stroke="var(--border)" stroke-width="4"/>
    <circle cx="16" cy="16" r="${r}" fill="none" stroke="${pct >= 100 ? "var(--good)" : "var(--warn)"}" stroke-width="4" stroke-linecap="round" stroke-dasharray="${(c * pct / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 16 16)"/>
    <text x="16" y="20" text-anchor="middle" font-size="11">⚡</text></svg>`;
}

async function renderPath({ background = false } = {}) {
  const main = $("#main");
  if (!PATH.data || renderPath.pid !== S.pid) main.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  renderPath.pid = S.pid;
  let d;
  try { d = await loadPath({ quiet: background }); }
  catch (e) {
    PATH.nextRefresh = Date.now() + 15000;
    if (!background && !profile()?.locked) main.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  if (!d || S.tab !== "path") return;
  PATH.nextRefresh = Date.now() + (d.leetcode_syncing ? 2000 : 15000);
  // The countdown changes every second; it already has its own clock. Only redraw real progress changes.
  const signature = JSON.stringify(d, (key, value) => key === "seconds_left" ? undefined : value);
  if (background && (signature === renderPath.signature || main.contains(document.activeElement)
      && document.activeElement.matches("input, textarea, select") || $("#lc-name")?.value.trim())) return;
  const keepPosition = !!main.querySelector(".path-head"), position = { x: window.scrollX, y: window.scrollY };
  const anchor = keepPosition && [...main.querySelectorAll(".trail-sec")].find((el) => el.getBoundingClientRect().bottom > 0);
  const anchorTop = anchor ? anchor.getBoundingClientRect().top : 0, anchorId = anchor?.id;
  const yearLeft = keepPosition ? main.querySelector(".year-grid")?.scrollLeft : null;
  renderPath.signature = signature;
  // where Jarvis stood on the trail, so he can hop to the next stop after a solve. Under an open window (logging a try)
  // the hop waits until the trail shows again.
  const stood = (() => {
    const j = main.querySelector(".lane-code .node-jarvis");
    if (!j) return null;
    const r = j.getBoundingClientRect();
    return { node: j.parentElement.querySelector("[data-node]")?.dataset.node, x: r.left, y: r.top };
  })();
  const covered = !!$("#modal-root")?.children.length;
  const hopFrom = covered ? null : PATH.hopFrom || stood;
  PATH.hopFrom = covered ? PATH.hopFrom || stood : null;
  const maxDay = Math.max(d.goal, ...d.week.map((w) => w.xp));
  const left = Math.max(0, d.goal - d.today);
  const say = d.today >= d.goal ? `Goal done today! Every extra bit counts toward ${d.level.to ? esc(d.level.next_name) : "legend status"}.`
    : d.streak ? `${left} XP to keep your ${d.streak}-day streak alive.` : `${left} XP to start a streak today. One application does it.`;
  const c = d.coding, nx = c.next;
  const lane = LS.get("path.lane") === "hunt" ? "hunt" : "code";
  const huntDone = d.hunt.reduce((n, x) => n + x.have, 0);
  main.innerHTML = `
    <section class="path-head card">
      <div class="path-jarvis">${jarvisSvg(d.today >= d.goal ? "cheer" : "hi", 92, { label: "Jarvis" })}<div class="bubble">${say}</div></div>
      ${pathStrip(d)}
      <div class="path-week" aria-label="XP this week">${d.week.map((w) => `<div class="pw-day" title="${w.day}: ${w.xp} XP"><div class="pw-bar ${w.xp >= d.goal ? "met" : ""}" style="height:${Math.max(4, Math.round((60 * w.xp) / maxDay))}px"></div><span>${new Date(w.day + "T12:00").toLocaleDateString(undefined, { weekday: "narrow" })}</span></div>`).join("")}
        <div class="pw-goal" style="bottom:${18 + Math.round((60 * d.goal) / maxDay)}px" title="Daily goal"></div></div>
      <div class="path-goal small muted">Daily goal: ${Object.entries(d.goals).map(([xp, name]) => `<button class="chip ${Number(xp) === d.goal ? "on" : ""}" data-goal="${xp}">${name} · ${xp} XP</button>`).join("")}</div>
    </section>
    <section class="card card-pad next-up">
      <div style="min-width:0"><div class="muted small">🧠 NeetCode 150 · <b>${c.solved}/${c.total}</b>${c.topic ? ` · ${esc(c.topic.name)} ${c.topic.solved}/${c.topic.total}` : ""} · ${["Easy", "Medium", "Hard"].map((lv) => `${lv[0]} ${c.by_level[lv].solved}/${c.by_level[lv].total}`).join(" · ")}${c.reviews_due ? ` · <b>${c.reviews_due} to review</b>` : ""}</div>
        ${nx ? `<h3>${nx.review_at ? "Review: " : "Next up: "}<a href="${safeUrl(nx.url)}" target="_blank" rel="noopener">${esc(nx.title)} ${extIcon}</a></h3>
          <div class="small"><span class="tag lvl-${nx.level.toLowerCase()}">${nx.level}</span>${nx.premium ? ` <a class="tag" href="${safeUrl(nx.free_url)}" target="_blank" rel="noopener" title="LeetCode Premium on LeetCode">free on NeetCode ${extIcon}</a>` : ""} <span class="muted">${esc(nx.topic)}${nx.last ? ` · last try: ${esc(RESULT_LABEL[nx.last.result])}` : ""}</span></div>`
        : `<h3>You've solved the whole NeetCode 150. 🎉</h3>`}</div>
      <div class="row" style="gap:6px;flex-wrap:wrap">${nx ? `<button class="btn btn-primary btn-sm" data-start="${esc(nx.slug)}">⏱ Start</button><button class="btn btn-sm" data-log="${esc(nx.slug)}">Log a try</button>` : ""}
        <button class="btn btn-ghost btn-sm" id="open-ladder">Open the NeetCode 150</button></div>
    </section>
    ${d.test ? `<section class="card card-pad test-banner"><div><b>⏳ Test running</b> <span class="muted small">${d.test.problems.length} problems · <span data-clock="${d.test.deadline}">${testClock(d.test.deadline)}</span></span></div><button class="btn btn-primary btn-sm" id="test-open">Open it</button></section>` : ""}
    <section class="card card-pad quests">
      <div class="row"><h3 style="margin:0">⚡ Today's quests</h3><span class="muted small">${d.quests.filter((q) => q.done).length}/${d.quests.length} · +${d.quests[0].xp} XP each</span></div>
      <div class="quest-list">${d.quests.map((q) => `<div class="quest ${q.done ? "done" : ""}"><span>${q.done ? "✅" : "⬜"}</span>${esc(q.title)}</div>`).join("")}</div>
    </section>
    <details class="card card-pad year" ${LS.get("path.year") ? "open" : ""}>
      <summary class="row"><h3 style="margin:0">📅 Your year</h3><span class="muted small">${Object.values(d.year).filter((x) => x >= d.goal).length} days at your goal · this week ${d.week_met}/${d.week_target}</span></summary>
      ${yearGrid(d)}
    </details>
    <div class="lane-switch" role="tablist" aria-label="Which lane">
      <button role="tab" data-lane="code" aria-selected="${lane === "code"}">🧠 NeetCode <span class="muted small">${c.solved}/${c.total}</span></button>
      <button role="tab" data-lane="hunt" aria-selected="${lane === "hunt"}">📨 Job hunt <span class="muted small">${huntDone}/${d.hunt_goals}</span></button>
    </div>
    <div class="lanes" data-show="${lane}">
      <div class="lane lane-code">
        <div class="trail-top">
          <div class="row"><h2 style="margin:0">🧠 NeetCode</h2><span class="muted small">${c.solved}/${c.total} problems · ${c.topics_done}/${d.sections.length} chests · ${d.counts.tests_passed} 🏅 · ${c.crowns} 👑</span><span class="spacer"></span>
            ${d.sections.some((x) => x.has_here) ? `<button class="btn btn-sm" data-here="code">🪿 Where I am</button>` : ""}</div>
          <div class="lc-link card card-pad ${d.leetcode_user ? "linked" : ""}" id="lc-link">${lcLink(d)}</div>
        </div>
        <div class="trail-map">${d.sections.map((x) => `<button class="tm ${x.done ? "done" : x.has_here ? "here" : ""}" data-jump="${esc(x.id)}" title="${esc(x.name)}: ${x.solved}/${x.total}"><i style="width:${Math.round((100 * x.solved) / x.total)}%"></i><span>${x.done ? "🏆 " : ""}${esc(x.name)}</span></button>`).join("")}</div>
        ${d.sections.map((x, si) => trailSection(x, si, d.sections.length)).join("")}
      </div>
      <div class="lane lane-hunt">
        <div class="trail-top">
          <div class="row"><h2 style="margin:0">📨 Job hunt</h2><span class="muted small">${huntDone}/${d.hunt_goals} goals · ${d.hunt.filter((x) => x.done).length}/${d.hunt.length} stages</span><span class="spacer"></span>
            ${d.hunt.some((x) => x.has_here) ? `<button class="btn btn-sm" data-here="hunt">🪿 Where I am</button>` : ""}</div>
          <div class="muted small">Moves with what you really do: preparing and sending applications, replies, interviews, events and people. Any order.</div>
        </div>
        ${d.hunt.map((x, si) => huntStage(x, si, d.hunt.length)).join("")}
      </div>
    </div>
    <details class="muted small" style="margin-top:12px"><summary>How you earn XP</summary><div style="margin-top:6px">
      You earn XP for real work: <b>applying</b> (+50), <b>solving a NeetCode problem</b> (+20 to +50, harder is more), or <b>logging a try</b> you couldn't finish (+5).
      Finish a topic to open its chest (+50). Do the steps in any order.</div></details>`;
  main.querySelectorAll("[data-goal]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/path/goal", { method: "POST", json: { profile_id: S.pid, goal: Number(b.dataset.goal) } });
    toast(`Daily goal: ${b.textContent}`); renderPath();
  }));
  main.querySelectorAll("[data-node]").forEach((b) => b.addEventListener("click", () => {
    const [si, ni] = b.dataset.node.split(":").map(Number);
    openStop(d.sections[si].nodes[ni]);
  }));
  main.querySelectorAll("[data-goal-node]").forEach((b) => b.addEventListener("click", () => {
    const [si, gi] = b.dataset.goalNode.split(":").map(Number);
    openStop(d.hunt[si].goals[gi]);
  }));
  main.querySelectorAll("[data-lane]").forEach((b) => b.addEventListener("click", () => {
    LS.set("path.lane", b.dataset.lane);
    main.querySelector(".lanes").dataset.show = b.dataset.lane;
    main.querySelectorAll("[data-lane]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  }));
  main.querySelector("details.year")?.addEventListener("toggle", (e) => LS.set("path.year", e.target.open));
  main.querySelectorAll("[data-mark]").forEach((b) => b.addEventListener("click", () => markSolved(d.sections.find((x) => x.id === b.dataset.mark))));
  document.documentElement.style.setProperty("--topbar-h", `${Math.round(document.querySelector(".topbar")?.getBoundingClientRect().height || 0)}px`);
  const toSection = (id, smooth = true) => document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  main.querySelectorAll("[data-jump]").forEach((b) => b.addEventListener("click", () => toSection(b.dataset.jump)));
  main.querySelectorAll("[data-here]").forEach((b) => b.addEventListener("click", () => main.querySelector(`.lane-${b.dataset.here} .stop .node.here`)?.scrollIntoView({ behavior: "smooth", block: "center" })));
  $("#open-ladder").addEventListener("click", () => openLadder());
  $("#test-open")?.addEventListener("click", () => openTest(d.test));
  bindLcLink(d);
  main.querySelector(".year-grid")?.scrollTo({ left: yearLeft ?? 99999 });
  if (keepPosition) {
    const moved = anchorId && document.getElementById(anchorId);
    window.scrollTo({ left: position.x, top: position.y + (moved ? moved.getBoundingClientRect().top - anchorTop : 0), behavior: "instant" });
  }
  jarvisHop(main, hopFrom);
  clearInterval(renderPath.tick);
  renderPath.tick = setInterval(() => {
    if (S.tab !== "path") return clearInterval(renderPath.tick);
    main.querySelectorAll("[data-clock]").forEach((e) => { e.textContent = testClock(Number(e.dataset.clock)); });
    if (Date.now() >= PATH.nextRefresh) refreshPath();
  }, 1000);
  main.querySelectorAll("[data-start]").forEach((b) => b.addEventListener("click", () => { startTimer(b.dataset.start, nx.url); openLadder(nx.topic_id, nx.slug); }));
  main.querySelectorAll("[data-log]").forEach((b) => b.addEventListener("click", () => openLadder(nx.topic_id, nx.slug, true)));
}

// After a solve, Jarvis hops along the trail from the stop he was on to the next one, and the solved stop pops.
function jarvisHop(main, from) {
  const j = main.querySelector(".lane-code .node-jarvis");
  if (!from || !j || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const now = j.parentElement.querySelector("[data-node]")?.dataset.node;
  if (!now || now === from.node) return;
  const r = j.getBoundingClientRect(), dx = from.x - r.left, dy = from.y - r.top;
  if (Math.abs(dy) > 900) return;  // too far down the trail to watch
  const lift = Math.min(70, 30 + Math.abs(dy) / 3);
  j.animate([{ translate: `${dx}px ${dy}px` }, { translate: `${dx * 0.5}px ${dy * 0.5 - lift}px`, offset: 0.45 }, { translate: "0 -6px", offset: 0.85 }, { translate: "0 0" }],
    { duration: 950, easing: "cubic-bezier(.45,0,.25,1)" });
  main.querySelector(`[data-node="${from.node}"]`)?.animate([{ scale: 1 }, { scale: 1.28 }, { scale: 1 }], { duration: 520, easing: "cubic-bezier(.2,.8,.2,1)" });
}

// Keep an open trail current after background imports or work done in another tab. The main content can
// refresh underneath a modal without replacing its fields, draft, or scroll position.
async function refreshPath() {
  if (typeof S === "undefined") return;  // focus can arrive while the page loads, before app.js defines S
  if (PATH.refreshing || document.hidden || S.tab !== "path" || profile()?.locked) return;
  PATH.refreshing = true;
  PATH.nextRefresh = Date.now() + 15000;
  try { await renderPath({ background: true }); }
  finally { PATH.refreshing = false; }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshPath(); });
window.addEventListener("focus", () => refreshPath());

// one topic of the trail: its stops wind left and right, Jarvis stands at the next problem
const WIND = [0, 46, 78, 46, 0, -46, -78, -46];
function trailSection(sec, si, of) {
  return `<section class="trail-sec tone-${si % 6}" id="sec-${esc(sec.id)}">
    <div class="sec-head"><div><div class="sec-n">Topic ${sec.n} of ${of}</div><h2>${sec.done ? "🏆 " : ""}${esc(sec.name)}</h2></div><span class="spacer"></span>
      <div class="sec-prog"><b>${sec.solved}/${sec.total}</b><span class="sec-bar"><i style="width:${Math.round((100 * sec.solved) / sec.total)}%"></i></span>
        ${sec.done ? "" : `<button class="sec-mark" data-mark="${esc(sec.id)}" title="Tick off the ones you've already solved on LeetCode">✓ Mark solved</button>`}</div></div>
    <div class="stops">${sec.nodes.map((n, ni) => trailStop(n, si, ni)).join("")}</div>
  </section>`;
}

// the job-hunt lane: stages of real-work goals, Jarvis at the first one not done yet
function huntStage(st, si, of) {
  return `<section class="trail-sec tone-${(si + 3) % 6}" id="stage-${esc(st.id)}">
    <div class="sec-head"><div><div class="sec-n">Stage ${st.n} of ${of}</div><h2>${st.done ? "🏆 " : ""}${esc(st.name)}</h2></div><span class="spacer"></span>
      <div class="sec-prog"><b>${st.have}/${st.total}</b><span class="sec-bar"><i style="width:${Math.round((100 * st.have) / st.total)}%"></i></span></div></div>
    <div class="stops">${st.goals.map((g, gi) => `<div class="stop" style="--off:${WIND[gi % WIND.length]}px">
      ${g.state === "here" ? `<div class="node-jarvis">${jarvisSvg("hi", 54, { label: "Jarvis: you are here" })}</div>` : ""}
      <button class="node work ${g.done ? "done" : g.state === "here" ? "here" : ""}" data-goal-node="${si}:${gi}" title="${esc(g.title)}">${g.done ? "✓" : g.icon}</button>
      <div class="node-label"><b>${esc(g.title)}</b>${g.need > 1 ? `<span class="muted small">${g.have}/${g.need}</span>` : ""}</div></div>`).join("")}</div>
  </section>`;
}

// ones solved before Co-op Coach was keeping track: LeetCode shows anyone only the last 20
function markSolved(sec) {
  const root = $("#modal-root");
  const left = sec.nodes.filter((n) => n.kind === "problem" && n.state !== "solved");
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack" style="max-width:460px">
    <div class="row"><h2 style="margin:0">✓ ${esc(sec.name)}: already solved</h2><span class="spacer"></span><button class="icon-btn" id="ms-x" aria-label="Close">✕</button></div>
    <div class="muted small">Tick the ones you've solved before. A linked LeetCode adds new solves by itself, but LeetCode only shows the last 20, so older ones go here.</div>
    <div class="ms-list">${left.map((n) => `<label class="row small"><input type="checkbox" value="${esc(n.slug)}"> <span class="tag lvl-${n.level.toLowerCase()}">${n.level[0]}</span> ${esc(n.title)}</label>`).join("")}</div>
    <div class="row"><button class="btn btn-primary" id="ms-go">Mark solved</button><button class="btn btn-ghost" id="ms-all">Tick all</button></div>
  </div></div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#ms-x").addEventListener("click", close);
  $("#ms-all").addEventListener("click", () => root.querySelectorAll(".ms-list input").forEach((i) => { i.checked = true; }));
  $("#ms-go").addEventListener("click", async (ev) => {
    const slugs = [...root.querySelectorAll(".ms-list input:checked")].map((i) => i.value);
    if (!slugs.length) return close();
    ev.target.disabled = true;
    try {
      const r = await api("/api/path/solved", { method: "POST", json: { profile_id: S.pid, slugs } });
      close(); toast(`Marked ${r.marked.length} solved · +${r.xp} XP`, 3500); renderPath();
    } catch (e) { toast(e.message, 5000); ev.target.disabled = false; }
  });
}

function trailStop(n, si, ni) {  // (the NeetCode lane)
  const off = `--off:${WIND[ni % WIND.length]}px`, data = `data-node="${si}:${ni}"`;
  if (n.kind === "chest") return `<div class="stop" style="${off}"><button class="node chest ${n.open ? "done" : ""}" ${data} title="${esc(n.title)}">${n.open ? "🎉" : "🎁"}</button>
    <div class="node-label"><b>${esc(n.title)}</b><span class="muted small">${n.open ? `+${n.xp} XP` : `${n.left} to go · +${n.xp} XP`}</span></div></div>`;
  if (n.kind === "test") return `<div class="stop" style="${off}"><button class="node test ${n.passed ? "done" : n.running ? "running" : n.ready ? "ready" : ""}" ${data} title="${esc(n.title)}">${n.passed ? "🏅" : n.running ? "⏳" : "📝"}</button>
    <div class="node-label"><b>${esc(n.title)}</b><span class="muted small">${n.passed ? `Passed · +${n.xp} XP` : n.running ? "Running now" : n.retake_at ? `Retake ${new Date(n.retake_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` : `${n.size} problems · +${n.xp} XP`}</span></div></div>`;
  if (n.kind === "work") return `<div class="stop" style="${off}"><button class="node work ${n.done ? "done" : ""}" ${data} title="${esc(n.title)}">${n.done ? "✓" : n.icon}</button>
    <div class="node-label"><b>${esc(n.title)}</b>${n.need > 1 ? `<span class="muted small">${n.have}/${n.need}</span>` : ""}</div></div>`;
  const crowns = n.crowns > 1 ? "👑".repeat(n.crowns - 1) : "";
  return `<div class="stop" style="${off}">
    ${n.state === "here" ? `<div class="node-jarvis">${jarvisSvg("hi", 54, { label: "Jarvis: you are here" })}</div>` : ""}
    <button class="node prob lvl-${n.level.toLowerCase()} ${n.state === "solved" ? "done" : n.state} ${n.due ? "due" : ""}" ${data} title="${esc(n.title)} (${n.level})">${n.state === "solved" ? "✓" : n.due ? "⏰" : n.state === "here" ? "▶" : esc(n.level[0])}</button>
    <div class="node-label"><b>${esc(n.title)}</b>${crowns ? `<span class="small">${crowns}</span>` : n.state === "solved" ? "" : `<span class="muted small">+${n.xp} XP</span>`}</div>
  </div>`;
}

// tapping a stop: a problem opens its card (Start, Log a try), a real-work stop goes where the work is
function openStop(n) {
  if (n.kind === "chest") return toast(n.open ? `${n.title}: opened, +${n.xp} XP` : `${n.left} more to open the ${n.title} (+${n.xp} XP)`, 3500);
  if (n.kind === "work") return n.done ? toast(`Done: ${n.title}`) : stepAction(n);
  if (n.kind === "test") return openTestCard(n);
  const root = $("#modal-root");
  const next = n.crowns >= 3 ? "All three crowns: this one's mastered." : n.crowns ? `Solve it again ${n.crowns === 1 ? "7" : "21"} days after your last crown for another 👑 (+15 XP).` : `First solve: +${n.xp} XP. Couldn't finish? Log it: +5 XP, and it comes back for review.`;
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack" style="max-width:440px">
    <div class="row"><h2 style="margin:0">${esc(n.title)}</h2><span class="spacer"></span><button class="icon-btn" id="st-x" aria-label="Close">✕</button></div>
    <div class="row small"><span class="tag lvl-${n.level.toLowerCase()}">${n.level}</span><span class="muted">${esc(n.topic)}${n.attempts ? ` · ${n.attempts} tr${n.attempts > 1 ? "ies" : "y"}` : ""}</span>${n.crowns > 1 ? `<span>${"👑".repeat(n.crowns - 1)}</span>` : ""}</div>
    <div class="muted small">${esc(next)}</div>
    ${n.premium ? `<a class="small" href="${safeUrl(n.free_url)}" target="_blank" rel="noopener">LeetCode Premium on LeetCode: free on NeetCode ${extIcon}</a>` : ""}
    <div class="row"><button class="btn btn-primary" id="st-start">⏱ Start</button><button class="btn" id="st-log">Log a try</button><a class="btn btn-ghost" href="${safeUrl(n.url)}" target="_blank" rel="noopener">Open ${extIcon}</a></div>
  </div></div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#st-x").addEventListener("click", close);
  $("#st-start").addEventListener("click", () => { startTimer(n.slug, n.url); openLadder(n.topic_id, n.slug); });
  $("#st-log").addEventListener("click", () => openLadder(n.topic_id, n.slug, true));
}

// ---------- tests: real problems, solved on LeetCode against the clock ----------
const testClock = (deadline) => { const s = Math.round(deadline - Date.now() / 1000); return s > 0 ? `${Math.floor(s / 3600) ? `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}` : Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} left` : "Time's up"; };

function closeModal() { $("#modal-root").innerHTML = ""; if (S.tab === "path") renderPath(); }

function openTestCard(n) {
  const root = $("#modal-root");
  if (n.running) return api(`/api/path/test?profile_id=${S.pid}`).then((r) => r.test && openTest(r.test));
  const what = { topic: "from this topic", checkpoint: "from the last few topics", final: "from all 150" }[n.test_kind];
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack" style="max-width:480px">
    <div class="row"><h2 style="margin:0">📝 ${esc(n.title)}</h2><span class="spacer"></span><button class="icon-btn" id="tc-x" aria-label="Close">✕</button></div>
    <div>${n.size} problems ${what}, picked for you, on LeetCode. No hints, against the clock: about ${n.about_minutes} minutes, what a real assessment gives you.</div>
    <div class="muted small">Pass: all solved in time, +${n.xp} XP and a 🏅. Miss one and it says what to review, then you can try again after 3 days. Tests never lock anything.${n.passed ? " You've passed this one: taking it again is just for you." : ""}</div>
    ${n.ready || n.passed ? "" : `<div class="small warn-note">You've solved less than half of what this covers. Most people wait, but it's up to you.</div>`}
    <div class="row">${n.retake_at ? `<span class="muted small">You can retake this on ${new Date(n.retake_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}.</span>` : `<button class="btn btn-primary" id="tc-go">Start the clock</button>`}<button class="btn btn-ghost" id="tc-no">Not now</button></div>
  </div></div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#tc-x").addEventListener("click", close);
  $("#tc-no").addEventListener("click", close);
  $("#tc-go")?.addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try { const r = await api("/api/path/test/start", { method: "POST", json: { profile_id: S.pid, kind: n.test_kind, scope: n.scope } }); openTest(r.test); }
    catch (e) { toast(e.message, 5000); ev.target.disabled = false; }
  });
}

// the test itself: the problems, the clock, and how each one went (a linked LeetCode profile confirms what it can)
async function openTest(t) {
  const root = $("#modal-root"), key = `cc-test:${t.id}`;
  let draft = {};
  try { draft = JSON.parse(localStorage.getItem(key) || "{}"); } catch {}
  const save = () => { try { localStorage.setItem(key, JSON.stringify(draft)); } catch {} };
  root.innerHTML = `<div class="modal-bg"><div class="modal card wide stack" style="max-width:640px">
    <div class="row"><h2 style="margin:0">📝 ${{ topic: "Topic test", checkpoint: "Checkpoint", final: "The final" }[t.kind]}</h2><span class="tag test-clock" data-clock="${t.deadline}">${testClock(t.deadline)}</span><span class="spacer"></span><button class="icon-btn" id="tt-x" aria-label="Close">✕</button></div>
    <div class="muted small">Solve each one on LeetCode, then mark how it went. Closing this doesn't stop the clock.</div>
    <div class="stack">${t.problems.map((p) => `<div class="test-row" data-p="${esc(p.slug)}">
      <div class="lr-main"><a href="${safeUrl(p.url)}" target="_blank" rel="noopener">${esc(p.title)} ${extIcon}</a> <span class="tag lvl-${p.level.toLowerCase()}">${p.level}</span>${p.premium ? ` <a class="tag" href="${safeUrl(p.free_url)}" target="_blank" rel="noopener">free on NeetCode</a>` : ""}<span class="tag ok-tag" data-ok="${esc(p.slug)}" hidden>✓ LeetCode confirms</span>
        <div class="muted small">${esc(p.topic)}</div></div>
      <div class="result-pick">${Object.entries(RESULT_LABEL).map(([k, l]) => `<label class="chip"><input type="radio" name="tr-${esc(p.slug)}" value="${k}" ${draft[p.slug] === k ? "checked" : ""}> ${l}</label>`).join("")}</div></div>`).join("")}</div>
    <div class="row"><button class="btn btn-primary" id="tt-done">Finish the test</button><button class="btn btn-ghost" id="tt-stop">Stop without finishing</button></div>
  </div></div>`;
  const stopTick = () => clearInterval(openTest.tick);
  const close = () => { stopTick(); closeModal(); };
  clearInterval(openTest.tick);
  openTest.tick = setInterval(() => root.querySelectorAll("[data-clock]").forEach((e) => { e.textContent = testClock(Number(e.dataset.clock)); }), 1000);
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  $("#tt-x").addEventListener("click", close);
  root.querySelectorAll(".result-pick input").forEach((r) => r.addEventListener("change", () => { draft[r.closest("[data-p]").dataset.p] = r.value; save(); }));
  $("#tt-stop").addEventListener("click", async () => {
    if (!confirm("Stop this test? It counts as not passed, and you can try again after 3 days.")) return;
    try { await api("/api/path/test/give-up", { method: "POST", json: { profile_id: S.pid, test_id: t.id } }); close(); } catch (e) { toast(e.message, 5000); }
  });
  $("#tt-done").addEventListener("click", async () => {
    const missing = t.problems.filter((p) => !draft[p.slug]);
    if (missing.length) return toast(`Mark how ${esc(missing[0].title)} went first`, 4000);
    $("#tt-done").disabled = true;
    try {
      const r = await api("/api/path/test/finish", { method: "POST", json: { profile_id: S.pid, test_id: t.id, results: Object.fromEntries(t.problems.map((p) => [p.slug, { result: draft[p.slug] }])) } });
      try { localStorage.removeItem(key); } catch {}
      stopTick();
      testResult(t, r);
    } catch (e) { toast(e.message, 5000); $("#tt-done").disabled = false; }
  });
  // a linked profile: what LeetCode already shows as accepted is marked, without waiting for it to open the test
  if (PATH.data?.leetcode_user) api(`/api/path/test?profile_id=${S.pid}&check=true`).then((r) => (r.test?.confirmed || []).forEach((slug) => {
    root.querySelector(`[data-ok="${CSS.escape(slug)}"]`)?.removeAttribute("hidden");
    const pick = root.querySelector(`input[name="tr-${CSS.escape(slug)}"][value="solved"]`);
    if (pick && !draft[slug]) { pick.checked = true; draft[slug] = "solved"; save(); }
  })).catch(() => {});
}

function testResult(t, r) {
  const root = $("#modal-root"), title = (slug) => t.problems.find((p) => p.slug === slug)?.title || slug;
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack" style="max-width:480px">
    <h2 style="margin:0">${r.passed ? `🏅 Passed! +${r.xp} XP` : "Not this time"}</h2>
    ${r.passed ? `<div>Every problem solved, no hints, in time.${r.confirmed_by_leetcode.length ? ` LeetCode confirms ${r.confirmed_by_leetcode.length} of them.` : ""}</div>`
      : `<div>${r.in_time ? "" : "The clock ran out. "}${r.missed.length ? `Review: ${r.missed.map((s) => `<b>${esc(title(s))}</b>`).join(", ")}. They come back on your review list.` : "Everything was solved, just not in time."}</div><div class="muted small">You can retake it in ${r.retake_in_days} days, with different problems where there are enough.</div>`}
    <div class="row"><button class="btn btn-primary" id="tr-ok">Back to the trail</button></div>
  </div></div>`;
  if (r.passed) jarvisConfetti(root.querySelector(".modal"));
  $("#tr-ok").addEventListener("click", closeModal);
}

// ---------- the year, and the link to a public LeetCode profile ----------
function yearGrid(d) {
  const today = new Date(), cells = [];
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (52 * 7 + today.getDay()));
  for (let t = new Date(start); t <= today; t.setDate(t.getDate() + 1)) {
    const key = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`, xp = d.year[key] || 0;
    cells.push(`<i class="y${xp ? (xp >= d.goal * 2 ? 3 : xp >= d.goal ? 2 : 1) : 0}" title="${key}: ${xp} XP"></i>`);
  }
  return `<div class="year-grid" aria-label="XP each day over the last year">${cells.join("")}</div>`;
}

function lcLink(d) {
  return d.leetcode_user
    ? `<span class="small">🔗 Tracking <a href="https://leetcode.com/u/${encodeURIComponent(d.leetcode_user)}/" target="_blank" rel="noopener">${esc(d.leetcode_user)} on LeetCode ${extIcon}</a>: accepted solves on the 150 log themselves.</span> <button class="btn btn-ghost btn-sm" id="lc-off">Unlink</button>`
    : `<span class="small"><b>Solving on LeetCode?</b> Link your public username and new solves count here by themselves. No password.</span> <input type="text" id="lc-name" placeholder="LeetCode username" aria-label="LeetCode username" style="width:170px"> <button class="btn btn-sm" id="lc-go">Link</button>`;
}

function bindLcLink(d) {
  const send = async (username) => {
    try {
      const r = await api("/api/path/leetcode", { method: "POST", json: { profile_id: S.pid, username } });
      toast(r.linked ? (r.new ? `Linked: ${r.new} solve${r.new > 1 ? "s" : ""} logged from LeetCode` : "Linked. New accepted solves will log themselves") : "Unlinked", 4000);
      renderPath();
    } catch (e) { toast(e.message, 6000); }
  };
  $("#lc-go")?.addEventListener("click", () => { const v = $("#lc-name").value.trim(); if (v) send(v); });
  $("#lc-name")?.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.value.trim()) send(e.target.value.trim()); });
  $("#lc-off")?.addEventListener("click", () => send(""));
}

function stepAction(s) {
  const a = s.action || {};
  if (a.kind === "ladder") return openLadder(a.topic);
  if (a.kind === "prepare") return goView("autopilot");
  if (a.kind === "settings") return openSettings(a.section);
  if (a.kind === "go") return goView(a.view);
}

// ---------- the NeetCode 150: tracked here, solved on LeetCode (links only, never the problem itself) ----------
const RESULT_LABEL = { solved: "Solved", hints: "Needed hints", unsolved: "Couldn't" };
const timerKey = (slug) => `cc-timer:${S.pid}:${slug}`;
function startTimer(slug, url) {
  try { if (!localStorage.getItem(timerKey(slug))) localStorage.setItem(timerKey(slug), String(Date.now())); } catch {}
  if (url) window.open(url, "_blank", "noopener");
}
function timerSecs(slug) {
  try { const t = Number(localStorage.getItem(timerKey(slug))); return t ? Math.round((Date.now() - t) / 1000) : 0; } catch { return 0; }
}
function stopTimer(slug) { try { localStorage.removeItem(timerKey(slug)); } catch {} }
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const agoDays = (ts) => { const d = Math.floor((Date.now() / 1000 - ts) / 86400); return d <= 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`; };

async function openLadder(topic, focus, logNow) {
  const root = $("#modal-root");
  const st = { tab: "roadmap", level: "", topic: topic || "" };
  let L, apps = null, tick = null;
  const close = () => { clearInterval(tick); root.innerHTML = ""; if (S.tab === "path") renderPath(); };
  const load = async () => { L = await api(`/api/path/ladder?profile_id=${S.pid}`); };
  const row = (p) => {
    const running = timerSecs(p.slug), due = p.review_at && p.review_at * 1000 <= Date.now();
    const last = p.last ? `last try ${agoDays(p.last.created_at)}: ${RESULT_LABEL[p.last.result]}${p.last.seconds ? ` in ${Math.round(p.last.seconds / 60)} min` : ""}` : "";
    return `<div class="ladder-row ${p.solved_at ? "solved" : due ? "due" : ""}" data-row="${esc(p.slug)}">
      <span>${p.solved_at ? "✅" : due ? "⏰" : "○"}</span>
      <div class="lr-main"><a href="${safeUrl(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a> <span class="tag lvl-${p.level.toLowerCase()}">${p.level}</span>${p.premium ? ` <a class="tag" href="${safeUrl(p.free_url)}" target="_blank" rel="noopener" title="LeetCode Premium on LeetCode">free on NeetCode</a>` : ""}
        <div class="muted small">${esc(p.topic)}${p.attempts ? ` · ${p.attempts} tr${p.attempts > 1 ? "ies" : "y"}` : ""}${p.best_seconds ? ` · best ${Math.max(1, Math.round(p.best_seconds / 60))} min` : ""}${last ? ` · ${esc(last)}` : ""}${p.review_at && !p.solved_at ? ` · review ${due ? "now" : new Date(p.review_at * 1000).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}` : ""}${p.last?.notes ? ` · <span title="${esc(p.last.notes)}">📝</span>` : ""}</div>
        ${p.stars.length ? `<div class="kw" style="margin-top:4px">${p.stars.map((s) => `<span class="tag">★ ${esc(s.label)} <a href="#" data-unstar="${esc(p.slug)}" data-kind="${esc(s.kind)}" data-tag="${esc(s.tag)}" aria-label="Remove">✕</a></span>`).join("")}</div>` : ""}</div>
      <span class="muted small">${p.solved_at ? (p.crowns > 1 ? "👑".repeat(p.crowns - 1) : "") : `+${p.xp}`}</span>
      <div class="lr-acts">${running ? `<span class="timer" data-timer="${esc(p.slug)}">⏱ ${mmss(running)}</span>` : `<button class="btn btn-ghost btn-sm" data-go="${esc(p.slug)}" title="Opens it on LeetCode and starts a timer here">⏱ Start</button>`}
        <button class="btn btn-sm" data-try="${esc(p.slug)}">Log a try</button>
        <button class="icon-btn star-btn ${p.stars.length ? "on" : ""}" data-star="${esc(p.slug)}" title="Keep it on a list: for a job, a kind of role or a company">${p.stars.length ? "★" : "☆"}</button></div>
    </div>`;
  };
  // the roadmap: each topic in NeetCode's order with its problems (optionally one difficulty only)
  const roadmap = () => L.by_topic.map((t) => {
    const ps = L.problems.filter((p) => p.topic_id === t.id && (!st.level || p.level === st.level));
    return ps.length ? `<div class="ladder-topic" data-topic="${esc(t.id)}"><div class="row"><b>${esc(t.name)}</b><span class="muted small">${t.solved}/${t.total}</span>
      <span class="lt-bar"><span style="width:${Math.round((100 * t.solved) / t.total)}%"></span></span></div>${ps.map(row).join("")}</div>` : "";
  }).join("");
  const draw = () => {
    const by = L.by_level;
    root.innerHTML = `<div class="modal-bg"><div class="modal card wide stack" style="max-width:780px">
      <div class="row"><h2>🧠 NeetCode 150</h2><span class="tag">${L.solved}/${L.total}</span><span class="spacer"></span><button class="icon-btn" id="ld-close" aria-label="Close">✕</button></div>
      <div class="muted small">The 150 coding problems most interviews draw from, in 18 topics. Solve them on LeetCode, then log each try here. Ones you miss come back for another go a few days later.</div>
      <div class="ladder-tabs"><button class="chip ${st.tab === "roadmap" ? "on" : ""}" data-tab="roadmap">Roadmap <span class="n">${L.solved}/${L.total}</span></button>
        <button class="chip ${st.tab === "review" ? "on" : ""}" data-tab="review">⏰ Review <span class="n">${L.due.length + L.sharpen.length}</span></button>
        <button class="chip ${st.tab === "lists" ? "on" : ""}" data-tab="lists">★ Lists <span class="n">${L.lists.length}</span></button>
        ${st.tab === "roadmap" ? `<span class="spacer"></span>${[["", "All"], ["Easy", "Easy"], ["Medium", "Medium"], ["Hard", "Hard"]].map(([v, l]) => `<button class="chip ${st.level === v ? "on" : ""}" data-level="${v}">${l}${v ? ` <span class="n">${by[v].solved}/${by[v].total}</span>` : ""}</button>`).join("")}` : ""}</div>
      <div id="ld-panel"></div>
      <div class="ladder-list">${st.tab === "roadmap" ? roadmap() : st.tab === "review" ? (L.due.length || L.sharpen.length ? `${L.due.map(row).join("")}${L.sharpen.length ? `<div class="muted small" style="margin-top:6px"><b>👑 Ready for another crown</b> (optional): solved a while ago. Solve one again to prove it stuck.</div>${L.sharpen.map(row).join("")}` : ""}` : `<div class="empty">Nothing to review right now.</div>`)
        : (L.lists.length ? L.lists.map((x) => `<div class="card card-pad stack" style="box-shadow:none"><b>★ ${esc(x.label)}</b><span class="muted small">${{ job: "For a job", track: "For a kind of role", company: "For a company" }[x.kind]} · ${x.slugs.length} problem${x.slugs.length > 1 ? "s" : ""}</span>${x.slugs.map((sl) => row(L.problems.find((p) => p.slug === sl))).join("")}</div>`).join("")
          : `<div class="empty">Star a problem (☆) to keep it on a list for a job, a kind of role (Quant, Big Tech...) or a company.</div>`)}</div>
    </div></div>`;
    root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
    $("#ld-close").addEventListener("click", close);
    root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { st.tab = b.dataset.tab; draw(); }));
    root.querySelectorAll("[data-level]").forEach((b) => b.addEventListener("click", () => { st.level = b.dataset.level; draw(); }));
    root.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => { const p = L.problems.find((x) => x.slug === b.dataset.go); startTimer(p.slug, p.url); draw(); }));
    root.querySelectorAll("[data-try]").forEach((b) => b.addEventListener("click", () => logTry(b.dataset.try)));
    root.querySelectorAll("[data-star]").forEach((b) => b.addEventListener("click", () => starPanel(b.dataset.star)));
    root.querySelectorAll("[data-unstar]").forEach((a) => a.addEventListener("click", async (e) => {
      e.preventDefault();
      await api("/api/path/star", { method: "POST", json: { profile_id: S.pid, slug: a.dataset.unstar, kind: a.dataset.kind, tag: a.dataset.tag, on: false } });
      await load(); draw();
    }));
    clearInterval(tick);
    tick = setInterval(() => root.querySelectorAll("[data-timer]").forEach((t) => { t.textContent = `⏱ ${mmss(timerSecs(t.dataset.timer))}`; }), 1000);
    if (focus) { root.querySelector(`[data-row="${CSS.escape(focus)}"]`)?.scrollIntoView({ block: "center" }); focus = null; }
    else if (st.topic) { root.querySelector(`[data-topic="${CSS.escape(st.topic)}"]`)?.scrollIntoView({ block: "start" }); st.topic = ""; }
  };
  const panel = (html) => { $("#ld-panel").innerHTML = `<div class="card card-pad stack" style="box-shadow:none">${html}</div>`; };
  const logTry = (slug) => {
    const p = L.problems.find((x) => x.slug === slug), secs = timerSecs(slug) || (p.last?.seconds ?? 0);
    panel(`<b>Log a try: ${esc(p.title)}</b>
      <div class="result-pick">${Object.entries(RESULT_LABEL).map(([k, l], i) => `<label class="chip"><input type="radio" name="ld-res" value="${k}" ${i === 0 ? "checked" : ""}> ${l}</label>`).join("")}</div>
      <label class="row small">Time <input type="number" id="ld-min" min="0" max="360" style="width:90px" value="${secs ? Math.max(1, Math.round(secs / 60)) : ""}"> minutes ${timerSecs(slug) ? '<span class="muted">(from your timer)</span>' : ""}</label>
      <textarea id="ld-notes" rows="2" placeholder="Notes for next time: the trick, where you got stuck"></textarea>
      <div class="row"><button class="btn btn-primary btn-sm" id="ld-save">Save</button><button class="btn btn-ghost btn-sm" id="ld-cancel">Cancel</button></div>`);
    $("#ld-cancel").addEventListener("click", () => ($("#ld-panel").innerHTML = ""));
    $("#ld-save").addEventListener("click", async (ev) => {
      const result = root.querySelector('input[name="ld-res"]:checked').value;
      ev.target.disabled = true;  // (on a slow connection a second tap would log it twice)
      ev.target.textContent = "Saving…";
      try {
        const r = await api("/api/path/attempt", { method: "POST", json: { profile_id: S.pid, slug, result, seconds: Math.round(Number($("#ld-min").value || 0) * 60), notes: $("#ld-notes").value } });
        stopTimer(slug);
        toast(r.first_solve ? `Solved! +${r.xp} XP` : r.xp ? `Logged: +${r.xp} XP. It comes back for review` : "Logged", 4000);
        if (r.first_solve) jarvisConfetti($(".modal"));
        await load(); draw(); loadPath();
      } catch (e) { toast(e.message, 5000); ev.target.disabled = false; ev.target.textContent = "Save"; }
    });
    $("#ld-panel").scrollIntoView({ block: "nearest" });
  };
  const starPanel = async (slug) => {
    const p = L.problems.find((x) => x.slug === slug);
    if (!apps) apps = (await api(`/api/applications?profile_id=${S.pid}`).catch(() => ({ applications: [] }))).applications || [];
    const jobs = apps.filter((a) => !a.job_id.startsWith("hack:")).sort((a, b) => ({ interviewing: 0, oa: 1, applied: 2 }[a.status] ?? 3) - ({ interviewing: 0, oa: 1, applied: 2 }[b.status] ?? 3));
    const tracks = ["Software", "Quant", "AI / ML", "Data", "Hardware", "Big Tech", "Startups", "Fintech"];
    panel(`<b>☆ Keep ${esc(p.title)} on a list</b>
      ${jobs.length ? `<label class="row small">For a job <select id="st-job" style="flex:1"><option value="">Choose…</option>${jobs.map((a) => `<option value="${esc(a.job_id)}">${esc(a.company)} · ${esc(a.title || "Role")}</option>`).join("")}</select></label>` : ""}
      <div class="small">For a kind of role or industry: ${tracks.map((t) => `<button class="chip" data-st-track="${esc(t)}">${esc(t)}</button>`).join("")}</div>
      <label class="row small">For a company <input type="text" id="st-co" placeholder="e.g. Jane Street" style="flex:1"><button class="btn btn-sm" id="st-co-go">Add</button></label>
      <div class="row"><button class="btn btn-ghost btn-sm" id="st-cancel">Done</button></div>`);
    const save = async (kind, tag) => {
      if (!tag) return;
      try { const r = await api("/api/path/star", { method: "POST", json: { profile_id: S.pid, slug, kind, tag } }); toast(`★ For ${r.for}`); await load(); draw(); }
      catch (e) { toast(e.message, 5000); }
    };
    $("#st-job")?.addEventListener("change", (e) => save("job", e.target.value));
    root.querySelectorAll("[data-st-track]").forEach((b) => b.addEventListener("click", () => save("track", b.dataset.stTrack)));
    $("#st-co-go").addEventListener("click", () => save("company", $("#st-co").value.trim()));
    $("#st-cancel").addEventListener("click", () => ($("#ld-panel").innerHTML = ""));
  };
  const logSlug = logNow ? focus : null;  // draw() clears focus once it has scrolled to the problem
  await load();
  draw();
  if (logSlug) logTry(logSlug);
}

// ---------- milestones: Jarvis jumps ----------
// Everything new shows in ONE card, once: several milestones reached together (an inbox check that found an offer
// and an interview, say) are listed in it, and one tap closes it. Each is remembered in this browser too, so a card
// never plays twice even if telling the app failed.
const shownKey = () => `cc-ms-shown:${S.pid}`;
function shownMilestones() { try { return new Set(JSON.parse(localStorage.getItem(shownKey()) || "[]")); } catch { return new Set(); } }
function rememberShown(keys) {
  try { localStorage.setItem(shownKey(), JSON.stringify([...new Set([...shownMilestones(), ...keys])].slice(-200))); } catch {}
}
async function tellSeen(keys, tries = 3) {
  for (let i = 0; i < tries; i++) {
    try { await api("/api/path/seen", { method: "POST", json: { profile_id: S.pid, keys } }); return; }
    catch { await new Promise((r) => setTimeout(r, 1500 * (i + 1))); }
  }
}
function celebrate(list) {
  const done = shownMilestones();
  list = list.filter((x) => x.local || !done.has(x.key));
  if (!list.length || $(".milestone-bg")) return;
  const keys = list.filter((x) => !x.local).map((x) => x.key);
  if (keys.length) { rememberShown(keys); tellSeen(keys); }
  const el = document.createElement("div");
  el.className = "modal-bg milestone-bg";
  const one = list.length === 1;
  el.innerHTML = `<div class="milestone card">
      <div class="milestone-jarvis jump-loop">${jarvisSvg("cheer", 150, { label: "Jarvis celebrating" })}</div>
      <div class="muted small">${list.some((m) => m.local) && one ? "Today" : one ? "Milestone" : `${list.length} milestones`}</div>
      ${one ? `<h2>${esc(list[0].title)}</h2>` : `<ul class="ms-won">${list.map((m) => `<li>${esc(m.title)}</li>`).join("")}</ul>`}
      <button class="btn btn-lg" id="ms-ok">Keep going →</button></div>`;
  document.body.appendChild(el);
  jarvisConfetti(el.querySelector(".milestone"));
  const close = () => el.remove();
  el.querySelector("#ms-ok").addEventListener("click", close);
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
}
