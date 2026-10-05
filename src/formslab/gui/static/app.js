// formsLabCLI GUI: login, status and control, the plot viewer. No framework;
// every value from the server is put on the page as text only.
(function () {
  "use strict";
  const C = window.Chart2D;
  const $ = (sel) => document.querySelector(sel);
  const el = (tag, attrs, text) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  };

  // The colour family of a phrase is the step or instrument it starts with.
  const FAMILY = { hvc: "hvc", psu1: "psu", psu2: "psu", cryo: "cryo", slta: "slta", tc: "tc" };
  const STEP_WORDS = ["hold", "until", "log", "load", "record"];
  const famOf = (word) => {
    const w = (word || "").toLowerCase();
    return FAMILY[w] || (STEP_WORDS.includes(w) ? "flow" : "other");
  };
  const unitText = (u) => (u === "C" ? "\u00b0C" : u || "");

  window.App = { api: (...a) => api(...a), el, $, famOf, unitText };    // for editor.js, loaded next

  const state = {
    view: "status", timer: null, runs: [], run: null, selected: new Set(), data: null,
    x0: null, x1: null, hoverT: null, drag: null, tempUnit: "C", plotTimer: null, frame: 0,
    plansLoaded: false,
  };

  // --- server calls ------------------------------------------------------------------

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: "POST",
      headers: { "X-Requested-With": "formslab", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("login required"); }
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  // --- login -------------------------------------------------------------------------

  // The header's End button must be right on every tab, so it has its own slow poll.
  async function pollHeader() {
    try {
      if (!document.hidden) {
        const s = await api("/api/status?log=1");
        $("#end").hidden = !s.host;
        $("#end").disabled = Boolean(s.action);
      }
    } catch (e) { /* the login screen takes over on a 401 */ }
    state.headerTimer = setTimeout(pollHeader, 2000);
  }

  function showLogin() {
    clearTimeout(state.headerTimer);
    stopTimers();
    state.plansLoaded = false;
    $("#app").hidden = true;
    $("#login").hidden = false;
    $("#login-password").value = "";
  }

  async function startApp(user) {
    clearTimeout(state.headerTimer);
    showDemo();
    pollHeader();
    $("#login").hidden = true;
    $("#app").hidden = false;
    $("#who").textContent = user;
    const wanted = location.hash.slice(1);
    show(["plots", "plans", "tvac"].includes(wanted) ? wanted : "status");
  }

  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("#login-error").textContent = "";
    try {
      const r = await api("/api/login", { user: $("#login-user").value, password: $("#login-password").value });
      startApp(r.user);
    } catch (e) {
      $("#login-error").textContent = e.message;
    }
  });

  $("#logout").addEventListener("click", async () => {
    try { await api("/api/logout", {}); } catch (e) { /* already out */ }
    showLogin();
  });

  // --- views -------------------------------------------------------------------------

  function show(view) {
    state.view = view;
    location.hash = view;
    for (const b of document.querySelectorAll("#nav button")) b.classList.toggle("active", b.dataset.view === view);
    $("#view-status").hidden = view !== "status";
    $("#view-plans").hidden = view !== "plans";
    $("#view-tvac").hidden = view !== "tvac";
    $("#view-plots").hidden = view !== "plots";
    stopTimers();
    if (view === "status") { state.plansLoaded = false; pollStatus(); }     // plans may have been saved since
    if (view === "plots") loadRuns();
    if (view === "plans" && window.App.editor) window.App.editor.open();
    if (view === "tvac") pollTvac();
  }
  for (const b of document.querySelectorAll("#nav button")) b.addEventListener("click", () => show(b.dataset.view));

  function stopTimers() {
    clearTimeout(state.timer);
    clearInterval(state.plotTimer);
    state.timer = state.plotTimer = null;
  }

  // --- status ------------------------------------------------------------------------

  const ORDER = ["hvc", "tc", "psu1", "psu2", "cryo", "slta"];

  function fmtAge(s) {
    return s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
  }
  function fmtValue(v) {
    if (v === null || v === undefined) return "-";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(5)));
    return String(v);
  }
  function flatten(obj, prefix, out) {
    for (const [k, v] of Object.entries(obj || {})) {
      if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, `${prefix}${k} `, out);
      else out.push([prefix + k, Array.isArray(v) ? v.join(", ") : v]);
    }
    return out;
  }

  function renderStatus(s) {
    const banner = $("#run-banner");
    banner.className = "banner " + (s.host ? "running" : "stopped");
    banner.textContent = s.host
      ? `Running: plan ${s.host.plan} (pid ${s.host.pid}), since ${new Date(s.host.started).toLocaleString()}. CSV in ${s.host.output}`
      : "No run is going." + (s.last_run ? ` Last run: plan ${s.last_run.plan}.` : "");
    if (s.cast_unreadable) banner.textContent += " The CAST file could not be read just now.";
    renderControls(s);

    const cards = $("#cards");
    cards.replaceChildren();
    const labels = Object.keys(s.blocks);
    labels.sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99));
    for (const label of labels) {
      const b = s.blocks[label];
      const card = el("div", { class: "card " + (b.live ? "live" : "stale") });
      const h = el("h3");
      h.append(el("span", {}, label), el("span", { class: "badge" }, (b.live ? "live" : "not live") + " · " + fmtAge(b.age_s)));
      card.append(h);
      if (!b.live && b.reason) card.append(el("div", { class: "why" }, b.reason));
      if (b.pending) card.append(el("div", { class: "pending" }, "a command is waiting to be taken"));
      const rows = flatten(b.status, "", []);
      if (rows.length) {
        const t = el("table");
        const names = b.labels || {};
        for (const [k, v] of rows) {
          const tr = el("tr");
          tr.append(el("td", names[k] ? { title: k } : {}, names[k] || k), el("td", {}, fmtValue(v)));
          t.append(tr);
        }
        card.append(t);
      }
      cards.append(card);
    }
    const log = $("#log");
    const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.textContent = s.log.join("\n") || "(no host log yet)";
    if (atEnd) log.scrollTop = log.scrollHeight;
  }

  // --- control ---------------------------------------------------------------------

  const busy = (s) => Boolean(s.action);

  function renderControls(s) {
    const running = Boolean(s.host);
    $("#start-box").hidden = running;
    $("#run-box").hidden = !running;
    $("#end").hidden = !running;
    for (const id of ["#start", "#pause", "#resume", "#end"]) $(id).disabled = busy(s);
    const note = $("#action");
    if (s.action === "starting") { note.className = "note"; note.textContent = "Starting the run..."; }
    else if (s.action === "ending") { note.className = "note"; note.textContent = "Ending: each instrument's shutdown is running..."; }
    else if (note.dataset.sticky !== "1") { note.textContent = ""; }
    if (!running && !state.plansLoaded) loadPlans();
  }

  function say(note, text, isError) {
    note.className = isError ? "error" : "note";
    note.textContent = text;
  }

  async function loadPlans() {
    state.plansLoaded = true;
    try {
      const r = await api("/api/plans");
      const sel = $("#plan"), keep = sel.value;
      sel.replaceChildren();
      for (const p of r.plans) {
        const o = el("option", { value: p.name },
          p.error ? `${p.name} (cannot run)`
            : `${p.name} - ${p.rscripts.join(", ")}${p.open_ended ? " - until you end it" : ""}${p.warnings?.length ? " - \u26a0 checks at the start" : ""}`);
        if (p.error) { o.disabled = true; o.title = p.error; }
        else if (p.warnings?.length) o.title = p.warnings.join("\n");
        sel.append(o);
      }
      if (keep) sel.value = keep;
    } catch (e) { state.plansLoaded = false; }
  }

  async function act(path, body, label) {
    const note = $("#action");
    note.dataset.sticky = "";
    try {
      await api(path, body);
    } catch (e) {
      note.dataset.sticky = "1";
      say(note, `${label}: ${e.message}`, true);
    }
  }

  $("#start").addEventListener("click", () => act("/api/run", { plan: $("#plan").value }, "Start"));
  $("#pause").addEventListener("click", () => act("/api/pause", {}, "Pause"));
  $("#resume").addEventListener("click", () => act("/api/resume", {}, "Resume"));
  $("#end").addEventListener("click", () => {
    if (confirm("End the run? Each instrument's shutdown runs (outputs off, pumping it started stopped).")) act("/api/end", {}, "End");
  });

  // The command box: the same words as the cast tab. Suggestions come from the
  // server, which asks the instruments' own declared commands what may follow.
  let hintSeq = 0, hintTimer = null;

  function words() {
    const text = $("#cmd").value;
    const parts = text.trim().split(/\s+/).filter(Boolean);
    const complete = text === "" || /\s$/.test(text);
    return { done: complete ? parts : parts.slice(0, -1), prefix: complete ? "" : (parts[parts.length - 1] || "").toLowerCase() };
  }

  async function updateHints() {
    const { done, prefix } = words();
    const mine = ++hintSeq;
    let r;
    try { r = await api("/api/complete?words=" + encodeURIComponent(done.join(" "))); } catch (e) { return; }
    if (mine !== hintSeq) return;
    const box = $("#cmd-hints");
    box.replaceChildren();
    const options = r.options.filter((o) => o.kind !== "word" || o.text.toLowerCase().startsWith(prefix));
    for (const o of options.slice(0, 40)) {
      if (o.kind === "word") {
        const b = el("button", { type: "button", title: o.help || "", class: "tok " + (done.length ? "kw" : "verb"),
                                 "data-fam": famOf(done.length ? done[0] : o.text) }, o.text);
        b.addEventListener("click", () => {
          $("#cmd").value = done.concat(o.text).join(" ") + " ";
          $("#cmd").focus();
          updateHints();
        });
        box.append(b);
      } else {
        const lim = o.lo !== null || o.hi !== null ? ` ${o.lo ?? ""}..${o.hi ?? ""}` : "";
        box.append(el("span", { class: "tok value", title: o.help || "" }, `${o.text}${lim}${o.unit ? " " + unitText(o.unit) : ""}`));
      }
    }
    if (!options.length && done.length) box.append(el("span", { class: "note" }, "Nothing more to add: press Send."));
    // The help card for what is typed, its prerequisites checked against the chamber now.
    const typed = $("#cmd").value.trim().split(/\s+/).filter(Boolean);
    let info = { cards: [] };
    if (typed.length) { try { info = await api("/api/describe", { words: typed }); } catch (e) { /* keep the hints */ } }
    if (mine === hintSeq && window.InfoCard) window.InfoCard.render($("#cmd-info"), info, "live");
  }

  $("#cmd").addEventListener("input", () => { clearTimeout(hintTimer); hintTimer = setTimeout(updateHints, 150); });
  $("#cmd").addEventListener("focus", updateHints);

  $("#cmd-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const line = $("#cmd").value.trim();
    const out = $("#cmd-result");
    if (!line) return;
    try {
      const r = await api("/api/cast", { line });
      say(out, `${r.label} <- ${JSON.stringify(r.request)}  ${r.text}`, !r.ok);
      if (r.ok) { $("#cmd").value = ""; updateHints(); }
    } catch (e) {
      say(out, e.message, true);
    }
  });

  async function pollStatus() {
    if (state.view !== "status") return;
    try {
      if (!document.hidden) renderStatus(await api("/api/status?log=40"));
    } catch (e) { /* login screen takes over on a 401; otherwise try again */ }
    if (state.view === "status") state.timer = setTimeout(pollStatus, 1000);
  }

  // --- the chamber -------------------------------------------------------------------

  function renderTvac(s) {
    const T = window.TvacView, b = s.blocks.hvc;
    const banner = $("#tvac-banner");
    if (!b) { banner.className = "banner stopped"; banner.textContent = "No chamber data yet: the hvc block is missing."; return; }
    const vm = T.viewModel(b.status, $("#tvac-unit").value);
    banner.className = "banner " + (b.live ? "running" : "stopped");
    banner.textContent = b.live
      ? `Chamber live, updated ${fmtAge(b.age_s)} ago.` + (vm.mode ? ` Mode ${vm.mode}.` : "") + (vm.testStatus ? ` ${vm.testStatus}.` : "")
      : `Not live (${b.reason}). Showing the last values seen, ${fmtAge(b.age_s)} old, not current readings.`;
    $("#tvac-fault").textContent = vm.faults ? `Fault${vm.severity && vm.severity !== "N" ? " (severity " + vm.severity + ")" : ""}: ${vm.faults}` : "";
    $("#tvac-info").textContent = (vm.recipe !== null ? `Recipe ${vm.recipe}${vm.recipeStep ? ", step " + vm.recipeStep : ""}. ` : "")
      + "Heater output, turbo speed, foreline pressure and each zone's own on/off are not reported by the controller.";
    T.render($("#tvac-svg"), vm, b.live);
    const box = $("#tvac-sensors");
    box.className = "sensors" + (b.live ? "" : " stale");
    box.replaceChildren(...vm.sensors.map((x) => {
      const d = el("div");
      d.append(el("span", {}, x.name), el("span", {}, T.fmtTemp(x.value, vm.unit)));
      return d;
    }));
  }

  async function pollTvac() {
    if (state.view !== "tvac") return;
    try {
      if (!document.hidden) renderTvac(await api("/api/status?log=1"));
    } catch (e) { /* the login screen takes over on a 401; otherwise try again */ }
    if (state.view === "tvac") state.timer = setTimeout(pollTvac, 1000);
  }
  $("#tvac-unit").addEventListener("change", () => { clearTimeout(state.timer); pollTvac(); });

  // --- plots -------------------------------------------------------------------------

  const colorOf = (name) => {
    const i = state.run ? state.run.columns.findIndex((c) => c.name === name) : 0;
    return C.PALETTE[Math.max(0, i) % C.PALETTE.length];
  };

  async function loadRuns() {
    const r = await api("/api/runs");
    state.runs = r.runs;
    const sel = $("#run"), keep = state.run && state.run.id;
    sel.replaceChildren();
    for (const run of r.runs) {
      sel.append(el("option", { value: run.id },
        `${run.name} · ${new Date(run.started * 1000).toLocaleString()}${run.live ? " · live" : ""}`));
    }
    $("#unsupported").textContent = r.unsupported.length
      ? `${r.unsupported.length} other file(s) in the output folder use an older format and are not listed.` : "";
    if (!r.runs.length) {
      state.run = null;
      $("#run-info").textContent = "No recorded runs yet. Start a plan and its CSV appears here.";
      buildVars(); drawAll();
      return;
    }
    sel.value = r.runs.some((x) => x.id === keep) ? keep : r.runs[0].id;
    if (!state.run || state.run.id !== sel.value) runChanged(); else state.run = r.runs.find((x) => x.id === sel.value);
  }

  function defaultSelection(run) {
    const want = ["chamberP", "platenT", "shroudT", "TC01"];
    const names = run.columns.map((c) => c.name);
    const pick = want.filter((n) => names.includes(n));
    return new Set(pick.length ? pick : names.slice(0, 3));
  }

  function runChanged() {
    state.run = state.runs.find((r) => r.id === $("#run").value) || null;
    state.x0 = state.x1 = null;
    state.selected = state.run ? defaultSelection(state.run) : new Set();
    $("#run-info").textContent = state.run
      ? `${state.run.columns.length} variables, ${(state.run.bytes / 1024).toFixed(0)} KB, ${state.run.parts.length} file(s)` : "";
    buildVars();
    loadSeries();
  }

  function buildVars() {
    const box = $("#vars");
    box.replaceChildren();
    if (!state.run) return;
    const filter = $("#var-filter").value.trim().toLowerCase();
    const groups = {};
    for (const c of state.run.columns) {
      if (filter && !c.name.toLowerCase().includes(filter)) continue;
      (groups[c.unit || "state"] = groups[c.unit || "state"] || []).push(c);
    }
    for (const [unit, cols] of Object.entries(groups)) {
      box.append(el("h4", {}, unit));
      for (const c of cols) {
        const label = el("label");
        const cb = el("input", { type: "checkbox" });
        cb.checked = state.selected.has(c.name);
        cb.addEventListener("change", () => {
          if (cb.checked) state.selected.add(c.name); else state.selected.delete(c.name);
          loadSeries();
        });
        const dot = el("span", { class: "dot" });
        dot.style.background = colorOf(c.name);
        label.append(cb, dot, el("span", {}, c.name));
        box.append(label);
      }
    }
  }

  async function loadSeries() {
    if (!state.run || !state.selected.size) { state.data = null; drawAll(); return; }
    const names = [...state.selected].map(encodeURIComponent).join(",");
    try {
      state.data = await api(`/api/runs/${encodeURIComponent(state.run.id)}?vars=${names}&max=2000`);
    } catch (e) { return; }
    drawAll();
  }

  function groupsOf(data) {
    const groups = {};
    for (const name of Object.keys(data.series)) {
      const unit = data.units[name];
      const shown = C.displayUnit(unit, state.tempUnit) || "state";
      (groups[shown] = groups[shown] || []).push({
        name, color: colorOf(name), values: data.series[name].map((v) => C.convert(v, unit, state.tempUnit)),
      });
    }
    return groups;
  }

  function fullRange() {
    const t = state.data.t;
    return t.length > 1 ? [t[0], t[t.length - 1]] : [(t[0] || 0) - 30, (t[0] || 0) + 30];
  }
  const range = () => (state.x0 === null ? fullRange() : [state.x0, state.x1]);

  function drawAll() {
    const holder = $("#charts");
    if (!state.data || !state.data.t.length) {
      holder.replaceChildren(el("p", { class: "note" }, state.run
        ? (state.selected.size ? "No rows recorded yet." : "Pick variables on the left. Wheel zooms, drag pans, double-click resets.")
        : "No run to show."));
      return;
    }
    const groups = groupsOf(state.data);
    const keys = Object.keys(groups);
    const have = [...holder.querySelectorAll("canvas")].map((c) => c.dataset.unit);
    if (have.join("|") !== keys.join("|")) {
      holder.replaceChildren(...keys.map((k) => {
        const canvas = el("canvas", { "data-unit": k });
        wireChart(canvas);
        return canvas;
      }));
    }
    const css = getComputedStyle(document.documentElement);
    const theme = { fg: css.getPropertyValue("--muted").trim() || "#666",
                    grid: css.getPropertyValue("--grid").trim() || "#ddd",
                    bg: css.getPropertyValue("--bg").trim() || "#fff" };
    const [x0, x1] = range();
    for (const canvas of holder.querySelectorAll("canvas")) {
      C.drawChart(canvas, { t: state.data.t, lines: groups[canvas.dataset.unit], x0, x1,
                            unit: canvas.dataset.unit, hoverT: state.hoverT, theme });
    }
  }

  function redraw() {
    if (state.frame) return;
    state.frame = requestAnimationFrame(() => { state.frame = 0; drawAll(); });
  }

  function timeAt(canvas, clientX) {
    const r = canvas.getBoundingClientRect(), m = C.MARGIN;
    const [x0, x1] = range();
    const f = (clientX - r.left - m.l) / (r.width - m.l - m.r);
    return x0 + Math.max(0, Math.min(1, f)) * (x1 - x0);
  }

  function wireChart(canvas) {
    canvas.addEventListener("pointermove", (ev) => {
      if (state.drag) {
        const [x0, x1] = range(), r = canvas.getBoundingClientRect(), m = C.MARGIN;
        const dx = -((ev.clientX - state.drag) / (r.width - m.l - m.r)) * (x1 - x0);
        state.drag = ev.clientX;
        [state.x0, state.x1] = C.panRange(x0, x1, dx, fullRange()[0], fullRange()[1]);
      }
      state.hoverT = timeAt(canvas, ev.clientX);
      redraw();
    });
    canvas.addEventListener("pointerdown", (ev) => { state.drag = ev.clientX; canvas.setPointerCapture(ev.pointerId); });
    canvas.addEventListener("pointerup", () => { state.drag = null; });
    canvas.addEventListener("pointerleave", () => { state.hoverT = null; redraw(); });
    canvas.addEventListener("dblclick", () => { state.x0 = state.x1 = null; redraw(); });
    canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const [x0, x1] = range(), [lo, hi] = fullRange();
      [state.x0, state.x1] = C.zoomRange(x0, x1, timeAt(canvas, ev.clientX), ev.deltaY > 0 ? 1.25 : 0.8, lo, hi);
      redraw();
    }, { passive: false });
  }

  $("#run").addEventListener("change", runChanged);
  $("#var-filter").addEventListener("input", buildVars);
  $("#tempunit").addEventListener("change", () => { state.tempUnit = $("#tempunit").value; drawAll(); });
  $("#reset").addEventListener("click", () => { state.x0 = state.x1 = null; drawAll(); });
  $("#live").addEventListener("change", () => {
    clearInterval(state.plotTimer);
    state.plotTimer = $("#live").checked ? setInterval(() => { if (!document.hidden) loadSeries(); }, 5000) : null;
  });
  window.addEventListener("resize", redraw);

  function download(name, blob) {
    const a = el("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  $("#png").addEventListener("click", () => {
    const canvases = [...document.querySelectorAll("#charts canvas")];
    if (!canvases.length) return;
    const out = el("canvas");
    out.width = Math.max(...canvases.map((c) => c.width));
    out.height = canvases.reduce((n, c) => n + c.height, 0);
    const ctx = out.getContext("2d");
    let y = 0;
    for (const c of canvases) { ctx.drawImage(c, 0, y); y += c.height; }
    out.toBlob((blob) => download(`${state.run.id}.png`, blob));
  });

  $("#csv").addEventListener("click", async () => {
    if (!state.run || !state.selected.size) return;
    const names = [...state.selected];
    const d = await api(`/api/runs/${encodeURIComponent(state.run.id)}?vars=${names.map(encodeURIComponent).join(",")}&max=20000`);
    const head = ["timestamp", ...names.map((n) => `${n} [${C.displayUnit(d.units[n], state.tempUnit)}]`)];
    const rows = d.t.map((t, i) => [new Date(t * 1000).toISOString(),
      ...names.map((n) => { const v = C.convert(d.series[n][i], d.units[n], state.tempUnit); return v === null ? "" : v; })]);
    download(`${state.run.id}.csv`, new Blob([[head, ...rows].map((r) => r.join(",")).join("\n") + "\n"], { type: "text/csv" }));
  });

  // --- start -------------------------------------------------------------------------

  // Is this the demo server? Asked before login, so the login page can say so.
  async function showDemo() {
    try {
      const { demo } = await (await fetch("/api/info")).json();
      $("#demo-bar").hidden = !demo;
      $("#login-demo").hidden = !demo;
      if (demo) document.title = "DEMO - formsLabCLI";
    } catch (e) { /* the page works without it */ }
  }
  showDemo();

  api("/api/me").then((r) => startApp(r.user)).catch(() => showLogin());
})();
