/* Credentials stay in session storage, which is inaccessible to content scripts by default. */
importScripts("worker_form.js", "worker_inbox.js");
chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(() => {});
function endpoint(value) {
  const u = new URL(value);
  if (u.protocol !== "http:" || u.hostname !== "127.0.0.1" || u.username || u.password || u.pathname !== "/" || u.search || u.hash) throw new Error("Use the local app address, such as http://127.0.0.1:8765");
  return u.origin;
}
async function request(base, path, body, ticket) {
  let r;
  try {
    r = await fetch(base + "/api/extension/" + path, {method: "POST", credentials: "omit",
      headers: {"Content-Type": "application/json", ...(ticket ? {Authorization: `Bearer ${ticket}`} : {})},
      body: JSON.stringify(body || {}), signal: AbortSignal.timeout(30000)});
  } catch { throw new Error("The local app is not responding. Start ./run.sh (or run.bat), then retry."); }
  const data = await r.json();
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The app could not complete this request.");
  return data;
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("panel.html")) return;
  (async () => {
    if (message.action === "pair") {
      const base = endpoint(message.base);
      const data = await request(base, "pair", {code: message.code});
      await chrome.storage.session.set({connection: {base, ticket: data.ticket, profile: data.profile}});
      return {profile: data.profile, base};
    }
    const {connection: c} = await chrome.storage.session.get("connection");
    if (message.action === "state") return c ? {profile: c.profile, base: c.base} : {};
    if (!c) throw new Error("Connect from Settings → AI & extension in the app first.");
    if (message.action === "disconnect") {
      try { await request(c.base, "disconnect-self", {}, c.ticket); }
      finally { await chrome.storage.session.remove("connection"); }
      return {};
    }
    if (message.action === "today") return await request(c.base, "today", {}, c.ticket);
    if (message.action === "fill") return await fillCurrentForm(c);
    if (message.action === "inbox") return await readOutlookTab(c, (line) => chrome.runtime.sendMessage({progress: line}).catch(() => {}));
    if (message.action === "submitted") {
      const tab = await currentFormTab();
      return await request(c.base, "submitted", {url: tab.url}, c.ticket);
    }
    throw new Error("Unsupported action");
  })().then(data => reply({data}), e => reply({error: e.message}));
  return true;
});
