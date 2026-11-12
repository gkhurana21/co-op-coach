/* Run only after the person presses a panel button. Requests remain in the worker. */
const fillingTabs = new Set();
async function currentFormTab() {
  const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  if (!tab?.id || !/^https?:\/\//.test(tab.url || "")) throw new Error("Open the application tab, then use Co-op Coach from the Chrome toolbar.");
  return tab;
}
async function fillCurrentForm(c) {
  const tab = await currentFormTab();
  if (fillingTabs.has(tab.id)) throw new Error("This form is already being filled.");
  fillingTabs.add(tab.id);
  try {
    const context = await request(c.base, "context", {url: tab.url}, c.ticket);
    const injected = await chrome.scripting.executeScript({target: {tabId: tab.id, allFrames: true}, files: ["form_shared.js", "form.js"]});
    const filled = [], left = [...context.needs_you], skipped = [];
    let captcha = false;
    const checkTab = async () => {
      if ((await chrome.tabs.get(tab.id)).url !== tab.url) throw new Error("The page changed. Press Fill this form again on the new page.");
    };
    for (const frame of injected) {
      const run = async (action, args) => {
        await checkTab();
        const [r] = await chrome.scripting.executeScript({target: {tabId: tab.id, documentIds: [frame.documentId]},
          func: (name, values) => globalThis.coopForm[name](...values), args: [action, args]});
        return r.result;
      };
      try {
        const scan = await run("scan", [context.answers]);
        filled.push(...scan.filled); captcha ||= scan.captcha;
        const plain = scan.questions.filter(q => q.kind !== "combobox");
        if (plain.length) {
          const result = await request(c.base, "answers", {url: tab.url, questions: plain}, c.ticket);
          for (const a of result.answers) {
            const q = plain.find(q => q.id === a.id);
            if (await run("answer", [a.id, a])) filled.push(q.label); else left.push(q.label);
          }
        }
        for (const q of scan.questions.filter(q => q.kind === "combobox")) {
          const ask = async options => (await request(c.base, "answers", {url: tab.url, questions: [{...q, options}]}, c.ticket)).answers[0];
          // Ask first, so unknown and demographic dropdowns are never opened.
          const seed = await ask([]);
          if (!seed.answer) { left.push(q.label); continue; }
          let chosen = false;
          try {
            await run("dropdown", [q.id, "open"]);
            await new Promise(resolve => setTimeout(resolve, 250));
            let options = await run("dropdown", [q.id, "read"]);
            let a = await ask(options);
            if (a.index === null || a.index === undefined) {
              await run("dropdown", [q.id, "search", seed.answer]);
              await new Promise(resolve => setTimeout(resolve, 1100));
              options = await run("dropdown", [q.id, "read"]); a = await ask(options);
            }
            if (a.index !== null && a.index !== undefined && await run("dropdown", [q.id, "choose", options[a.index]])) {
              chosen = true; filled.push(q.label); continue;
            }
            left.push(q.label);
          } finally { if (!chosen) await run("dropdown", [q.id, "close"]); }
        }
        if (context.prepared) {
          const files = await request(c.base, "files", {url: tab.url}, c.ticket);
          filled.push(...await run("attach", [files.files]));
        }
      } catch (e) {
        // Authorization and transport errors must stop the whole action, not leave other frames filling.
        skipped.push(`Frame ${frame.frameId}: ${e.message}`);
        break;
      }
    }
    return {filled: [...new Set(filled)], needs_you: [...new Set(left.filter(Boolean))], captcha,
      warnings: skipped, prepared: context.prepared, job: context.job};
  } finally { fillingTabs.delete(tab.id); }
}
