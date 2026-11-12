/* Calendar → Board: two week grids side by side on one time axis. Yours (interviews, assessments and coffee chats from
   your inbox, events you saved, your own blocks) and Events (everything pulled in, with the filters). Drag an event
   onto Yours, or tap it, to add it; tap an empty spot on Yours to add your own block. Drag uses board.js. */

const CAL_H = 44;               // pixels per hour
const CAL_FROM = 7, CAL_TO = 23; // the hours shown
const CAL_DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function calWeekStart(offset = 0) {  // the next seven days from today, so there's always a week ahead to plan
  const d = new Date(); d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + offset * 7);
  return d;
}
const calHM = (ts) => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const calDayIdx = (ts, start) => {  // by calendar date, so a clock change in the week can't shift a day
  const d = new Date(ts * 1000); d.setHours(12, 0, 0, 0);
  const s = new Date(start); s.setHours(12, 0, 0, 0);
  return Math.round((d - s) / 864e5);
};
const calDate = (start, i) => { const d = new Date(start); d.setDate(d.getDate() + i); return d; };

// blocks in one day column, side by side where they overlap (up to three lanes)
function calLayout(items) {
  const lanes = [];
  const out = items.map((e) => {
    const s = e.start_ts, en = e.end_ts && e.end_ts > s ? e.end_ts : s + (e.due ? 1800 : 3600);
    let lane = lanes.findIndex((end) => end <= s);
    if (lane < 0) { lane = lanes.length; lanes.push(en); } else lanes[lane] = en;
    return { e, s, en, lane };
  });
  const n = Math.min(3, lanes.length || 1);
  return out.filter((x) => x.lane < 3).map((x) => ({ ...x, n }));
}

function calBlock(x, start, side) {
  const d = new Date(x.s * 1000), hrs = d.getHours() + d.getMinutes() / 60;
  const last = (CAL_TO - CAL_FROM) * CAL_H - 24;  // very early or late ones stay on the grid, at its edge
  const top = Math.min(last, Math.max(0, (hrs - CAL_FROM) * CAL_H)), h = Math.max(22, Math.min(((x.en - x.s) / 3600) * CAL_H - 2, last + 24 - top));
  const e = x.e, own = side === "yours";
  const kind = e.yours ? (e.kind === "block" ? "own" : "inbox") : "event";
  return `<button type="button" class="cb ${own ? "cb-yours" : "cb-pub"} cb-${kind} ${own && !e.yours ? "cb-saved" : ""}" data-cal="${esc(e.id)}"
      style="top:${top}px;height:${h}px;left:calc(${(100 / x.n) * x.lane}% + 2px);width:calc(${100 / x.n}% - 4px)"
      title="${esc(e.title)} · ${esc(calHM(x.s))}">
      <span class="cb-t">${esc(e.due ? `Due: ${e.title}` : e.title)}</span><span class="cb-time">${esc(calHM(x.s))}</span></button>`;
}

function calPane(side, items, start, label, note) {
  const days = Array.from({ length: 7 }, (_, i) => calDate(start, i));
  const allDay = days.map((d, i) => items.filter((e) => e.start_ts && e.all_day && calDayIdx(e.start_ts, start) === i));
  const anyAll = allDay.some((x) => x.length);
  const today = new Date().toDateString();
  const cols = days.map((d, i) => {
    const mine = items.filter((e) => e.start_ts && calDayIdx(e.start_ts, start) === i && !e.all_day);
    return `<div class="cday ${d.toDateString() === today ? "today" : ""}" data-day="${i}">${calLayout(mine.sort((a, b) => a.start_ts - b.start_ts)).map((x) => calBlock(x, start, side)).join("")}</div>`;
  }).join("");
  return `<section class="cpane cpane-${side}" data-side="${side}" aria-label="${esc(label)}">
    <header class="cpane-h"><h3>${esc(label)}</h3><span class="tel">${esc(note)}</span></header>
    <div class="cdays">${days.map((d) => `<div class="cdh ${d.toDateString() === today ? "today" : ""}"><span>${CAL_DAY[(d.getDay() + 6) % 7]}</span><b>${d.getDate()}</b></div>`).join("")}</div>
    ${anyAll ? `<div class="callday">${allDay.map((list) => `<div>${list.slice(0, 3).map((e) => `<button type="button" class="cb cb-all ${side === "yours" ? "cb-yours" : "cb-pub"} ${e.yours ? "cb-inbox" : "cb-event"} ${side === "yours" && !e.yours ? "cb-saved" : ""}" data-cal="${esc(e.id)}" title="${esc(e.title)}"><span class="cb-t">${esc(e.title)}</span></button>`).join("")}${list.length > 3 ? `<span class="tel">+${list.length - 3}</span>` : ""}</div>`).join("")}</div>` : ""}
    <div class="cgrid" style="height:${(CAL_TO - CAL_FROM) * CAL_H}px">${cols}</div></section>`;
}

