/* Isolated world only. No API access, secrets, page listeners, or submission actions. */
globalThis.coopForm = (() => {
  const {visible, safeField, fill, choices} = globalThis.coopShared;
  let fields = new Map();
  const element = id => document.querySelector(`[data-cc-choice="${CSS.escape(id)}"]`);
  function set(el, value) {
    const proto = el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
  }
  function options(el) {
    const ids = (el.getAttribute("aria-controls") || el.getAttribute("aria-owns") || "").split(/\s+/).filter(Boolean);
    const roots = ids.map(id => document.getElementById(id)).filter(Boolean);
    const container = el.closest('[class*=select__control], [class*=select-shell], .select, .f');
    if (!roots.length && container) roots.push(container);
    // Portal menus may live under body. Accept only a single visible listbox.
    if (!roots.length || !roots.some(r => r.querySelector('[role=option], .dropdown-location'))) {
      const lists = [...document.querySelectorAll('[role=listbox]')].filter(visible);
      if (lists.length === 1) roots.push(lists[0]);
    }
    return [...new Set(roots.flatMap(r => [...r.querySelectorAll('[role=option], .dropdown-location')]))]
      .filter(o => visible(o) && !o.matches('button, input, a, [aria-disabled="true"]') && !o.closest('button, a'));
  }
  function field(id) {
    const f = fields.get(id);
    if (!f) return null;
    const els = (Array.isArray(f.id) ? f.id : [f.id]).map(element);
    if (els.some(e => !e || !safeField(e, f.label))) return null;
    return {f, els};
  }
  function scan(answers) {
    const filled = fill(answers);
    fields = new Map(choices().slice(0, 100).map((f, i) => [`q${i}`, f]));
    return {filled, questions: [...fields].map(([id, f]) => ({...f, id})),
      captcha: !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], iframe[title*="captcha" i]')};
  }
  function answer(id, response) {
    const found = field(id); if (!found) return false;
    const {f, els} = found, el = els[0];
    if (!response.answer) return false;
    if (f.kind === "text" && !el.value) set(el, response.answer);
    else if (f.kind === "select" && (!el.value || el.selectedIndex === 0)) {
      const option = f.options?.[response.index];
      const match = [...el.options].filter(o => o.text.trim() === option && !o.disabled);
      if (match.length !== 1) return false;
      set(el, match[0].value);
    } else if (f.kind === "radio" && !els.some(e => e.checked)) {
      const radio = els[response.index]; if (!radio) return false;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked").set.call(radio, true);
      radio.dispatchEvent(new MouseEvent("click", {bubbles: true}));
      radio.dispatchEvent(new Event("input", {bubbles: true}));
      radio.dispatchEvent(new Event("change", {bubbles: true}));
    } else return false;
    return true;
  }
  function dropdown(id, action, value) {
    const found = field(id); if (!found || found.f.kind !== "combobox") return [];
    const el = found.els[0];
    if (action === "open") { if (el.value) return []; el.focus(); el.click(); }
    if (action === "search") {
      set(el, value.split(/[,(]/)[0].slice(0, 80));
      el.dispatchEvent(new KeyboardEvent("keyup", {key: "End", bubbles: true}));
    }
    if (action === "choose") {
      const found = options(el).filter(o => o.innerText.trim() === value);
      if (found.length !== 1) return false;
      const opt = found[0];
      opt.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true}));
      opt.dispatchEvent(new MouseEvent("mouseup", {bubbles: true})); opt.click();
      el.blur(); return true;
    }
    if (action === "close") {
      set(el, ""); el.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true})); el.blur();
    }
    return options(el).map(o => o.innerText.trim()).filter(Boolean).slice(0, 200);
  }
  function attach(files) {
    const filled = [];
    for (const file of files) for (const el of document.querySelectorAll('input[type=file]')) {
      const label = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      const text = `${label?.innerText || ''} ${el.name} ${el.id} ${el.closest('div,fieldset,label')?.innerText || ''}`;
      if (el.files.length || !safeField(el, text) || /autofill/i.test(text)) continue;
      if (!(file.kind === "resume" ? /resume|\bcv\b/i : /cover/i).test(text)) continue;
      const bytes = Uint8Array.from(atob(file.data), c => c.charCodeAt(0));
      const transfer = new DataTransfer(); transfer.items.add(new File([bytes], file.name, {type: file.type}));
      el.files = transfer.files; el.dispatchEvent(new Event("input", {bubbles: true})); el.dispatchEvent(new Event("change", {bubbles: true}));
      filled.push(`${file.kind} file`);
    }
    return filled;
  }
  return {scan, answer, dropdown, attach};
})();
