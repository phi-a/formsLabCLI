// Plan text as a list of lines: what a line is, where the two header lines go, and
// which elements an orbit file still lacks.
// A plan starts with `load`, then (optionally) `record`; everything else is a
// step. Those two are not the user's to place: asking for one puts it where it
// belongs, wherever the user happened to be, so deleting one is never a dead end.
// Pure, so it can be tested under Node.
(function (root) {
  "use strict";

  function kindOf(line) {
    const t = line.trim();
    if (!t) return "blank";
    if (t.startsWith("#")) return "comment";
    const head = t.split(/\s+/)[0].toLowerCase();
    return head === "load" ? "load" : head === "record" ? "record" : "step";
  }

  const indexOfKind = (lines, kind) => lines.findIndex((l) => kindOf(l) === kind);

  // Where a header line of this kind belongs: `load` before the first `record` or
  // step (after any comment banner); `record` straight after `load`.
  function headerIndex(lines, kind) {
    if (kind === "record") {
      const load = indexOfKind(lines, "load");
      if (load >= 0) return load + 1;
    }
    const first = lines.findIndex((l) => ["record", "step"].includes(kindOf(l)));
    if (first < 0) return lines.length;
    // Plans put one blank line between the comment banner and the header lines,
    // and one more between the header and the steps: of two blank lines above the
    // first step, the header takes the place between them.
    let blanks = 0;
    while (first - blanks - 1 >= 0 && kindOf(lines[first - blanks - 1]) === "blank") blanks++;
    return blanks >= 2 ? first - (blanks - 1) : first;
  }

  const DEFAULTS = (scripts) => ({
    load: "load " + (scripts || []).join(" ").trim(),
    record: "record every 10 s",
  });

  // `lines` with the header line added where it belongs; the same lines (and
  // index -1) when it is already there. Returns { lines, index }.
  function withHeader(lines, kind, scripts) {
    if (indexOfKind(lines, kind) >= 0) return { lines: lines.slice(), index: -1 };
    const at = headerIndex(lines, kind);
    const out = lines.slice();
    out.splice(at, 0, DEFAULTS(scripts)[kind].trimEnd());
    return { lines: out, index: at };
  }

  // Which header lines a plan lacks.
  const missingHeaders = (lines) => ["load", "record"].filter((k) => indexOfKind(lines, k) < 0);

  // An orbit file names each of its seven elements once, in any order: which it lacks.
  const ELEMENTS = ["epoch", "a", "e", "i", "raan", "argp", "nu"];
  const missingElements = (lines) => {
    const named = new Set(lines.map((l) => kindOf(l) === "step" ? l.trim().split(/\s+/)[0].toLowerCase() : ""));
    return ELEMENTS.filter((k) => !named.has(k));
  };

  const api = { kindOf, headerIndex, withHeader, missingHeaders, ELEMENTS, missingElements };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PlanText = api;
})(typeof window !== "undefined" ? window : globalThis);
