/* Jarvis, Co-op Coach's assistant. The basics come from gkhurana's site (a little terminal window: title bar with three
   dots, block eyes, a curved mouth, the same moods); the rest follows this app: rounder corners like its cards, its
   accent colour (so it changes with the light and dark themes, via the --jv-* variables in style.css), a soft glow on
   the face, and a status light that's green when ready and pulses while thinking. One SVG, five moods, used by the
   chat, Today, the Path and celebrations. Plus voice input (the browser's own speech recognition) for any text box.
   Loaded before app.js; plain functions, no state. */

const JARVIS_MOODS = ["hi", "thinking", "talking", "cheer", "oops"];

function jarvisSvg(mood = "hi", size = 120, { head = false, label = "" } = {}) {  // (head: the same face either way)
  const m = JARVIS_MOODS.includes(mood) ? mood : "hi";
  const face = "style=\"stroke:var(--jv-face)\"", fill = "style=\"fill:var(--jv-face)\"";
  const eyeY = m === "thinking" ? 30 : 32.5;
  const eyes = m === "cheer"
    ? `<path d="M22.5 39 L26.5 33 L30.5 39 M43.5 39 L47.5 33 L51.5 39" ${face} stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`
    : m === "oops"
    ? `<g ${face} stroke-width="2.2" stroke-linecap="round"><path d="M23.5 33.5l6 6.5M29.5 33.5l-6 6.5"/><path d="M44.5 33.5l6 6.5M50.5 33.5l-6 6.5"/></g>`
    : `<g class="jarvis-eyes"><rect class="jarvis-eye" x="23" y="${eyeY}" width="7.5" height="9.5" rx="2.6" ${fill}/>`
      + `<rect class="jarvis-eye" x="43.5" y="${eyeY}" width="7.5" height="9.5" rx="2.6" ${fill}/></g>`;
  const mouth = m === "cheer" ? `<path d="M27 48 Q37 57.5 47 48" ${face} stroke-width="2" fill="none" stroke-linecap="round"/>`
    : m === "thinking" ? `<path d="M30.5 51 h9.5" ${face} stroke-width="1.6" stroke-linecap="round" opacity=".55"/><rect class="jarvis-cursor" x="42" y="47.5" width="3.4" height="6.2" rx=".8" ${fill}/>`
    : m === "oops" ? `<ellipse cx="37" cy="51" rx="3.4" ry="4" ${face} stroke-width="1.8" fill="none"/>`
    : m === "talking" ? `<ellipse class="jarvis-jaw" cx="37" cy="50.5" rx="4.6" ry="2.8" ${face} stroke-width="1.8" fill="none"/>`
    : `<path d="M28.5 49 Q37 54.8 45.5 49" ${face} stroke-width="1.9" fill="none" stroke-linecap="round"/>`;
  const cheeks = m === "cheer" ? `<ellipse cx="19" cy="46" rx="3.2" ry="1.8" style="fill:var(--jv-cheek)"/><ellipse cx="55" cy="46" rx="3.2" ry="1.8" style="fill:var(--jv-cheek)"/>` : "";
  const aria = label ? `role="img" aria-label="${esc(label)}"` : `aria-hidden="true"`;
  return `<svg class="jarvis jarvis-${m}" viewBox="0 0 74 74" width="${size}" height="${size}" fill="none" ${aria}>
    <rect x="4" y="8" width="66" height="58" rx="13" style="fill:var(--jv-screen);stroke:var(--jv-line)" stroke-width="1.8"/>
    <path d="M5 20.5 H69" style="stroke:var(--jv-line)" stroke-width="1" opacity=".22"/>
    <circle cx="13" cy="14.4" r="1.9" style="fill:var(--jv-line)"/><circle cx="19.5" cy="14.4" r="1.9" style="fill:var(--jv-line)" opacity=".55"/><circle cx="26" cy="14.4" r="1.9" style="fill:var(--jv-line)" opacity=".28"/>
    <circle class="jarvis-status" cx="61.5" cy="14.4" r="1.9" style="fill:${m === "thinking" || m === "talking" ? "var(--jv-line)" : m === "oops" ? "var(--bad, #f87171)" : "var(--good)"}"/>
    <g class="jarvis-face">${eyes}${mouth}</g>${cheeks}</svg>`;
}

