/* "Read my Outlook tab": runs in the student's own signed-in Outlook tab, where they can watch it. The app decides
   which rows to open (new, possibly about an application, inside the lookback); this opens each one in the reading
   pane, reads its text and sends it to the local app. Nothing is sent anywhere else. */
const OUTLOOK_HOSTS = /^https:\/\/(outlook\.office\.com|outlook\.office365\.com|outlook\.live\.com|outlook\.cloud\.microsoft)\//;
let readingInbox = false;

async function readOutlookTab(c, onProgress) {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id || !OUTLOOK_HOSTS.test(tab.url || "")) throw new Error("Open your Outlook inbox in this tab first, then press Read my Outlook tab.");
  if (readingInbox) throw new Error("Already reading your inbox.");
  readingInbox = true;
  const run = async (name, args = []) => {
    const [r] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: (n, a) => globalThis.coopInbox[n](...a), args: [name, args] });
    return r.result;
  };
  const started = Date.now(), BUDGET = 240000;
  let read = 0, jobEmails = 0, updates = 0, last = "", stalls = 0;
  const handled = new Set();
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["inbox.js"] });
    while (Date.now() - started < BUDGET) {
      const rows = (await run("list")).filter((r) => !handled.has(r.id));
      rows.forEach((r) => handled.add(r.id));
      if (!rows.length) {
        if (++stalls >= 3) break;  // the end of the list
      } else {
        stalls = 0;
        const plan = await request(c.base, "inbox-plan", { rows }, c.ticket);
        const batch = [];
        for (const id of plan.open) {
          if (Date.now() - started > BUDGET) break;
          const row = rows.find((r) => r.id === id);
          const got = await run("open", [id, last]);
          if (!got) continue;
          last = got.subject;
          batch.push({ id, dated: row.dated || row.label, ...got });
          read++;
          onProgress?.(`read ${read} · ${got.subject.slice(0, 60)}`);
          if (batch.length >= 10) { const r = await request(c.base, "inbox", { messages: batch.splice(0) }, c.ticket); jobEmails += r.job_emails; updates += r.updates; }
        }
        if (batch.length) { const r = await request(c.base, "inbox", { messages: batch }, c.ticket); jobEmails += r.job_emails; updates += r.updates; }
        if (plan.stop) break;  // reached the lookback window's end
      }
      if (!(await run("scroll"))) stalls++;
      await new Promise((r) => setTimeout(r, 900));
    }
    await request(c.base, "inbox", { messages: [], finished: true }, c.ticket);
    return { read, job_emails: jobEmails, updates, done: Date.now() - started < BUDGET };
  } finally { readingInbox = false; }
}
