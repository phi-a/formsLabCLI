// The help card for a command or a plan step: what it does, what its inputs
// mean, and what must be true before it runs. The plan editor shows it for the
// row you are on (prerequisites as this plan leaves them there); the command box
// for what you are typing (prerequisites as the chamber is now). Server text goes
// on the page as text only.
(function () {
  "use strict";
  const { el, unitText } = window.App;
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

  function input(i) {
    if (i.kind === "choice") {
      return h("li", {}, el("code", {}, i.name ? `<${i.name}>` : i.choices.join("|")),
               el("span", {}, ` one of ${i.choices.join(", ")}`));
    }
    const lim = i.lo !== null && i.hi !== null ? `${i.lo} to ${i.hi}`
      : i.lo !== null ? `at least ${i.lo}` : i.hi !== null ? `at most ${i.hi}` : "";
    const unit = i.unit ? ` ${unitText(i.unit)}` : "";
    return h("li", {}, el("code", {}, `<${i.name}>`),
             el("span", {}, ` ${KINDS[i.kind] || i.kind}${lim ? ", " + lim + unit : unit}`));
  }

  function render(box, info, mode) {
    box.replaceChildren();
    const cards = (info && info.cards) || [];
    box.hidden = !cards.length;
    if (!cards.length) return;
    if (cards.length > 1) {                                  // the line begins several commands
      box.append(el("div", { class: "title" }, "This can go on as:"));
      box.append(h("ul", { class: "begun" }, ...cards.map((c) =>
        h("li", {}, el("code", {}, c.usage), el("span", { class: "muted" }, ` ${c.help}`)))));
      return;
    }
    const c = cards[0];
    box.append(el("code", { class: "usage" }, c.usage), el("div", { class: "title" }, c.help));
    if (c.details) for (const p of c.details.split("\n\n")) box.append(el("p", { class: "details" }, p));
    if (c.inputs.length) box.append(el("h4", {}, "Inputs"), h("ul", {}, ...c.inputs.map(input)));
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
