// The plan editor. A plan is a list of lines; each line is shown by what it is:
// a comment, the `load` line (checkboxes), the `record` line, or a step. A step
// is a chain of dropdowns whose choices come from the server, which asks the
// instruments' own declared commands what may follow the words so far -- so
// picking `hvc` narrows the next choice to hvc's commands, and `platen` narrows
// that to a number box with its limits. The text of the line is always the
// truth; the dropdowns only edit it. Server text goes on the page as text only.
(function () {
  "use strict";
  const { api, el, $, famOf, unitText } = window.App;
  // el(tag, attrs, "text") makes a text element; h(tag, attrs, ...children) one with children.
  const h = (tag, attrs, ...kids) => { const e = el(tag, attrs); for (const k of kids) e.append(k); return e; };

  const S = {
    plans: [], name: null, editable: false, hash: null, original: "", lines: [], raw: false,
    errors: [], warnings: [], available: [], cache: new Map(), checkTimer: null, rows: [], busy: false, tokStamp: 0,
    fresh: -1,     // the line just inserted as a step: empty, so it shows the step chooser, not a blank
  };

  const { kindOf, withHeader, missingHeaders } = window.PlanText;
  const splitWords = (line) => line.trim().split(/\s+/).filter(Boolean);
  const text = () => S.lines.join("\n") + "\n";
  const dirty = () => text() !== S.original;
  const scriptsOf = () => {
    const load = S.lines.find((l) => kindOf(l) === "load");
    return load ? splitWords(load).slice(1) : [];
  };

  function say(msg, isError) {
    const m = $("#plan-msg");
    m.className = isError ? "error" : "note";
    m.textContent = msg || "";
  }

  // --- the list of plans -----------------------------------------------------------------

  async function loadList() {
    const r = await api("/api/plans");
    S.plans = r.plans;
    const box = $("#plan-list");
    box.replaceChildren();
    for (const p of r.plans) {
      const b = el("button", { type: "button", class: p.name === S.name ? "active" : "" });
      b.append(el("span", {}, p.name),
        el("small", {}, (p.editable ? "yours" : "shipped") + (p.error ? " - cannot run" : "")));
      if (p.error) b.title = p.error;
      b.addEventListener("click", () => openPlan(p.name));
      box.append(b);
    }
  }

  async function openPlan(name) {
    if (dirty() && !confirm("Discard your unsaved changes?")) return;
    try {
      const r = await api("/api/plans/" + encodeURIComponent(name));
      load(r);
    } catch (e) { say(e.message, true); }
    loadList();
  }

  function load(r) {
    S.name = r.name; S.editable = r.editable; S.hash = r.hash;
    S.lines = r.text.split("\n");
    if (S.lines[S.lines.length - 1] === "") S.lines.pop();
    S.original = text();
    S.errors = r.errors || [];
    S.warnings = r.warnings || [];
    S.raw = false;
    S.fresh = -1;
    say("");
    renderAll();
  }

  // --- rendering ---------------------------------------------------------------------------

  function renderAll() {
    $("#plan-title").textContent = S.name || "No plan open";
    const badge = $("#plan-badge");
    badge.textContent = !S.name ? "" : S.editable ? "yours" : "shipped, read-only";
    $("#plan-text").hidden = !S.raw;
    $("#plan-rows").hidden = S.raw;
    $("#plan-raw").textContent = S.raw ? "Show steps" : "Edit as text";
    if (S.raw) $("#plan-text").value = text();
    $("#plan-text").readOnly = !S.editable;
    renderRows();
    updateButtons();
    showErrors();
  }

  function renderRows() {
    const box = $("#plan-rows");
    box.replaceChildren();
    S.rows = [];
    if (!S.name) { box.append(el("p", { class: "note" }, "Pick a plan on the left, or make a new one.")); return; }
    S.lines.forEach((line, i) => {
      const row = el("div", { class: "prow " + kindOf(line) });
      row.append(el("div", { class: "no" }, String(i + 1)));
      const body = el("div", { class: "body" });
      row.append(body);
      const tools = el("div", { class: "tools" });
      if (S.editable) {
        const header = kindOf(line) === "load" || kindOf(line) === "record";     // their place is fixed
        for (const [label, title, fn] of [...(header ? [] : [["^", "Move up", () => move(i, -1)], ["v", "Move down", () => move(i, 1)]]),
                                          ["+", "Insert a step below", () => insert(i + 1, "")],
                                          ["#", "Insert a comment below", () => insert(i + 1, "# ")],
                                          ["x", "Delete this line", () => remove(i)]]) {
          const b = el("button", { type: "button", title }, label);
          b.addEventListener("click", fn);
          tools.append(b);
        }
      }
      row.append(tools, el("div", { class: "rowerr" }), el("div", { class: "rowwarn" }));
      box.append(row);
      S.rows.push({ row, body });
      renderLine(i);
    });
    if (!S.editable) paintTokens();
    if (S.editable) {
      const add = el("button", { type: "button" }, "+ Add a step");
      add.addEventListener("click", () => insert(S.lines.length, ""));
      box.append(h("div", { class: "prow" }, el("div"), add));
    }
  }

  function renderLine(i) {
    const { body } = S.rows[i];
    const line = S.lines[i], kind = kindOf(line);
    body.replaceChildren();
    if (!S.editable) { body.append(el("pre", { class: "line" }, line || " ")); return; }   // painted by paintTokens
    if (kind === "blank" && i === S.fresh) renderStep(i, body);
    else if (kind === "blank") body.append(el("span", {}, "(blank line)"));
    else if (kind === "comment") renderComment(i, body);
    else if (kind === "load") renderLoad(i, body);
    else if (kind === "record") renderRecord(i, body);
    else renderStep(i, body);
  }

  // A read-only plan is drawn with the same tokens, from the server's reading of it.
  async function paintTokens() {
    const stamp = ++S.tokStamp;
    let r;
    try { r = await api("/api/plan/tokens", { text: text() }); } catch (e) { return; }
    if (stamp !== S.tokStamp) return;
    r.lines.forEach((toks, i) => { if (S.rows[i]) S.rows[i].body.replaceChildren(tokenLine(toks)); });
  }

  function tokenLine(toks) {
    const line = el("div", { class: "tokline" });
    if (!toks.length) { line.append(el("span", { class: "note" }, " ")); return line; }
    const fam = famOf(toks[0].text);
    let text = null;
    for (const t of toks) {
      if (t.role === "text") {                         // a run of free text is one underlined phrase
        if (!text) { text = el("span", { class: "tok text" }, t.text); line.append(text); }
        else text.textContent += " " + t.text;
        continue;
      }
      text = null;
      line.append(el("span", { class: "tok " + t.role, "data-fam": t.role === "script" ? "flow" : fam }, t.text));
    }
    return line;
  }

  function legend() {
    const box = $("#plan-legend");
    box.replaceChildren(el("span", {}, "Reading a step:"),
      el("span", { class: "tok verb", "data-fam": "hvc" }, "hvc"), el("span", {}, "instrument or step"),
      el("span", { class: "tok kw", "data-fam": "hvc" }, "platen"), el("span", {}, "keyword"),
      el("span", { class: "tok value" }, "25 \u00b0C"), el("span", {}, "a value you type"),
      el("span", { class: "tok text" }, "a log message"), el("span", {}, "free text"),
      el("span", { class: "tok bad" }, "900"), el("span", {}, "does not fit"));
  }

  function setLine(i, value, rerender) {
    S.lines[i] = value;
    if (rerender) renderLine(i);
    scheduleCheck();
    updateButtons();
  }

  function renderComment(i, body) {
    const input = el("input", { class: "tok comment", value: S.lines[i], "aria-label": "Comment" });
    input.addEventListener("input", () => setLine(i, input.value, false));
    input.addEventListener("change", () => renderLine(i));
    body.append(input);
  }

  function renderLoad(i, body) {
    const chosen = new Set(splitWords(S.lines[i]).slice(1));
    const names = [...S.available, ...[...chosen].filter((n) => !S.available.includes(n))];
    const box = h("div", { class: "chain" }, el("span", { class: "tok verb", "data-fam": "flow" }, "load"));
    for (const n of names) {
      const label = el("label", { class: "tok script" + (S.available.includes(n) ? "" : " bad") }), cb = el("input", { type: "checkbox" });
      cb.checked = chosen.has(n);
      cb.addEventListener("change", () => {
        if (cb.checked) chosen.add(n); else chosen.delete(n);
        S.lines[i] = ["load", ...names.filter((x) => chosen.has(x))].join(" ");
        scheduleCheck(); updateButtons();
        S.lines.forEach((l, k) => { if (kindOf(l) === "step") renderLine(k); });   // their choices depend on it
      });
      label.append(cb, el("span", {}, n + (S.available.includes(n) ? "" : " (not found)")));
      box.append(label);
    }
    body.append(box);
  }

  function renderRecord(i, body) {
    const m = /^\s*record\s+every\s+([0-9.]+)\s+(s|min|h)\s*$/i.exec(S.lines[i]);
    if (!m) { renderStep(i, body, true); return; }
    const n = el("input", { value: m[1], inputmode: "decimal", "aria-label": "Record every" });
    const unit = el("select", { "aria-label": "Unit", class: "tok kw", "data-fam": "flow" });
    for (const u of ["s", "min", "h"]) unit.append(el("option", { value: u }, u));
    unit.value = m[2].toLowerCase();
    const upd = () => setLine(i, `record every ${n.value.trim() || "0"} ${unit.value}`, false);
    n.addEventListener("input", upd);
    unit.addEventListener("change", upd);
    body.append(h("div", { class: "chain" }, el("span", { class: "tok verb", "data-fam": "flow" }, "record"),
      el("span", { class: "tok kw", "data-fam": "flow" }, "every"), h("span", { class: "tok value" }, n), unit));
  }

  const limitsOf = (o) => {
    const lim = o.lo !== null || o.hi !== null ? ` ${o.lo ?? ""}..${o.hi ?? ""}` : "";
    return `<${o.text}${lim}${o.unit ? " " + o.unit : ""}>`;
  };

  async function lineOptions(words) {
    const key = scriptsOf().join(",") + "|" + words.join(" ");
    if (!S.cache.has(key)) S.cache.set(key, api("/api/plan/line", { scripts: scriptsOf(), words }));
    return S.cache.get(key);
  }

  async function renderStep(i, body, forceRaw) {
    const stamp = (body._stamp = (body._stamp || 0) + 1);
    const words = splitWords(S.lines[i]);
    let r;
    try { r = await lineOptions(words); } catch (e) { body.textContent = e.message; return; }
    if (body._stamp !== stamp) return;                       // a newer render took over
    body.replaceChildren();
    const chain = el("div", { class: "chain" });
    const apply = (next) => { if (!next.length) S.fresh = i; setLine(i, next.join(" "), true); };

    for (let k = 0; k <= words.length; k++) {
      const opts = r.positions[k] || [];
      if (!opts.length) {
        if (k < words.length) {                              // a word that fits nothing: show it, flagged
          const bad = el("input", { value: words[k], "aria-label": "Word " + (k + 1) });
          bad.addEventListener("change", () => apply(words.slice(0, k).concat(splitWords(bad.value)).concat(words.slice(k + 1))));
          chain.append(h("span", { class: "tok bad", title: "This word does not fit here" }, bad));
        }
        continue;
      }
      const current = words[k];
      if (opts.some((o) => o.kind === "rest")) {              // the rest of the line is free text
        const rest = el("input", { value: words.slice(k).join(" "), placeholder: "message", "aria-label": "Message" });
        rest.addEventListener("input", () => { S.lines[i] = words.slice(0, k).concat(splitWords(rest.value)).join(" "); scheduleCheck(); updateButtons(); });
        chain.append(h("span", { class: "tok text" }, rest));
        break;
      }
      const wordOpts = opts.filter((o) => o.kind === "word"), slot = opts.find((o) => o.kind !== "word");
      if (!slot) {                                            // only fixed words: a dropdown
        const sel = el("select", { "aria-label": "Choice " + (k + 1),
          class: "tok " + (k === 0 ? "verb" : "kw") + (k === 0 && current === undefined ? " empty" : ""),
          "data-fam": famOf(k === 0 ? current : words[0]) });
        if (current === undefined) sel.append(el("option", { value: "" }, k === 0 ? "add a step..." : "..."));
        const addable = k === 0 && current === undefined ? missingHeaders(S.lines) : [];   // load / record, if deleted
        for (const kind of addable) {
          const opt = el("option", { value: "@" + kind }, kind + (kind === "load" ? "  (always first)" : "  (after load)"));
          opt.title = kind === "load" ? "Which rScripts run. A plan starts with it; it goes to the top for you."
                                      : "How often a CSV row is written. It goes right after load for you.";
          sel.append(opt);
        }
        const seen = new Set();
        for (const o of wordOpts) {
          if (seen.has(o.text.toLowerCase())) continue;
          seen.add(o.text.toLowerCase());
          const opt = el("option", { value: o.text }, o.text);
          if (o.help) opt.title = o.help;
          sel.append(opt);
        }
        if (current !== undefined) {
          const match = wordOpts.find((o) => o.text.toLowerCase() === current.toLowerCase());
          if (!match) { sel.append(el("option", { value: current }, current + " (?)")); sel.classList.add("bad"); }
          sel.value = match ? match.text : current;
        }
        sel.addEventListener("change", () => {
          if (sel.value.startsWith("@")) { addHeader(sel.value.slice(1), i); return; }
          apply(words.slice(0, k).concat(sel.value ? [sel.value] : []));
        });
        chain.append(sel);
      } else {                                                // a value to type, maybe also fixed words
        const listId = `dl-${i}-${k}`;
        const isWord = current !== undefined && wordOpts.some((o) => o.text.toLowerCase() === current.toLowerCase());
        const lim = slot.lo !== null || slot.hi !== null ? `${slot.lo ?? ""}..${slot.hi ?? ""}` : slot.text;
        const input = el("input", { value: current ?? "", placeholder: lim, size: Math.max(4, lim.length),
                                    "aria-label": limitsOf(slot), title: (slot.help || "") + (wordOpts.length ? " -- or pick a word from the list" : "") });
        const box = h("span", { class: "tok " + (isWord ? "kw" : "value"), "data-fam": famOf(words[0]) }, input);
        if (!isWord && slot.unit) box.append(el("span", { class: "unit" }, unitText(slot.unit)));
        if (wordOpts.length) {
          input.setAttribute("list", listId);
          const dl = el("datalist", { id: listId });
          for (const o of wordOpts) dl.append(el("option", { value: o.text }));
          chain.append(dl);
        }
        input.addEventListener("change", () => {
          const v = input.value.trim();
          const word = wordOpts.find((o) => o.text.toLowerCase() === v.toLowerCase());
          if (word) apply(words.slice(0, k).concat([word.text]));          // a fixed word: later choices restart
          else if (v === "") apply(words.slice(0, k));
          else { const next = words.slice(); next[k] = v; apply(next); }   // a value: the words after it stay
        });
        chain.append(box);
      }
    }
    if (r.error) [...chain.querySelectorAll(".tok")].pop()?.classList.add("bad");   // errors sit at the farthest point
    if (forceRaw) chain.prepend(el("span", { class: "tok verb", "data-fam": "flow" }, "record"));
    body.append(chain);
    body.parentElement.querySelector(".rowerr").dataset.stepError = r.error || "";
    showErrors();
  }

  // --- structure -------------------------------------------------------------------------------

  // Put `kind` (load or record) where it belongs, wherever it was asked from; the
  // empty row the request came from is dropped.
  async function addHeader(kind, fromRow) {
    const lines = S.lines.slice();
    if (lines[fromRow] !== undefined && kindOf(lines[fromRow]) === "blank") lines.splice(fromRow, 1);
    let scripts = [];
    if (kind === "load") {                                  // the scripts the steps already use, else a safe default
      try { scripts = (await api("/api/plan/needs", { text: lines.join("\n") })).rscripts; } catch (e) { /* fall back */ }
      if (!scripts.length) scripts = [S.available.includes("rSMTC08") ? "rSMTC08" : (S.available[0] || "")].filter(Boolean);
    }
    const { lines: next, index } = withHeader(lines, kind, scripts);
    S.lines = next;
    S.fresh = -1;
    renderRows(); scheduleCheck(); updateButtons();
    S.rows[index]?.row.scrollIntoView({ block: "nearest" });
  }

  function insert(at, value) {
    S.lines.splice(at, 0, value);
    S.fresh = value === "" ? at : -1;
    renderRows(); scheduleCheck(); updateButtons();
  }
  function remove(i) { S.lines.splice(i, 1); S.fresh = -1; renderRows(); scheduleCheck(); updateButtons(); }
  function move(i, d) {
    const j = i + d;
    if (j < 0 || j >= S.lines.length) return;
    [S.lines[i], S.lines[j]] = [S.lines[j], S.lines[i]];
    S.fresh = -1;
    renderRows(); scheduleCheck(); updateButtons();
  }

  // --- checking ----------------------------------------------------------------------------------

  function scheduleCheck() {
    say("");                                                // an edit makes the last "Saved" stale
    clearTimeout(S.checkTimer);
    S.checkTimer = setTimeout(async () => {
      try {
        const r = await api("/api/plan/check", { text: text() });
        S.errors = r.errors; S.warnings = r.warnings || []; showErrors();
      } catch (e) { /* login screen */ }
    }, 400);
  }

  function showErrors() {
    $("#plan-doc-errors").textContent = S.errors.filter((e) => e.line === 0).map((e) => e.message).join(" ");
    S.rows.forEach((r, idx) => {
      const holder = r.row.querySelector(".rowerr");
      const msgs = new Set(S.errors.filter((e) => e.line === idx + 1).map((e) => e.message));
      if (holder.dataset.stepError) msgs.add(holder.dataset.stepError);
      holder.textContent = [...msgs].join("  ");
      // A warning: a rule this plan does not establish. It can still run; the step is checked live.
      const warn = msgs.size ? [] : S.warnings.filter((w) => w.line === idx + 1).map((w) => w.message);
      r.row.querySelector(".rowwarn").textContent = warn.length ? `\u26a0 ${warn.join("  ")}` : "";
    });
  }

  function updateButtons() {
    $("#plan-save").disabled = !S.name || !S.editable || !dirty() || S.busy;
    $("#plan-revert").disabled = !S.name || !dirty();
    $("#plan-saveas").disabled = !S.name;
    $("#plan-raw").disabled = !S.name;
    $("#plan-delete").disabled = !S.name || !S.editable || S.busy;
    $("#plan-delete").hidden = !S.editable;
    const d = dirty() ? " (unsaved changes)" : "";
    $("#plan-title").textContent = (S.name || "No plan open") + d;
  }

  // --- saving --------------------------------------------------------------------------------------

  // Save `content` (the open plan's text by default). Nothing in the editor
  // changes unless the server accepts it: a refused save leaves it as it was.
  async function save(asNew, newName, content) {
    const body = content === undefined ? text() : content;
    S.busy = true; updateButtons();
    try {
      const r = await api("/api/plan/save", asNew
        ? { name: newName, text: body, as_new: true }
        : { name: S.name, text: body, base_hash: S.hash });
      S.name = r.name; S.hash = r.hash; S.editable = true; S.errors = r.errors; S.warnings = r.warnings || [];
      S.lines = body.split("\n");
      if (S.lines[S.lines.length - 1] === "") S.lines.pop();
      S.original = text();
      say(r.errors.length ? `Saved, but it cannot run yet: ${r.errors.length} problem(s) below.`
        : S.warnings.length ? `Saved. It can run; ${S.warnings.length} step(s) depend on the chamber at the start (amber), checked when they run.`
        : "Saved. It can run.", r.errors.length > 0);
      await loadList();
      renderAll();
    } catch (e) {
      say(e.message, true);
    } finally { S.busy = false; updateButtons(); }
  }

  $("#plan-save").addEventListener("click", () => save(false));
  $("#plan-delete").addEventListener("click", async () => {
    if (!confirm(`Delete the plan "${S.name}"? It is moved to the trash folder (~/.formslab/plans/.trash), not erased.`)) return;
    try {
      const r = await api("/api/plan/delete", { name: S.name, base_hash: S.hash });
      S.name = null; S.lines = []; S.original = text();
      await loadList();
      const next = S.plans.find((p) => p.editable) || S.plans[0];
      if (next) await openPlan(next.name); else renderAll();
      say(`Deleted "${r.deleted}". It is kept in the trash folder if you need it back.`);
    } catch (e) { say(e.message, true); }
  });
  $("#plan-saveas").addEventListener("click", () => {
    const name = prompt("Name for your copy (letters, digits, - and _):", S.name ? S.name + "_copy" : "");
    if (name) save(true, name.trim());
  });
  $("#plan-new").addEventListener("click", () => {
    const name = prompt("Name for the new plan (letters, digits, - and _):", "");
    if (!name) return;
    if (dirty() && !confirm("Discard your unsaved changes?")) return;
    const first = S.available.includes("rSMTC08") ? "rSMTC08" : (S.available[0] || "rLACO");
    save(true, name.trim(), ["# " + name.trim(), "load " + first, "record every 10 s", "", "log start", "hold 30 s", ""].join("\n"));
  });
  $("#plan-revert").addEventListener("click", () => { if (confirm("Discard your unsaved changes?")) openPlanFresh(); });
  $("#plan-raw").addEventListener("click", () => {
    if (S.raw) {
      S.lines = $("#plan-text").value.replace(/\r\n/g, "\n").split("\n");
      if (S.lines[S.lines.length - 1] === "") S.lines.pop();
    } else {
      $("#plan-text").value = text();
    }
    S.raw = !S.raw;
    renderAll();
    scheduleCheck();
  });
  $("#plan-text").addEventListener("input", () => {
    S.lines = $("#plan-text").value.replace(/\r\n/g, "\n").split("\n");
    if (S.lines[S.lines.length - 1] === "") S.lines.pop();
    scheduleCheck(); updateButtons();
  });

  async function openPlanFresh() {
    const keep = S.name;
    S.original = text();                                     // not dirty, so openPlan will not ask again
    await openPlan(keep);
  }

  window.addEventListener("beforeunload", (ev) => { if (S.name && dirty()) { ev.preventDefault(); ev.returnValue = ""; } });

  // --- entry -----------------------------------------------------------------------------------------

  window.App.editor = {
    async open() {
      try {
        S.available = (await api("/api/rscripts")).rscripts;
        legend();
        await loadList();
        if (!S.name && S.plans.length) await openPlan(S.plans.find((p) => p.editable)?.name || S.plans[0].name);
        else renderAll();
      } catch (e) { say(e.message, true); }
    },
  };
})();