// a burst of confetti over an element (applied, interview, offer): pure CSS, gone after a second and a half
function jarvisConfetti(host) {
  if (!host || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ["#5cf2b0", "#b9ffe0", "#ffb547", "#7cb8ff", "#e9eef0"];  // Phosphor: mint, its glow, signal, ice, paper
  const box = document.createElement("div");
  box.className = "confetti";
  box.setAttribute("aria-hidden", "true");
  box.innerHTML = Array.from({ length: 28 }, (_, i) => `<i style="--x:${Math.round(Math.random() * 100)}%;--d:${(Math.random() * 0.6 + 0.9).toFixed(2)}s;--r:${Math.round(Math.random() * 720 - 360)}deg;background:${colors[i % colors.length]}"></i>`).join("");
  host.appendChild(box);
  setTimeout(() => box.remove(), 1800);
}

// 🎤 talk instead of typing: the browser's own speech recognition (Chrome sends the audio to Google to transcribe,
// Safari to Apple), filling the text box as you speak. Shows nothing where the browser can't do it.
function addVoice(textarea, { label = "Talk instead of typing", lang = "en-US" } = {}) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR || !textarea || textarea.dataset.voice) return null;
  textarea.dataset.voice = "1";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "icon-btn mic-btn";
  btn.title = `${label} (your browser's speech recognition)`;
  btn.setAttribute("aria-label", label);
  btn.innerHTML = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/></svg>`;
  let rec = null, base = "";
  const stop = () => { rec?.stop(); };
  btn.addEventListener("click", () => {
    if (rec) return stop();
    rec = new SR();
    rec.lang = lang;
    rec.interimResults = true;
    rec.continuous = true;
    base = textarea.value ? textarea.value.replace(/\s*$/, " ") : "";
    rec.onresult = (e) => {
      let said = "";
      for (let i = 0; i < e.results.length; i++) said += e.results[i][0].transcript;
      textarea.value = base + said;
      textarea.dispatchEvent(new Event("input"));
    };
    rec.onerror = (e) => { if (e.error === "not-allowed") toast("Allow the microphone for this page to talk to Jarvis", 5000); };
    rec.onend = () => { rec = null; btn.classList.remove("on"); btn.setAttribute("aria-pressed", "false"); };
    rec.start();
    btn.classList.add("on");
    btn.setAttribute("aria-pressed", "true");
  });
  textarea.insertAdjacentElement("afterend", btn);
  return btn;
}

// ---------- the dock: Jarvis bottom-left with a status line in his voice; his glow brightens while he works ----------
const JV = { busy: new Set(), line: "" };

function jarvisDockMarkup() {
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  return `${jarvisSvg("hi", 40, { head: true })}<span class="dock-status" id="dock-status">standing by</span>`
    + `<span class="dock-key" aria-hidden="true">${mac ? "⌘" : "Ctrl "}J</span><span class="chat-dot hidden" id="chat-dot"></span>`;
}

// what he says when nothing is running: the day in one line
function jarvisIdleLine() {
  const c = (typeof S !== "undefined" && S.app?.counts) || {};
  const ready = Number(document.getElementById("ready-count")?.textContent || 0);
  const bits = [ready ? `${ready} ready to apply` : "", c.new_24h ? `${Number(c.new_24h).toLocaleString()} new today` : ""].filter(Boolean);
  return bits.join(" · ") || "standing by";
}

// jarvisSay("scanning 312 boards", { key: "sync" }) while a job runs; jarvisSay(null, { key: "sync" }) when it ends
function jarvisSay(text, { key = "", mood } = {}) {
  if (key) text ? JV.busy.add(key) : JV.busy.delete(key);
  const busy = JV.busy.size > 0;
  document.body.classList.toggle("jv-busy", busy);
  const line = text || (busy ? JV.line : jarvisIdleLine());
  JV.line = line;
  const el = document.getElementById("dock-status");
  if (el && el.textContent !== line) el.textContent = line;
  const fab = document.getElementById("chat-fab");
  const want = mood || (busy ? "thinking" : "hi");
  const svg = fab?.querySelector("svg.jarvis");
  if (svg && !svg.classList.contains(`jarvis-${want}`)) svg.outerHTML = jarvisSvg(want, 40, { head: true });
}

// numbers roll up from zero when they appear (data-n on the element), once per element
function jarvisCount(root) {
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  (root || document).querySelectorAll("[data-n]:not([data-counted])").forEach((el) => {
    el.dataset.counted = "1";
    const to = Number(el.dataset.n) || 0;
    if (calm || to <= 1) { el.textContent = to.toLocaleString(); return; }
    const t0 = performance.now(), dur = Math.min(900, 380 + to * 6);
    const step = (t) => {
      const k = Math.max(0, Math.min(1, (t - t0) / dur)), eased = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(to * eased).toLocaleString();
      if (k < 1) requestAnimationFrame(step);
    };
    el.textContent = "0";
    requestAnimationFrame(step);
  });
}

// first open of the day: the screen powers on while Today loads, counts in once the numbers are there, then hands off
// (any key or click skips it). Returns null when it has already played today.
function jarvisBootStart() {
  const day = new Date().toDateString();
  let seen = "";
  try { seen = localStorage.getItem("cc.boot") || ""; localStorage.setItem("cc.boot", day); } catch { /* private mode */ }
  if (seen === day || matchMedia("(prefers-reduced-motion: reduce)").matches) return null;
  const box = document.createElement("div");
  box.className = "boot";
  box.setAttribute("aria-hidden", "true");
  box.innerHTML = `<div class="boot-screen"><div class="boot-name">Jarvis</div><div id="boot-lines"><div class="boot-line" style="animation-delay:420ms">› waking up</div></div></div>
    <span class="tel boot-skip">click to skip</span>`;
  document.body.appendChild(box);
  let over = false, after = null, safety = 0;
  const end = () => {
    if (over) return;
    over = true;
    clearTimeout(safety);
    removeEventListener("keydown", end);
    box.classList.add("out");
    setTimeout(() => box.remove(), 340);
    after?.();
  };
  box.addEventListener("click", end);
  addEventListener("keydown", end);
  safety = setTimeout(end, 9000);  // a slow network never traps anyone behind it
  return {
    end,
    // the real numbers have arrived: show them, then reveal Today (reveal runs at once if it was already skipped)
    say(lines, name, reveal) {
      if (over) return reveal?.();
      after = reveal;
      box.querySelector(".boot-name").textContent = name;
      box.querySelector("#boot-lines").innerHTML = lines.map((l, i) => `<div class="boot-line" style="animation-delay:${i * 170}ms">› ${esc(l)}</div>`).join("");
      clearTimeout(safety);
      safety = setTimeout(end, lines.length * 170 + 900);
    },
  };
}

// tab switches morph instead of jumping, where the browser can
function withTransition(fn) {
  if (!document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) return fn();
  document.startViewTransition(fn);
}

// ⌘J / Ctrl+J opens Jarvis from anywhere
addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "j") {
    const fab = document.getElementById("chat-fab");
    if (fab && !fab.classList.contains("hidden")) { e.preventDefault(); fab.click(); }
  }
});

// the ⋯ menus everywhere: one open at a time, never cut off by the list around them, above the button when there's no
// room below, and closed by a click elsewhere, Esc or scrolling
function placeMenu(d) {
  const menu = d.querySelector(".cal-menu"), btn = d.querySelector("summary");
  if (!menu || !btn) return;
  menu.style.cssText = "position:fixed;visibility:hidden;left:0;top:0;right:auto;max-height:none";
  const b = btn.getBoundingClientRect(), gap = 6, edge = 10, h = menu.offsetHeight, w = menu.offsetWidth;
  const below = innerHeight - b.bottom - gap - edge, above = b.top - gap - edge;
  const up = h > below && above > below;
  const room = up ? above : below;
  const top = up ? b.top - gap - Math.min(h, room) : b.bottom + gap;
  const left = Math.max(edge, Math.min(b.right - w, innerWidth - w - edge));
  menu.style.cssText = `position:fixed;visibility:visible;left:${left}px;top:${top}px;right:auto;max-height:${room}px;overflow-y:auto;z-index:70`;
  placeMenu.at = Date.now();
}
function closeMenus(except) { document.querySelectorAll("details.cal[open]").forEach((d) => { if (d !== except) d.open = false; }); }
document.addEventListener("toggle", (e) => {
  if (e.target.matches?.("details.cal") && e.target.open) { closeMenus(e.target); placeMenu(e.target); }
}, true);
document.addEventListener("click", (e) => { if (!e.target.closest("details.cal")) closeMenus(); });
addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });
addEventListener("resize", () => closeMenus());
addEventListener("scroll", (e) => { if (Date.now() - (placeMenu.at || 0) > 200 && !e.target.closest?.(".cal-menu")) closeMenus(); }, true);
