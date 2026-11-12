// A tiny fake DOM for the page scripts, under node: setting innerHTML makes a flat list of child elements (one per
// tag), and the API answers only when a test says so, in the order it picks (as a slow network would).
// Used by tests/test_tracks.py:  const { load, answer, settle, html } = require("./dom_harness.js");
const fs = require("fs");
const vm = require("vm");

class El {
  constructor(tag, attrs) {
    this.tag = tag; this.attrs = attrs; this.kids = []; this.html = ""; this.on = {}; this.value = attrs.value || "";
    this.dataset = {};
    for (const [k, v] of Object.entries(attrs)) if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
    this.classList = { toggle() {}, add() {}, remove() {}, contains: () => false };
  }
  set innerHTML(h) { this.html = h; this.kids = [...h.matchAll(/<([a-z0-9]+)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*\/?>/gi)].map((m) => new El(m[1].toLowerCase(), Object.fromEntries([...m[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map((a) => [a[1], a[2] ?? ""])))); }
  get innerHTML() { return this.html; }
  set textContent(t) { this.innerHTML = String(t); }
  get textContent() { return this.html; }
  insertAdjacentHTML(_, h) { this.innerHTML = this.html + h; }
  addEventListener(type, fn) { (this.on[type] ||= []).push(fn); }
  closest() { return { open: false }; }
  focus() {}
  all() { return this.kids.flatMap((k) => [k, ...k.all()]); }
  querySelectorAll(sel) {
    const last = sel.trim().split(/\s+/).pop();
    const m = last.match(/^#([\w-]+)$/) || last.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    return this.all().filter((e) => (last.startsWith("#") ? e.attrs.id === m[1] : m ? m[1] in e.attrs && (m[2] === undefined || e.attrs[m[1]] === m[2]) : e.tag === last));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

// load one page script with what app.js would give it
function load(src, s = {}) {
  const root = new El("body", {});
  root.innerHTML = '<main id="main"></main><div id="modal-root"></div><div id="fit-card"></div>';
  // a lookup finds the newest element with that id (the fake DOM keeps old children around)
  const find = (sel) => { const all = root.querySelectorAll(sel); return all[all.length - 1] || null; };
  const document = { querySelector: find, querySelectorAll: (sel) => root.querySelectorAll(sel) };
  const store = {};
  const pending = [];
  const ctx = {
    document, console, setTimeout, clearTimeout, confirm: () => true, URLSearchParams,
    S: { tab: "matches", pid: 1, filters: { track: "" }, app: { categories: ["Software", "Quant", "Other"], terms: ["Summer 2027"], ai_enabled: false, profiles: [{ id: 1, resume_filename: "r.pdf" }] }, ...s },
    LS: { get: (k, d) => (k in store ? store[k] : d), set: (k, v) => { store[k] = v; } },
    $: (sel) => document.querySelector(sel),
    esc: (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    safeUrl: (u) => (/^https?:\/\//i.test(u || "") ? u : "#"),
    api: (url, opts) => new Promise((res, rej) => pending.push({ url, opts, res, rej })),
    toast: () => {}, openSettings: () => {}, switchTab: () => {}, goView: () => {}, renderNav: () => {}, aiName: () => "AI", extIcon: "↗",
    alumniUrl: (c) => "https://www.linkedin.com/search/results/people/?keywords=" + encodeURIComponent(c), openPeople: () => {},
    jobRow: (j) => `<div class="job" data-id="${j.id}"></div>`, bindJobActions: () => {},
    D: { resume: {} }, drawEditor: () => {}, changed: () => {},
  };
  ctx.profile = () => ctx.S.app.profiles[0];
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(src, "utf8"), ctx);
  return { ...ctx, ctx, store, pending, run: (code) => vm.runInContext(code, ctx), find };
}

// answer the oldest (or newest) waiting call to this API path
function answer(h, path, data, newest = false) {
  const waiting = h.pending.map((p, i) => [p, i]).filter(([p]) => p.url.split("?")[0] === path);
  if (!waiting.length) throw new Error("nothing waiting for " + path + " (waiting: " + h.pending.map((p) => p.url.split("?")[0]).join(", ") + ")");
  const [, i] = waiting[newest ? waiting.length - 1 : 0];
  h.pending.splice(i, 1)[0].res(JSON.parse(JSON.stringify(data)));
}

const settle = () => new Promise((r) => setTimeout(r, 0));
const html = (h, sel) => (h.find(sel) || { innerHTML: "" }).innerHTML;

module.exports = { El, load, answer, settle, html };
