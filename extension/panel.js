const $ = id => document.getElementById(id);
async function send(action, data = {}) {
  const r = await chrome.runtime.sendMessage({action, ...data});
  if (r.error) throw new Error(r.error);
  return r.data;
}
function items(id, rows, title, detail, empty) {
  const root = $(id); root.replaceChildren();
  if (!rows.length) { root.textContent = empty; return; }
  for (const row of rows.slice(0, 6)) {
    const box = document.createElement("div"); box.className = "item";
    const name = document.createElement("strong"); name.textContent = title(row);
    const sub = document.createElement("div"); sub.className = "detail"; sub.textContent = detail(row);
    box.append(name, sub); root.append(box);
  }
}
async function refresh() {
  $("refresh").disabled = true; $("message").textContent = "Loading Today…";
  try {
    const d = await send("today");
    items("ready", d.ready_queue.applications || [], x => `${x.company} · ${x.title}`, x => x.why || x.status, "Nothing prepared yet. Prepare applications in the full app.");
    items("matches", d.search_jobs.jobs || [], x => `${x.company} · ${x.title}`, x => `${x.score}% match · ${x.where}`, "No matches yet.");
    const cal = d.my_calendar;
    items("calendar", cal.items || cal.events || cal.upcoming || [], x => x.title || x.company || "Upcoming", x => x.when || x.start || x.status || "Open the calendar for details", "Open the full calendar for your schedule.");
    const p = d.my_path;
    $("path").textContent = p.error || `${p.streak_days}-day streak · ${p.xp_today}/${p.daily_goal} XP today · ${p.neetcode_150?.solved || "0/150"} solved`;
    const next = d.coding_progress.next_up;
    $("problem").hidden = !next;
    if (next && /^https:\/\/(leetcode\.com|neetcode\.io)\//.test(next.link)) { $("problem").href = next.link; $("problem").textContent = `Next: ${next.title}`; }
    $("message").textContent = "";
  } catch (e) { $("message").textContent = e.message; }
  finally { $("refresh").disabled = false; }
}
async function state() {
  const c = await send("state");
  $("pair").hidden = !!c.profile; $("today").hidden = !c.profile;
  if (c.profile) { $("person").textContent = c.profile.name; $("open-app").href = c.base; await refresh(); }
}
$("pair").addEventListener("submit", async e => {
  e.preventDefault(); const button = e.target.querySelector("button"); button.disabled = true;
  try { await send("pair", {base: $("base").value, code: $("code").value.trim()}); $("code").value = ""; await state(); }
  catch (err) { $("message").textContent = err.message; }
  finally { button.disabled = false; }
});
$("refresh").addEventListener("click", refresh);
$("fill").addEventListener("click", async () => {
  $("fill").disabled = true; $("submitted").disabled = true;
  $("fill-result").textContent = "Reading this form…";
  try {
    const r = await send("fill");
    const parts = [r.filled.length ? `Filled: ${r.filled.join(", ")}.` : "No empty fields could be filled on this page."];
    if (r.needs_you.length) parts.push(`For you to review: ${r.needs_you.join(", ")}.`);
    if (r.captcha) parts.push("Complete the CAPTCHA yourself.");
    if (!r.prepared) parts.push("Prepare this posting in the app to attach tailored files.");
    if (r.warnings.length) parts.push(...r.warnings);
    parts.push("Check everything, finish any remaining fields, and submit yourself.");
    $("fill-result").textContent = parts.join(" ");
  } catch (e) { $("fill-result").textContent = e.message; }
  finally { $("fill").disabled = false; $("submitted").disabled = false; }
});
$("submitted").addEventListener("click", async () => {
  $("submitted").disabled = true;
  try { const r = await send("submitted"); $("fill-result").textContent = `Marked applied: ${r.company} · ${r.title}.`; }
  catch (e) { $("fill-result").textContent = e.message; }
  finally { $("submitted").disabled = false; }
});
const OUTLOOK_ORIGINS = ["https://outlook.office.com/*", "https://outlook.office365.com/*", "https://outlook.live.com/*", "https://outlook.cloud.microsoft/*"];
chrome.runtime.onMessage.addListener((m) => { if (m?.progress) $("inbox-result").textContent = `Reading… ${m.progress}`; });
$("inbox").addEventListener("click", async () => {
  $("inbox").disabled = true;
  try {
    // Asked here, on this click, and only for Outlook's own sites.
    if (!(await chrome.permissions.request({ origins: OUTLOOK_ORIGINS }))) throw new Error("Co-op Coach needs permission to read Outlook's page to do this.");
    $("inbox-result").textContent = "Reading your inbox…";
    const r = await send("inbox");
    $("inbox-result").textContent = `Read ${r.read} email${r.read === 1 ? "" : "s"}: ${r.job_emails} about applications, ${r.updates} tracker update${r.updates === 1 ? "" : "s"}.${r.done ? "" : " Press again to keep going."}`;
  } catch (e) { $("inbox-result").textContent = e.message; }
  finally { $("inbox").disabled = false; }
});
$("disconnect").addEventListener("click", async () => { try { await send("disconnect"); } catch (e) { $("message").textContent = e.message; } await state(); });
state().catch(e => { $("message").textContent = e.message; });