// ctx: { all, mine, filtered, st, rerender }
function drawCalBoard(ctx) {
  const { st } = ctx;
  const start = calWeekStart(st.week || 0), end = calDate(start, 7);
  const inWeek = (e) => e.start_ts && e.start_ts * 1000 >= start.getTime() && e.start_ts * 1000 < end.getTime();
  const yours = [...ctx.mine, ...ctx.all.filter((e) => e.mark)].filter(inWeek);
  const pub = ctx.filtered().filter((e) => !e.mark && inWeek(e));
  const toBook = ctx.mine.filter((e) => e.status === "to_book");
  const range = `${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${calDate(start, 6).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  $("#ev-count").textContent = `${yours.length} yours · ${pub.length} events in these 7 days`;
  const hours = Array.from({ length: CAL_TO - CAL_FROM }, (_, i) => `<span style="top:${i * CAL_H}px">${new Date(2000, 0, 1, CAL_FROM + i).toLocaleTimeString(undefined, { hour: "numeric" })}</span>`).join("");
  $("#ev-list").innerHTML = `
    <div class="cal-top row"><button class="icon-btn" id="cw-prev" aria-label="Previous week">‹</button><b class="cal-range">${esc(range)}</b>
      <button class="icon-btn" id="cw-next" aria-label="Next week">›</button>${st.week ? '<button class="btn btn-ghost btn-sm" id="cw-now">Today</button>' : ""}
      <span class="spacer"></span><span class="muted small">Drag an event onto <b>Yours</b> to add it, or back to <b>Events</b> to remove it. Tap an empty spot on Yours for your own block.</span></div>
    ${toBook.length ? `<div class="cal-due"><span class="tel">Still to book</span>${toBook.map((e) => e.book_url ? `<a class="btn btn-ghost btn-sm" href="${safeUrl(e.book_url)}" target="_blank" rel="noopener">${esc(e.company || e.who || e.title)} ${extIcon}</a>` : `<span class="tag warn">${esc(e.company || e.who || e.title)}</span>`).join("")}</div>` : ""}
    <div class="cal-board">
      <div class="chours" style="height:${(CAL_TO - CAL_FROM) * CAL_H}px">${hours}</div>
      ${calPane("yours", yours, start, "Yours", `${yours.length} planned`)}
      ${calPane("events", pub, start, "Events", `${pub.length} to pick from`)}
    </div>`;
  const shift = (n) => { st.week = n; ctx.rerender(); };
  $("#cw-prev").addEventListener("click", () => shift((st.week || 0) - 1));
  $("#cw-next").addEventListener("click", () => shift((st.week || 0) + 1));
  $("#cw-now")?.addEventListener("click", () => shift(0));
  const board = $("#ev-list").querySelector(".cal-board");
  $("#ev-list .chours").style.marginTop = `${board.querySelector(".cgrid").offsetTop - board.querySelector(".cpane").offsetTop}px`;  // lines up with the grid
  const find = (id) => ctx.all.find((e) => String(e.id) === id) || ctx.mine.find((e) => String(e.id) === id);
  const add = async (e, dragged = false) => {
    await api(`/api/events/${e.id}/mark`, { method: "PUT", json: { profile_id: S.pid, mark: "going" } });
    e.mark = "going";
    const clash = yours.find((y) => y !== e && y.start_ts < (e.end_ts || e.start_ts + 3600) && (y.end_ts || y.start_ts + 3600) > e.start_ts);
    toast(clash ? `Added. Heads up: it overlaps ${clash.title}` : `On your calendar: ${e.title}`);
    ctx.rerender();  // then it lands on Yours with a little spring (a dragged one has already glided into place)
    if (!dragged) requestAnimationFrame(() => $(`.cpane-yours [data-cal="${CSS.escape(String(e.id))}"]`)?.classList.add("landed"));
  };
  // drag an event across: a ghost slot shows where it lands, and clashes show in red
  let slot = null;
  dragCards(board, {
    item: ".cb-pub", target: ".cpane-yours",
    tilt: false,  // small blocks: a level glide reads calmer than a tilted card
    onMove: (el, zone) => {
      slot?.remove(); slot = null;
      if (!zone) return;
      const e = find(el.dataset.cal), i = calDayIdx(e.start_ts, start);
      const col = zone.querySelectorAll(".cday")[i];
      if (!col || e.all_day) return;
      const d = new Date(e.start_ts * 1000), top = (d.getHours() + d.getMinutes() / 60 - CAL_FROM) * CAL_H;
      const en = e.end_ts && e.end_ts > e.start_ts ? e.end_ts : e.start_ts + 3600;
      const clash = yours.some((y) => y.start_ts < en && (y.end_ts || y.start_ts + 3600) > e.start_ts);
      slot = document.createElement("div");
      slot.className = `cslot ${clash ? "clash" : ""}`;
      slot.style.cssText = `top:${Math.max(0, top)}px;height:${Math.max(22, ((en - e.start_ts) / 3600) * CAL_H - 2)}px`;
      slot.innerHTML = `<span class="tel">${esc(CAL_DAY[(calDate(start, i).getDay() + 6) % 7])} ${esc(calHM(e.start_ts))}${clash ? " · clash" : ""}</span>`;
      col.appendChild(slot);
    },
    settleTo: () => slot?.getBoundingClientRect() || false,  // it glides into the slot it showed
    onDrop: async (el) => { slot?.remove(); slot = null; await add(find(el.dataset.cal), true); },
  });
  board.addEventListener("pointerup", () => { slot?.remove(); slot = null; });
  // added by mistake: drag it back (only events you added; inbox items and your own blocks come off from their sheet)
  dragCards(board, {
    item: ".cb-saved", target: ".cpane-events",
    settleTo: () => false,
    onDrop: async (el) => {
      const e = find(el.dataset.cal);
      await api(`/api/events/${e.id}/mark`, { method: "PUT", json: { profile_id: S.pid, mark: null } });
      e.mark = null;
      toast(`Taken off your calendar: ${e.title}`);
      ctx.rerender();
    },
  });
  // taps: a block opens its sheet; an empty spot on Yours adds a block there
  board.addEventListener("click", (ev) => {
    if (document.body.classList.contains("dragging")) return;
    const b = ev.target.closest("[data-cal]");
    if (b) return calSheet(find(b.dataset.cal), { add, rerender: ctx.rerender, mine: ctx.mine });
    const col = ev.target.closest(".cpane-yours .cday");
    if (!col) return;
    const r = col.getBoundingClientRect(), mins = Math.round((((ev.clientY - r.top) / CAL_H) * 60) / 15) * 15;
    const at = calDate(start, Number(col.dataset.day));
    at.setHours(CAL_FROM, 0, 0, 0); at.setMinutes(at.getMinutes() + mins);
    calNewBlock(at, ctx);
  });
}

function calSheet(e, { add, rerender, mine }) {
  if (!e) return;
  const root = $("#modal-root");
  const when = `${new Date(e.start_ts * 1000).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })} · ${calHM(e.start_ts)}${e.end_ts ? `–${calHM(e.end_ts)}` : ""}`;
  const link = e.url || e.join_url || e.book_url || e.email_link;
  root.innerHTML = `<div class="modal-bg"><div class="modal card stack cal-sheet">
    <div class="row"><span class="tel">${esc(e.yours ? (e.label || "Yours") : (e.feed_label || "Event"))}</span><span class="spacer"></span><button class="icon-btn" id="cs-x" aria-label="Close">✕</button></div>
    <h2 style="margin:0">${esc(e.title)}</h2>
    <div class="muted small">${esc(when)}${e.location ? ` · ${esc(e.location)}` : ""}${e.company ? ` · ${esc(e.company)}` : ""}</div>
    ${e.description ? `<div class="small cal-desc">${esc(String(e.description).slice(0, 500))}</div>` : ""}
    <div class="row">
      ${!e.yours && !e.mark ? '<button class="btn" id="cs-add">Add to my calendar</button>' : ""}
      ${!e.yours && e.mark ? '<button class="btn btn-ghost" id="cs-unmark">Remove from my calendar</button>' : ""}
      ${e.yours && e.item_id ? '<button class="btn btn-ghost" id="cs-del">Remove</button>' : ""}
      ${link ? `<a class="btn btn-ghost" href="${safeUrl(link)}" target="_blank" rel="noopener">Open ${extIcon}</a>` : ""}
    </div></div></div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelector(".modal-bg").addEventListener("click", (x) => { if (x.target.classList.contains("modal-bg")) close(); });
  $("#cs-x").addEventListener("click", close);
  $("#cs-add")?.addEventListener("click", async () => { close(); await add(e); });
  $("#cs-unmark")?.addEventListener("click", async () => {
    await api(`/api/events/${e.id}/mark`, { method: "PUT", json: { profile_id: S.pid, mark: null } });
    e.mark = null; close(); toast("Taken off your calendar"); rerender();
  });
  $("#cs-del")?.addEventListener("click", async () => {
    await api(`/api/calendar/${e.item_id}?profile_id=${S.pid}`, { method: "DELETE" });
    const i = mine.indexOf(e); if (i >= 0) mine.splice(i, 1);
    close(); toast("Removed"); rerender();
  });
}

