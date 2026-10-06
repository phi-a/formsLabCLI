# Progress

Where the work stands, what is next, and what is waiting on a decision. Update it
when a pull request merges or a decision is made: change *Now*, add a line to
*Done*, and move settled questions out of *Waiting on a decision*. Intent and
architecture are in [project.md](project.md); the GUI's plan is in [gui.md](gui.md).

## Now (2026-10-05)

- **On main:** everything through PR #9, merged 2026-10-05 (J2, loops, conditions,
  rename, the orbit-first check).
- **In progress:** nothing open.
- **Next:** the environment profile, the first stage after orbit files
  ([gui.md](gui.md), section 4): orbit file + spacecraft model → per-face background
  temperature over an orbit → a recorder-format CSV in the Plots tab. It needs a way to
  describe the spacecraft first.

## Done

| Date | PR | What |
|---|---|---|
| 2026-09-01 | | The lab console extracted from FORMS; config and state moved out of the package; the sequence host brought over |
| 2026-09-10 | #1, #2 | New-computer setup simplified; updating without losing local settings documented |
| 2026-09-17 | #3 | Rigol DP832A over USB on the Raspberry Pi |
| 2026-09-24 to 29 | #4 | LACO chamber support (HVC-3500 driver, `rTVAC_LACO`); the fixed-frame console; the rScripts runtime moved in, so LACO runs without FORMS |
| 2026-10-01 | #5 | The lab-only architecture: plans, ported rScripts, no FORMS runtime; vent and pumpdown scripts |
| 2026-10-02 | | One `tvac` mode, which is a plan; rScripts declare their cast commands; one thread per rScript; faster PSU reads |
| 2026-10-03 | #6 | `devices/` one folder per instrument; `.plan` files, one step per line; rScripts get a `Run`; the command grammar as data; `labcli <command>`; `formslab.orbit` added (orbit, view-factor and environment models); the GUI planned here |
| 2026-10-04 | | Shared files safe between processes; the GUI: login, status, plots, run control, the plan editor, the chamber view, the demo; commands declare their prerequisites (`RULES`), checked in plans and when sent; help cards |
| 2026-10-05 | #7 | One writing standard (docs/WRITING.md); part symbols in commands, cards and the chamber view; orbit files in the Plans tab with a live panel; one orbit stack (orbit files drive the models, numpy in base); roughing refused below the crossover; blocks; Edit and Ship; CI fixed on Windows |
| 2026-10-05 | #8 | rOrbit: a plan follows an orbit file and waits for its umbra; *Use in a plan* |
| 2026-10-05 | #9 | J2 drift, so sun-synchronous orbits keep their local time; one umbra reading for the panel and rOrbit; a wait on an orbit value before an orbit is chosen is refused; loops (`repeat … end`); conditions (`< <= > >=`, `= true`, `within`, `or go on`; `above`, `below`, `timeout` retired); rename; list symbols; CI runs the orbit extra |

## Waiting on a decision

From the GUI's open questions ([gui.md](gui.md)) and recent reviews:

1. **The spacecraft model:** how it is written (a file with its own grammar, like an
   orbit?). Blocks the environment profile.
2. **3D view:** from the start of the space-environment work, or after the profile?
   And with what, given no internet and no build step (plotly's `scene3d` is multi-MB)?
3. **rOrbit and Pause:** a paused run holds its steps, but the orbit goes on. Should a
   paused `orbit replay` hold the satellite too?
4. **`labcli check` for `.orbit` files:** the GUI checks them; the console does not yet.
5. **The umbra strip in dark mode:** its contrast is low.
6. **Students starting runs** from the GUI, or only writing plans and watching.
7. **Default temperature unit** in the GUI (Celsius today).
8. **The controller's unread values** (heater output %, turbo speed %, foreline
   pressure): worth finding?

## Known gaps

- **The turbo path has never been run** on the chamber (crossover, foreline,
  gate valve): [tvac-chamber.md](tvac-chamber.md).
- **PSU2** is disabled in the shipped hardware map, but rSLTA powers the camera from it;
  enable it in the live map before an sLTA run (docs/ARCHITECTURE.md, Known conflicts).
- **No replay of a FORMS profile** yet: orbit timing reaches the bench through rOrbit,
  but no plan step follows a temperature profile.
- **Plots lag** by the record cadence (30 s for `tvac`).
- **The models' sweep is one orbit at its epoch**, two-body within it; J2 moves the
  orbit between dates (`Orbit(elements.at(t))`), not within one sweep.

## How work lands

- Work happens on the `cleanup` branch and reaches `main` by pull request; CI runs the
  suite on Ubuntu and Windows (Python 3.11, with the `orbit` extra).
- Two Claude Code sessions have worked in parallel, the second in a separate worktree
  (`formsLabCLI-blocks`) for blocks, loops and conditions; its commits were
  fast-forwarded onto `cleanup`.
- Docs say what exists and change with the code; this notebook says why, and what is
  next.
