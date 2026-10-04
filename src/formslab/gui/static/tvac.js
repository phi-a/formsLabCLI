// The chamber view: the HVC-3500's own Manual screen, redrawn from the `hvc`
// CAST block. `viewModel` (pure; tested under Node) turns the block into what is
// drawn; `render` draws it as SVG. What the block cannot say is shown as "n/a"
// rather than guessed: heater output %, turbo speed %, foreline pressure, and
// which zones are switched on (the controller reports one chamber-wide
// "holding temperature" state). Server text goes on the page as text only.
(function (root) {
  "use strict";

  const VALVES = ["vent", "fill", "rough", "foreline", "gate"];
  const PUMPS = ["pump", "turbo"];
  const ZONE_TITLES = { platen: "Platen (Cntrl P)", shroud: "Shroud (Cntrl S)", t2: "t2 (monitor)" };

  const state = (v, on, off) => (v === true ? on : v === false ? off : "unknown");

  function temp(v, unit) {
    return v === null || v === undefined ? null : unit === "K" ? v + 273.15 : v;
  }

  // The `hvc` block (temperatures in C, as CAST holds them) -> what to draw.
  function viewModel(status, tempUnit) {
    const s = status || {};
    const unit = tempUnit === "K" ? "K" : "C";
    const zoneNames = Object.keys(s).map((k) => /^(.+) setpoint C$/.exec(k)).filter(Boolean).map((m) => m[1]);
    const zones = zoneNames.map((n) => ({ key: n, title: ZONE_TITLES[n] || n, temp: temp(s[n + " C"], unit),
                                          setpoint: temp(s[n + " setpoint C"], unit) }));
    if ("t2 C" in s && !zoneNames.includes("t2")) {
      zones.push({ key: "t2", title: ZONE_TITLES.t2, temp: temp(s["t2 C"], unit), setpoint: null });
    }
    const taken = new Set(zones.map((z) => z.key));
    const sensors = Object.keys(s)
      .map((k) => /^(.+) C$/.exec(k)).filter((m) => m && !taken.has(m[1]) && !/ setpoint$/.test(m[1]))
      .map((m) => ({ name: m[1], value: temp(s[m[1] + " C"], unit) }));
    const faults = typeof s.faults === "string" && s.faults !== "none" && s.faults !== "" ? s.faults : "";
    return {
      connected: s.connected === true, unit,
      mode: s.mode ?? null, testStatus: s.test_status ?? null,
      pressure: { value: s.pressure ?? null, unit: s.pressure_unit || "" },
      vacuumSetpoint: s.vacuum_setpoint ?? null, recipe: s.recipe ?? null, recipeStep: s.recipe_step ?? null,
      holding: s.thermal_control === true ? true : s.thermal_control === false ? false : null,
      faults, severity: s.fault_severity ?? null,
      valves: Object.fromEntries(VALVES.map((v) => [v, state(s[v], "open", "closed")])),
      pumps: Object.fromEntries(PUMPS.map((p) => [p, state(s[p], "on", "off")])),
      zones, sensors,
    };
  }

  function fmtPressure(v, unit) {
    if (v === null || v === undefined || Number.isNaN(v)) return "-";
    const a = Math.abs(v);
    const text = a >= 100 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : a >= 0.001 ? v.toFixed(4) : v === 0 ? "0" : v.toExponential(2);
    return unit ? `${text} ${unit}` : text;
  }
  const fmtTemp = (v, unit) => (v === null || v === undefined ? "-" : `${v.toFixed(1)} ${unit === "K" ? "K" : "°C"}`);

  // --- drawing -------------------------------------------------------------------------

  const NS = "http://www.w3.org/2000/svg";
  function node(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const tip = (e, text) => { e.append(node("title", {}, text)); return e; };

  function render(svg, vm, live) {
    svg.replaceChildren();
    const css = getComputedStyle(document.documentElement);
    const c = { fg: css.getPropertyValue("--fg").trim() || "#222", muted: css.getPropertyValue("--muted").trim() || "#777",
                line: css.getPropertyValue("--line").trim() || "#ccc", panel: css.getPropertyValue("--panel").trim() || "#f6f8fa",
                ok: css.getPropertyValue("--ok").trim() || "#1a7f37", bad: css.getPropertyValue("--bad").trim() || "#cf222e",
                warn: css.getPropertyValue("--warn").trim() || "#9a6700", accent: css.getPropertyValue("--accent").trim() || "#0969da" };
    const g = node("g", { class: live ? "live" : "stale", opacity: live ? "1" : "0.5" });
    svg.append(g);
    const pipe = (d, w) => g.append(node("path", { d, fill: "none", stroke: c.muted, "stroke-width": w || 6, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    const text = (x, y, s, size, fill, anchor, weight) => g.append(node("text", {
      x, y, "font-size": size || 12, fill: fill || c.fg, "text-anchor": anchor || "middle",
      "font-weight": weight || "400", "font-family": "system-ui, sans-serif" }, s));

    // pipes first, so the parts sit on top of them
    pipe("M115 125 H225"); pipe("M115 215 H225");                                  // vent, fill into the chamber
    pipe("M565 125 H605"); pipe("M635 125 H690 V190");                              // chamber -> gate -> turbo
    pipe("M690 230 V415 H615");                                                    // turbo -> foreline valve
    pipe("M585 415 H500"); pipe("M500 330 V355"); pipe("M500 385 V430");           // foreline and chamber -> vacuum pump

    // the chamber
    g.append(node("rect", { x: 225, y: 95, width: 340, height: 235, rx: 6, fill: c.panel, stroke: c.fg, "stroke-width": 3 }));
    text(395, 138, fmtPressure(vm.pressure.value, vm.pressure.unit), 28, c.fg, "middle", "600");
    text(395, 156, vm.vacuumSetpoint === null ? "" : `setpoint ${fmtPressure(vm.vacuumSetpoint, vm.pressure.unit)}`, 11, c.muted);

    // zones inside it
    const xs = [240, 332, 424];
    vm.zones.slice(0, 3).forEach((z, i) => {
      const x = xs[i], y = 170;
      g.append(tip(node("rect", { x, y, width: 84, height: 148, rx: 4, fill: "none", stroke: c.line, "stroke-width": 1.5 }),
        `${z.title}. The controller does not report heater output or this zone's own on/off.`));
      text(x + 42, y + 18, z.title.split(" (")[0], 12, c.muted, "middle", "600");
      text(x + 42, y + 56, fmtTemp(z.temp, vm.unit), 17, c.fg, "middle", "600");
      text(x + 42, y + 78, z.setpoint === null ? "" : `set ${fmtTemp(z.setpoint, vm.unit)}`, 11, c.muted);
      text(x + 42, y + 108, "heater n/a", 11, c.muted);
      text(x + 42, y + 128, vm.holding === null ? "control n/a" : vm.holding ? "holding" : "not holding", 11,
        vm.holding ? c.ok : c.muted);
    });

    // valves and pumps
    // `side`: where the label goes -- below the part, or to its right (off a pipe that runs through it)
    const part = (x, y, label, st, side) => {
      const colour = st === "open" || st === "on" ? c.ok : st === "unknown" ? c.muted : c.bad;
      const grp = node("g", { "data-part": label });
      grp.append(node("circle", { cx: x, cy: y, r: 15, fill: colour, stroke: c.panel, "stroke-width": 2 }));
      grp.append(node("text", { x, y: y + 5, "text-anchor": "middle", "font-size": 16, "font-weight": "700", fill: "#fff",
                                "font-family": "system-ui, sans-serif" }, st === "unknown" ? "?" : st === "open" || st === "on" ? "✓" : "✕"));
      grp.append(node("title", {}, `${label}: ${st}`));
      g.append(grp);
      const stateColour = st === "open" || st === "on" ? c.ok : c.muted;
      if (side === "right") {
        text(x + 24, y - 1, label, 12, c.fg, "start");
        text(x + 24, y + 13, st === "unknown" ? "" : st, 11, stateColour, "start");
      } else {
        text(x, y + 32, label, 12, c.fg);
        text(x, y + 45, st === "unknown" ? "" : st, 11, stateColour);
      }
    };
    part(100, 125, "Vent Valve", vm.valves.vent);
    part(100, 215, "Fill Valve", vm.valves.fill);
    part(620, 125, "Gate Valve", vm.valves.gate);
    part(690, 210, "Turbo Pump", vm.pumps.turbo, "right");
    text(714, 241, "speed n/a", 11, c.muted, "start");
    part(500, 370, "Vacuum Valve", vm.valves.rough, "right");
    part(600, 415, "Foreline Valve", vm.valves.foreline);
    text(600, 473, "pressure n/a", 11, c.muted);
    part(500, 445, "Vacuum Pump", vm.pumps.pump);

    text(280, 355, "LN2 DEWAR", 11, c.muted);
    g.append(node("rect", { x: 232, y: 340, width: 96, height: 22, rx: 3, fill: "none", stroke: c.line }));
  }

  const api = { viewModel, fmtPressure, fmtTemp, render };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TvacView = api;
})(typeof window !== "undefined" ? window : globalThis);
