// The help card for a command or a plan step: what it does, what its inputs
// mean, and what must be true before it runs. The plan editor shows it for the
// row you are on (prerequisites as this plan leaves them there); the command box
// for what you are typing (prerequisites as the chamber is now). Server text goes
// on the page as text only.
(function () {
  "use strict";
  const { el, unitText, famOf, partIcon } = window.App;
  const h = (tag, attrs, ...kids) => { const e = el(tag, attrs); for (const k of kids) e.append(k); return e; };

  // What each status means, in a plan (static) and in the command box (live).
  const SAYS = {
    plan: { ok: "established by the plan", broken: "broken by the plan: it cannot run",
            unknown: "depends on the chamber at the start; checked when the step runs",
            live: "checked when the step runs" },
    live: { ok: "true now", broken: "not true now: the command would be refused",
            unknown: "not known now: the command would be refused", live: "checked when sent" },
  };
  const KINDS = { number: "a number", integer: "a whole number", text: "one word", rest: "free text" };
  const nice = (name) => name.charAt(0).toUpperCase() + name.slice(1).replace(/_/g, " ");
  const either = (xs) => (xs.length > 1 ? `${xs.slice(0, -1).join(", ")} or ${xs[xs.length - 1]}` : xs.join(""));

  // An input in words: "Temperature: a number from -180 to 200 °C", "Valve: rough, ... or gate".
  function input(i) {
    if (i.kind === "choice") return el("li", {}, `${nice(i.name)}: ${either(i.choices)}`);
    const unit = i.unit ? ` ${unitText(i.unit)}` : "";
    const lim = i.lo !== null && i.hi !== null ? ` from ${i.lo} to ${i.hi}${unit}`
      : i.lo !== null ? ` of at least ${i.lo}${unit}` : i.hi !== null ? ` of at most ${i.hi}${unit}` : unit;
    return el("li", {}, `${nice(i.name)}: ${KINDS[i.kind] || i.kind}${lim}`);
  }

  // The command as it is written, in the same tokens as the editor: words as pills,
  // a choice as its members, a value to type as a value box with its unit.
  function usageLine(c) {
    const words = c.words || [];
    const fam = famOf(words.length ? words[0].text : "");
    const line = el("div", { class: "tokline usage" });
    words.forEach((w, i) => {
      if (i === 1 && c.part) line.append(partIcon(c.part, fam));
      if (i === 0) line.append(el("span", { class: "tok verb", "data-fam": fam }, w.text));
      else if (w.role === "slot") {
        line.append(el("span", { class: "tok value" }, w.text.replace(/_/g, " ") + (w.unit ? " " + unitText(w.unit) : "")));
      } else if (w.role === "choice") {
        line.append(el("span", { class: "tok kw", "data-fam": fam, title: "one of: " + w.choices.join(", ") },
                       w.choices.length <= 5 ? w.choices.join(" | ") : w.text));
      } else line.append(el("span", { class: "tok kw", "data-fam": fam }, w.text));
    });
    return line;
  }

  function render(box, info, mode) {
    box.replaceChildren();
    const cards = (info && info.cards) || [];
    box.hidden = !cards.length;
    if (!cards.length) return;
    if (cards.length > 1) {                                  // the line begins several commands
      box.append(el("div", { class: "title" }, "This can go on as:"));
      box.append(h("ul", { class: "begun" }, ...cards.map((c) =>
        h("li", {}, usageLine(c), el("span", { class: "muted" }, c.help)))));
      return;
    }
    const c = cards[0];
    box.append(usageLine(c), el("div", { class: "title" }, c.help));
    if (c.details) for (const p of c.details.split("\n\n")) box.append(el("p", { class: "details" }, p));
    const named = c.inputs.filter((i) => i.name);         // an unnamed choice (on|off) is plain in the usage
    if (named.length) box.append(el("h4", {}, "Inputs"), h("ul", {}, ...named.map(input)));
    const rules = (info && info.rules) || [];
    if (!rules.length) return;
    const says = SAYS[mode] || SAYS.plan;
    box.append(el("h4", {}, mode === "live" ? "Needs first (now)" : "Needs first"));
    for (const r of rules) {
      box.append(h("ul", { class: "conds" }, ...r.conditions.map((k) =>
        el("li", { class: `cond ${k.status}`, title: says[k.status] || k.status }, k.text))));
      box.append(el("p", { class: "why" }, r.why));
    }
  }

  window.InfoCard = { render };
})();
