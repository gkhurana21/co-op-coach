/* Injected into the student's own Outlook tab, only after they press "Read my Outlook tab" in the side panel.
   Reads what the page shows as text: the message list, and the reading pane after opening a row. Never reads
   cookies, storage, tokens or network traffic, and never sends, moves or deletes anything. */
globalThis.coopInbox = {
  list() {
    const rows = [...document.querySelectorAll('[role="listbox"] [role="option"], [data-convid]')]
      .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    const seen = new Set(), out = [];
    for (const r of rows) {
      const id = r.getAttribute("data-convid") || r.querySelector("[data-convid]")?.getAttribute("data-convid") || r.id;
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const dated = [...r.querySelectorAll("[title]")].map((e) => e.getAttribute("title")).filter((t) => /\d/.test(t)).join(" | ");
      out.push({ id: id.slice(0, 300), label: (r.getAttribute("aria-label") || "").slice(0, 600), text: (r.innerText || "").slice(0, 600), dated: dated.slice(0, 600) });
    }
    return out;
  },
  scroll() {
    let e = document.querySelector('[role="listbox"] [role="option"], [data-convid]');
    while (e && e !== document.body) {
      const s = getComputedStyle(e);
      if (/(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 5) {
        const before = e.scrollTop; e.scrollTop = before + e.clientHeight * 0.85; return e.scrollTop - before;
      }
      e = e.parentElement;
    }
    return 0;
  },
  read() {
    const pane = document.querySelector('[aria-label="Reading Pane" i], [role="main"] [aria-label*="Reading" i], #ReadingPaneContainerId')
      || document.querySelector('[role="main"]') || document.body;
    const body = pane.querySelector('[aria-label="Message body" i], [role="document"]') || pane;
    const subject = (pane.querySelector('[role="heading"][aria-level="2"], [role="heading"]') || {}).innerText || "";
    const sender = [...pane.querySelectorAll('[aria-label^="From" i], [title*="@"], span')].map((e) => e.getAttribute("title") || e.innerText || "")
      .find((t) => /@/.test(t)) || "";
    const when = [...pane.querySelectorAll('[aria-label*="Date" i], [title]')].map((e) => e.getAttribute("title") || "")
      .find((t) => /\d{1,2}:\d{2}/.test(t)) || "";
    return { subject: subject.slice(0, 1000), sender: sender.slice(0, 600), when: when.slice(0, 300), body: (body.innerText || "").slice(0, 8000), url: location.href.slice(0, 2048) };
  },
  async open(id, lastSubject) {
    const row = document.querySelector(`[data-convid="${CSS.escape(id)}"]`) || document.getElementById(id);
    if (!row) return null;
    row.click();
    const end = Date.now() + 6000;
    let got = this.read();
    while (Date.now() < end && (!got.subject || got.subject === lastSubject || got.body.length < 20)) {
      await new Promise((r) => setTimeout(r, 120));  // until the reading pane shows this one
      got = this.read();
    }
    return got;
  },
};
