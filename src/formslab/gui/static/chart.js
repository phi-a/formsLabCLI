// A small line chart on a canvas: nice axes, gaps for missing values, a hover
// readout. The helpers up top are pure (and tested under Node); drawChart is
// the only part that touches a canvas.
(function (root) {
  "use strict";

  const PALETTE = ["#2f81f7", "#e5534b", "#2da44e", "#d29922", "#a371f7",
                   "#1b9aaa", "#e16f9b", "#8b949e", "#f0883e", "#56d364"];
  const TIME_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
                      3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];

  function niceStep(range, target) {
    const raw = range / Math.max(1, target);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / mag;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
  }

  // Round tick values covering [min, max], about `target` of them.
  function niceTicks(min, max, target) {
    if (!(max > min)) return { ticks: [min], step: 1 };
    const step = niceStep(max - min, target);
    const ticks = [];
    for (let k = Math.ceil(min / step - 1e-9); k * step <= max + step * 1e-9; k++) ticks.push(k * step);
    return { ticks, step };
  }

  // Tick times (epoch seconds) at round local clock values.
  function timeTicks(t0, t1, target) {
    const span = t1 - t0;
    const step = TIME_STEPS.find((s) => span / s <= target) || TIME_STEPS[TIME_STEPS.length - 1];
    const off = -new Date(t0 * 1000).getTimezoneOffset() * 60;
    const ticks = [];
    for (let t = Math.ceil((t0 + off) / step) * step - off; t <= t1; t += step) ticks.push(t);
    return { ticks, step };
  }

  const pad = (n) => String(n).padStart(2, "0");

  function formatClock(t, step) {
    const d = new Date(t * 1000);
    if (step >= 86400) return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return step >= 60 ? hm : `${hm}:${pad(d.getSeconds())}`;
  }

  function formatDateTime(t) {
    const d = new Date(t * 1000);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
           `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // A tick or reading, with as many decimals as its step needs.
  function formatNumber(v, step) {
    if (v === null || v === undefined || Number.isNaN(v)) return "";
    if (v !== 0 && (Math.abs(v) >= 1e6 || Math.abs(v) < 1e-3)) return v.toExponential(2);
    const decimals = step ? Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9))) : 3;
    return v.toFixed(decimals);
  }

  // A K column shown in C when asked; everything else as recorded.
  function displayUnit(unit, tempUnit) {
    return unit === "K" && tempUnit === "C" ? "C" : unit;
  }
  function convert(v, unit, tempUnit) {
    return v !== null && unit === "K" && tempUnit === "C" ? v - 273.15 : v;
  }

  // Index of the sample nearest x in the sorted array ts (-1 when empty).
  function nearestIndex(ts, x) {
    if (!ts.length) return -1;
    let lo = 0, hi = ts.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (ts[mid] < x) lo = mid; else hi = mid;
    }
    return Math.abs(ts[lo] - x) <= Math.abs(ts[hi] - x) ? lo : hi;
  }

  // [min, max] of the values in lines that fall inside [x0, x1], or null.
  function visibleExtent(ts, lines, x0, x1) {
    let min = Infinity, max = -Infinity;
    for (const line of lines) {
      for (let i = 0; i < ts.length; i++) {
        const v = line.values[i];
        if (v === null || ts[i] < x0 || ts[i] > x1) continue;
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    return min <= max ? [min, max] : null;
  }

  // Zoom [x0, x1] by `factor` (<1 zooms in) about x, kept inside [lo, hi].
  function zoomRange(x0, x1, x, factor, lo, hi) {
    const span = Math.min(hi - lo, Math.max(1, (x1 - x0) * factor));
    const frac = (x - x0) / (x1 - x0);
    let a = x - frac * span;
    a = Math.max(lo, Math.min(a, hi - span));
    return [a, a + span];
  }

  // Slide [x0, x1] by dx seconds, kept inside [lo, hi].
  function panRange(x0, x1, dx, lo, hi) {
    const span = x1 - x0;
    const a = Math.max(lo, Math.min(x0 + dx, hi - span));
    return [a, a + span];
  }

  // spec: { t, lines: [{name, color, values}], x0, x1, unit, hoverT, theme: {fg, grid, bg} }
  function drawChart(canvas, spec) {
    const dpr = root.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const { fg, grid, bg } = spec.theme;
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);
    ctx.font = "11px system-ui, sans-serif";

    const m = MARGIN;
    const pw = w - m.l - m.r, ph = h - m.t - m.b;
    const ext = visibleExtent(spec.t, spec.lines, spec.x0, spec.x1);
    ctx.fillStyle = fg;
    ctx.textBaseline = "alphabetic";
    ctx.fillText(spec.unit || "state", 6, 11);
    if (!ext) {
      ctx.fillText("no data in view", m.l + 8, m.t + 20);
      return;
    }
    let [y0, y1] = ext;
    if (y1 - y0 < 1e-9) { y0 -= 1; y1 += 1; }
    const padY = (y1 - y0) * 0.06;
    y0 -= padY; y1 += padY;
    const X = (t) => m.l + ((t - spec.x0) / (spec.x1 - spec.x0)) * pw;
    const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * ph;

    ctx.strokeStyle = grid;
    ctx.lineWidth = 1;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const yt = niceTicks(y0, y1, Math.max(2, Math.floor(ph / 40)));
    for (const v of yt.ticks) {
      const y = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(m.l, y); ctx.lineTo(w - m.r, y); ctx.stroke();
      ctx.fillStyle = fg;
      ctx.fillText(formatNumber(v, yt.step), m.l - 6, y);
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const xt = timeTicks(spec.x0, spec.x1, Math.max(2, Math.floor(pw / 90)));
    for (const t of xt.ticks) {
      const x = Math.round(X(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, m.t); ctx.lineTo(x, m.t + ph); ctx.stroke();
      ctx.fillStyle = fg;
      ctx.fillText(formatClock(t, xt.step), x, m.t + ph + 5);
    }

    ctx.save();
    ctx.beginPath(); ctx.rect(m.l, m.t, pw, ph); ctx.clip();
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    for (const line of spec.lines) {
      ctx.strokeStyle = line.color;
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < spec.t.length; i++) {
        const v = line.values[i];
        if (v === null) { pen = false; continue; }
        const x = X(spec.t[i]), y = Y(v);
        if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
      }
      ctx.stroke();
    }
    ctx.restore();

    if (spec.hoverT !== null && spec.hoverT !== undefined) {
      const i = nearestIndex(spec.t, spec.hoverT);
      if (i >= 0 && spec.t[i] >= spec.x0 && spec.t[i] <= spec.x1) {
        const x = Math.round(X(spec.t[i])) + 0.5;
        ctx.strokeStyle = fg; ctx.globalAlpha = 0.5;
        ctx.beginPath(); ctx.moveTo(x, m.t); ctx.lineTo(x, m.t + ph); ctx.stroke();
        ctx.globalAlpha = 1;
        const rows = [formatDateTime(spec.t[i])];
        for (const line of spec.lines) {
          const v = line.values[i];
          if (v !== null) {
            ctx.fillStyle = line.color;
            ctx.beginPath(); ctx.arc(X(spec.t[i]), Y(v), 3, 0, 6.2832); ctx.fill();
          }
          rows.push(`${line.name}  ${v === null ? "-" : formatNumber(v, (y1 - y0) / 200)}`);
        }
        const boxW = Math.max(...rows.map((r) => ctx.measureText(r).width)) + 12;
        const boxH = rows.length * 14 + 6;
        const bx = x + 10 + boxW > w ? x - 10 - boxW : x + 10;
        ctx.fillStyle = bg; ctx.globalAlpha = 0.92;
        ctx.fillRect(bx, m.t + 4, boxW, boxH);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = grid; ctx.strokeRect(bx + 0.5, m.t + 4.5, boxW, boxH);
        ctx.textAlign = "left"; ctx.textBaseline = "top";
        rows.forEach((r, k) => {
          ctx.fillStyle = k === 0 ? fg : spec.lines[k - 1].color;
          ctx.fillText(r, bx + 6, m.t + 8 + k * 14);
        });
      }
    }
  }

  const MARGIN = { l: 58, r: 12, t: 16, b: 22 };

  const api = { PALETTE, MARGIN, niceTicks, timeTicks, formatClock, formatDateTime, formatNumber,
                displayUnit, convert, nearestIndex, visibleExtent, zoomRange, panRange, drawChart };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Chart2D = api;
})(typeof window !== "undefined" ? window : globalThis);