function calNewBlock(at, ctx) {
  const root = $("#modal-root");
  const pad = (n) => String(n).padStart(2, "0");
  const local = `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(at.getHours())}:${pad(at.getMinutes())}`;
  root.innerHTML = `<div class="modal-bg"><form class="modal card stack" id="nb-form">
    <h2 style="margin:0">Add to your calendar</h2>
    <input type="text" id="nb-title" placeholder="What is it? e.g. OA prep, Coffee with Sam" maxlength="200" required autocomplete="off">
    <div class="row"><input type="datetime-local" id="nb-at" value="${local}" step="900" required>
      <select id="nb-len">${[[30, "30 min"], [60, "1 hour"], [90, "1.5 hours"], [120, "2 hours"], [180, "3 hours"]].map(([v, l]) => `<option value="${v}" ${v === 60 ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="row"><span class="spacer"></span><button type="button" class="btn btn-ghost" id="nb-x">Cancel</button><button class="btn">Add</button></div>
  </form></div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelector(".modal-bg").addEventListener("click", (x) => { if (x.target.classList.contains("modal-bg")) close(); });
  $("#nb-x").addEventListener("click", close);
  $("#nb-title").focus();
  $("#nb-form").addEventListener("submit", async (x) => {
    x.preventDefault();
    const s = Math.floor(new Date($("#nb-at").value).getTime() / 1000), len = Number($("#nb-len").value) * 60;
    try {
      const r = await api("/api/calendar", { method: "POST", json: { profile_id: S.pid, title: $("#nb-title").value.trim(), start_ts: s, end_ts: s + len } });
      ctx.mine.push({ id: `item-${r.item_id}`, item_id: r.item_id, kind: "block", label: "Your block", title: $("#nb-title").value.trim(), start_ts: s, end_ts: s + len, yours: true });
      close(); toast("Added to your calendar"); ctx.rerender();
    } catch (err) { toast(err.message, 5000); }
  });
}
