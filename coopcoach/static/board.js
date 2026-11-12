/* Drag and drop for boards (the Applications kanban, and the calendar's Yours | Events): Pointer Events, so it works
   with a mouse, a trackpad and touch, shows a real ghost under the finger and can snap. No library.
   dragCards(root, { item, target, onDrop }) — item: selector of draggable things; target: selector of drop zones;
   onDrop(el, zone, point) returns a promise; a rejected one puts the card back. settleTo(el, zone, point), optional, gives the
   rect the card lands in; without it the ghost glides onto the card where the drop put it. Loaded before app.js. */

// Where a fixed-position ghost's (0, 0) really is on screen: a transformed or filtered ancestor (the page body in some
// themes) makes position: fixed relative to it, which would put the ghost off by the scroll.
function ghostBase(ghost) {
  const t = ghost.style.transform;
  ghost.style.transform = "none";
  const r = ghost.getBoundingClientRect();
  ghost.style.transform = t;
  return { x: r.left, y: r.top };
}

function dragCards(root, { item, target, onDrop, onMove, settleTo, tilt = true, handle = null, threshold = 5 }) {
  let st = null;
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const zoneAt = (x, y) => {
    st.ghost.style.visibility = "hidden";
    const z = document.elementFromPoint(x, y)?.closest(target);
    st.ghost.style.visibility = "";
    return z && root.contains(z) ? z : null;
  };
  const down = (e) => {
    if (e.button !== 0) return;
    const el = e.target.closest(item);
    if (!el || !root.contains(el)) return;
    const control = e.target.closest("a, button, input, select, textarea, label, summary");
    if (control && control !== el) return;  // a link or button inside a card still works; a card that is a button drags
    if (handle && !e.target.closest(handle)) return;
    st = { el, x0: e.clientX, y0: e.clientY, started: false, id: e.pointerId, zone: null };
  };
  const move = (e) => {
    if (!st || e.pointerId !== st.id) return;
    const dx = e.clientX - st.x0, dy = e.clientY - st.y0;
    if (!st.started) {
      if (Math.hypot(dx, dy) < threshold) return;
      st.started = true;
      const r = st.el.getBoundingClientRect();
      st.off = { x: st.x0 - r.left, y: st.y0 - r.top };
      st.ghost = st.el.cloneNode(true);
      st.ghost.classList.add("drag-ghost");
      st.ghost.style.transformOrigin = "0 0";
      Object.assign(st.ghost.style, { width: `${r.width}px`, left: "0px", top: "0px" });
      document.body.appendChild(st.ghost);
      st.base = ghostBase(st.ghost);
      st.el.classList.add("drag-source");
      document.body.classList.add("dragging");
      try { st.el.setPointerCapture(e.pointerId); } catch { /* the element moved */ }
    }
    e.preventDefault();
    st.ghost.style.transform = `translate(${e.clientX - st.off.x - st.base.x}px, ${e.clientY - st.off.y - st.base.y}px) rotate(${calm || !tilt ? 0 : Math.max(-3, Math.min(3, dx / 60))}deg)`;
    const z = zoneAt(e.clientX, e.clientY);
    if (z !== st.zone) { st.zone?.classList.remove("drop-on"); z?.classList.add("drop-on"); st.zone = z; }
    onMove?.(st.el, z, { x: e.clientX, y: e.clientY }, st.ghost);
    // near the edge of a scrolling board, scroll it
    const sc = st.el.closest(".kanban");
    if (sc) { const b = sc.getBoundingClientRect(); if (e.clientX > b.right - 40) sc.scrollLeft += 14; else if (e.clientX < b.left + 40) sc.scrollLeft -= 14; }
  };
  const up = async (e) => {
    if (!st || e.pointerId !== st.id) return;
    const s = st;
    st = null;
    if (!s.started) return;
    // the browser sends a click when the finger lifts: that's the end of the drag, not a tap
    const swallow = (c) => { c.stopPropagation(); c.preventDefault(); };
    document.addEventListener("click", swallow, { capture: true, once: true });
    setTimeout(() => document.removeEventListener("click", swallow, { capture: true }), 0);
    document.body.classList.remove("dragging");
    s.zone?.classList.remove("drop-on");
    const point = { x: e.clientX, y: e.clientY };
    const finish = () => { s.ghost.remove(); s.el.classList.remove("drag-source"); };
    if (!s.zone) return glideHome(s, finish);
    // where it will land (a board's own slot, or the card itself once it has moved), read before the drop changes the page
    let to = settleTo ? settleTo(s.el, s.zone, point) : null;  // false: no glide (the board redraws itself)
    let failed = false;
    const done = Promise.resolve().then(() => onDrop(s.el, s.zone, point)).catch((err) => { failed = true; if (err?.message) toast(err.message, 5000); });
    await new Promise((r) => requestAnimationFrame(r));  // a board that moves the card right away has moved it by now
    if (to == null && s.el.isConnected) to = s.el.getBoundingClientRect();
    if (to && !calm && !failed) await settle(s.ghost, to, s.base);
    await done;
    finish();
  };
  // the ghost eases into its new place: no jump at the end of a drag
  const settle = (ghost, to, base) => {
    const g = ghost.getBoundingClientRect();
    const from = ghost.style.transform;
    const anim = ghost.animate([{ transform: from }, { transform: `translate(${to.left - base.x}px, ${to.top - base.y}px) rotate(0deg) scale(${(to.width || g.width) / g.width})` }],
      { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
    return anim.finished.catch(() => {});
  };
  // dropped nowhere: it floats back to where it came from
  const glideHome = (s, finish) => {
    if (calm) return finish();
    settle(s.ghost, s.el.getBoundingClientRect(), s.base).then(finish);
  };
  root.addEventListener("pointerdown", down);
  root.addEventListener("pointermove", move, { passive: false });
  root.addEventListener("pointerup", up);
  root.addEventListener("pointercancel", up);
}

// ---------- the Applications kanban ----------
const KANBAN = [["saved", "Saved"], ["applied", "Applied"], ["oa", "OA"], ["interviewing", "Interview"], ["offer", "Offer"], ["rejected", "Closed"]];

function kanbanHtml(apps, now) {
  const by = Object.fromEntries(KANBAN.map(([k]) => [k, []]));
  apps.forEach((a) => by[a.status]?.push(a));
  const total = apps.length || 1;
  const card = (a) => {
    const since = a.applied_at || a.updated_at;
    const days = since ? Math.max(0, Math.floor((now - since) / 86400)) : 0;
    const quiet = a.status === "applied" && days >= 21;
    return `<div class="kcard ${quiet ? "aging" : ""}" data-job="${esc(a.job_id)}" data-status="${esc(a.status)}" tabindex="0" role="listitem"
        aria-label="${esc(a.company)}, ${esc(a.title || "Role")}. Arrow keys move it between stages.">
      <div class="kcard-co">${esc(a.company)}${a.source === "email" ? ' <span class="kcard-mail" title="Found in your email">✉</span>' : ""}</div>
      <div class="kcard-role">${a.url ? `<a href="${safeUrl(a.url)}" target="_blank" rel="noopener">${esc(a.title || "Role")}</a>` : esc(a.title || "Role")}</div>
      <div class="kcard-meta"><span>${days}d</span>${a.last_email_at ? `<span title="${esc(a.last_email_subject || "")}">✉ ${esc(ago(a.last_email_at))}</span>` : ""}${quiet ? '<span class="kq">follow up?</span>' : ""}</div>
    </div>`;
  };
  return `<div class="kstage" aria-hidden="true">${KANBAN.map(([k]) => `<i class="ks-${k}" style="flex:${by[k].length / total || 0.0001}"></i>`).join("")}</div>
    <div class="kanban" role="list">${KANBAN.map(([k, label]) => `<section class="kcol kcol-${k}" data-col="${k}" aria-label="${label}">
      <header><span class="tel">${label}</span><span class="kcount">${by[k].length}</span></header>
      <div class="kcards">${by[k].map(card).join("") || '<div class="kempty">drop here</div>'}</div></section>`).join("")}</div>`;
}

function bindKanban(root, { move, pid = null, apps = null }) {
  const order = KANBAN.map(([k]) => k);
  const place = (el, status, spring = true) => {
    const cards = [...root.querySelectorAll(".kcard")].filter((c) => c !== el);
    const before = new Map(cards.map((c) => [c, c.getBoundingClientRect()]));
    const col = root.querySelector(`.kcol[data-col="${status}"] .kcards`);
    col.querySelector(".kempty")?.remove();
    col.prepend(el);
    el.dataset.status = status;
    root.querySelectorAll(".kcol").forEach((c) => {
      const n = c.querySelectorAll(".kcard").length;
      c.querySelector(".kcount").textContent = n;
      if (!n && !c.querySelector(".kempty")) c.querySelector(".kcards").innerHTML = '<div class="kempty">drop here</div>';
    });
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    // the other cards slide to their new places instead of jumping
    before.forEach((r, c) => {
      const n = c.getBoundingClientRect(), dx = r.left - n.left, dy = r.top - n.top;
      if (dx || dy) c.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 300, easing: "cubic-bezier(.2,.8,.2,1)" });
    });
    if (spring) { el.classList.remove("landed"); void el.offsetWidth; el.classList.add("landed"); }
  };
  const go = async (el, status, dragged = false) => {
    const was = el.dataset.status;
    if (status === was) return;
    place(el, status, !dragged);
    try { await move(el.dataset.job, status); }
    catch (e) { place(el, was); throw e; }
    if (status === "interviewing" || status === "offer") {
      jarvisConfetti(el.closest(".kcol"));
      jarvisSay(status === "offer" ? "an offer! huge." : "interview! let's prep.", { mood: "cheer" });
      setTimeout(() => jarvisSay(), 4000);
    }
  };
  dragCards(root, { item: ".kcard", target: ".kcol", onDrop: (el, zone) => go(el, zone.dataset.col, true) });
  if (pid != null && apps) replayInboxMoves(root, apps, pid, place);
  root.addEventListener("keydown", (e) => {
    const el = e.target.closest(".kcard");
    if (!el || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    e.preventDefault();
    const i = order.indexOf(el.dataset.status) + (e.key === "ArrowRight" ? 1 : -1);
    if (i >= 0 && i < order.length) go(el, order[i]).then(() => el.focus()).catch((err) => toast(err.message, 5000));
  });
}

// When an inbox check moved an application since you last looked (an interview invite, an offer), Jarvis carries the card
// across while you watch, instead of it just being somewhere else. What you last saw is remembered in this browser only.
const LABEL_OF = Object.fromEntries(KANBAN);
function replayInboxMoves(root, apps, pid, place) {
  const key = `cc-board:${pid}`;
  let seen = null;
  try { seen = JSON.parse(localStorage.getItem(key) || "null"); } catch { /* private window */ }
  try { localStorage.setItem(key, JSON.stringify({ at: Date.now() / 1000, st: Object.fromEntries(apps.map((a) => [a.job_id, a.status])) })); } catch { /* fine */ }
  if (!seen?.st || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const order = KANBAN.map(([k]) => k);
  const moved = apps.filter((a) => {
    const was = seen.st[a.job_id];
    return was && was !== a.status && order.includes(was) && a.last_email_at && (a.updated_at || 0) >= seen.at - 5;  // an email moved it, after you last looked
  }).slice(0, 3);
  if (!moved.length) return;
  const els = moved.map((a) => [a, root.querySelector(`.kcard[data-job="${CSS.escape(a.job_id)}"]`)]).filter(([, el]) => el);
  els.forEach(([a, el]) => place(el, seen.st[a.job_id], false));  // start where you last saw it
  const carry = async ([a, el]) => {
    const r0 = el.getBoundingClientRect();
    const ghost = el.cloneNode(true);
    ghost.classList.add("drag-ghost", "jarvis-carry");
    Object.assign(ghost.style, { width: `${r0.width}px`, left: "0px", top: "0px", transformOrigin: "0 0" });
    document.body.appendChild(ghost);
    const b = ghostBase(ghost);
    const at = (r, dy = 0, rot = 0, k = 1) => `translate(${r.left - b.x}px, ${r.top + dy - b.y}px) rotate(${rot}deg) scale(${k})`;
    ghost.style.transform = at(r0);
    el.style.visibility = "hidden";
    jarvisSay(`${a.company.toLowerCase()} → ${LABEL_OF[a.status].toLowerCase()}. moved it.`, { mood: "cheer" });
    await ghost.animate([{ transform: at(r0) }, { transform: at(r0, -12, -2, 1.04) }], { duration: 320, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" }).finished;
    place(el, a.status, false);  // the cards around make room while it travels
    const r1 = el.getBoundingClientRect();
    const mid = { left: (r0.left + r1.left) / 2, top: Math.min(r0.top, r1.top) - 46 };
    await ghost.animate([{ transform: at(r0, -12, -2, 1.04) }, { transform: at(mid, 0, 1.5, 1.05), offset: 0.5 }, { transform: at(r1, -8, 0, 1.03), offset: 0.88 }, { transform: at(r1) }],
      { duration: 1100, easing: "cubic-bezier(.45,0,.25,1)", fill: "forwards" }).finished;
    ghost.remove();
    el.style.visibility = "";
    el.classList.remove("landed"); void el.offsetWidth; el.classList.add("landed");
    if (a.status === "interviewing" || a.status === "offer") jarvisConfetti(el.closest(".kcol"));
  };
  setTimeout(async () => {
    for (const x of els) { if (x[1].isConnected) await carry(x); }
    setTimeout(() => jarvisSay(), 4000);
  }, 650);
}
