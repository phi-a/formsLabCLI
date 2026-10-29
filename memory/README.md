# formsLabCLI memory

Everything known about formsLabCLI and the bench it runs: what it is for, how to use
it, how it is built, the rules that keep people and the chamber safe, and where the
work stands. It is the canon. A question about formsLabCLI should be answerable from
here; if it is not, the answer belongs here.

It is an [Obsidian](https://obsidian.md) vault: open this folder as a vault. The
links are plain relative markdown links, so it reads the same on GitHub and in an
editor.

## Start here

- **New to the bench:** [safety](rules/safety.md), then [setup](use/setup.md), then
  the practice [session](use/session.md).
- **A question:** [questions](questions.md), then the map below.
- **Before working on the code:** [project](project.md), then [progress](progress.md).

## Map

| Folder | Holds |
|---|---|
| [rules/](rules/) | The rule sets: [safety](rules/safety.md), operating the chamber ([tvac](rules/tvac.md)), talking to the controller ([hvc](rules/hvc.md)), controller and test [recipes](rules/recipes.md) |
| [use/](use/) | How to use it: [setup](use/setup.md), writing and running [plans](use/plans.md), the [gui](use/gui.md), the practice [session](use/session.md), [orbit](use/orbit.md) files, the NASA [gevs](use/gevs.md) test |
| [instruments/](instruments/) | Each instrument as it is at this bench: the [laco-chamber](instruments/laco-chamber.md), its [hvc3500](instruments/hvc3500.md) controller, the [cryocooler](instruments/cryocooler.md) board, the [powerswitch](instruments/powerswitch.md), the wired [network](instruments/network.md) that joins them |
| [design/](design/) | How it is built and why: [architecture](design/architecture.md), [end](design/end.md) (the End button), [gui-design](design/gui-design.md), [writing](design/writing.md) |
| [sources/](sources/README.md) | The manuals and records the notes cite |
| top level | [project](project.md) (intent and principles), [progress](progress.md) (where the work stands), [questions](questions.md) |

## Keeping it

The vault is only worth anything if it stays true.

1. **A change and its note land together.** A pull request that changes behaviour
   changes the note that describes it. Code comments point here
   (`memory/use/plans.md`), not to a copy.
2. **Rules say who holds them** (the controller, formsLAB, or the operator) and cite
   their source: a manual's page, a file, or a dated finding at the bench.
3. **Unknowns stay unknown.** What has not been run on the real chamber says so.
   Guesses are marked as guesses until the bench settles them.
4. **Found at the bench, written here.** A finding goes into the note it changes,
   with its date; a question asked twice goes into [questions](questions.md).
5. **Decisions keep their reason.** A decision goes into the note it governs (End's
   are D1 to D11 in [end](design/end.md)) and into [progress](progress.md).
6. **One home per fact.** Link to it rather than copy it. The rule notes gather
   rules from elsewhere; when the source changes, change the rule note too.
7. **Written for the operator at the bench**, often a student, in the author's lens:
   the clearest sufficient form ([writing](design/writing.md)).
8. When a pull request merges, update [progress](progress.md): *Now*, a line in
   *Done*, settled questions out of *Waiting on a decision*.
