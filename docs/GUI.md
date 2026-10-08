# The GUI

A small web app for the bench machine, used from a browser. It shows what the
host is doing, starts and ends runs, sends commands, and plots recorded runs. It
is a client of the files the console and the sequence host already share; it
never opens an instrument.

```
labcli gui --set-login     # once: choose a user name and password
labcli gui                 # then open http://localhost:8080/
```

`--port N` changes the port. Stop it with Ctrl+C. Runs started from the console
or over SSH keep running either way.

To try it without the bench (a simulated chamber, a throwaway login and config, nothing real
touched): `python scripts/gui_demo.py`, then open `http://localhost:8088/` (user `demo`,
password `demo`). The demo has its own port and an orange DEMO bar, so it cannot be
mistaken for the bench's own GUI.

## From another machine

The server listens on this machine only. Reach it through an SSH tunnel:

```
ssh -L 8080:localhost:8080 <bench>
```

then open `http://localhost:8080/` on your own machine. `labcli gui --listen`
accepts connections from the network instead; the login then travels as plain
HTTP, so use it only on a network you trust.

## The login

`--set-login` stores a salted hash in `~/.formslab/gui.json` (private to the
account on Linux and Mac; Windows relies on the profile's permissions). Nothing
is in the source or the repo. A wrong password waits about a second. Sessions
last 8 hours and end when the server restarts. Logins and logouts are noted in
`~/.formslab/.run/gui.log`.

## Screens

**Status and control.** Whether a run is going (plan, since when, where its CSV
goes), one card per instrument, and the end of the host log. From here:

- *Start run* starts a plan from the list (the plans `labcli plans` shows).
  *Pause* and *Resume* hold and continue the plan's steps.
- *End run* is in the header on every tab while a run is going. The dialog lists
  what will happen, in order: the end script's steps for the routines this plan
  loaded (docs/SEQUENCE.md, Ending: the cooler's and the camera's supplies off,
  the chamber's zones off, its valves closed and confirmed, its pumps stopped),
  then each instrument's shutdown. The request is sent again every few seconds
  until the host is gone; the GUI never kills the host. Afterwards a banner says
  how the run ended and how the chamber was left (`Chamber left: sealed, 3.2 Torr,
  pump off, turbo off, zones off`), red with what the end script could not do when
  there was anything; *Dismiss* hides it in this browser.
- The command box takes the cast tab's words (`hvc platen 20`, `psu1 ch1 on`).
  The buttons under it are what the instruments' own grammar allows next, with
  limits and units; click one or type. A bad command is refused with what would
  fit and a "did you mean". A command is refused too when no run is going or the
  instrument is not live, since nothing would take it, or when its prerequisites
  are not met now (the rough valve open for a vent; docs/SEQUENCE.md). It reports
  what became of it: taken, done, or refused with the chamber's reason.
- Under the box, a help card for what you are typing: what the command does, what
  each input means, and what it needs first, each marked from the chamber's last
  report (✓ true now, ✗ not, ? not known).

Every action is noted with the user name in `~/.formslab/.run/gui.log`.

Each card shows its readings in groups, by what they are: for the chamber, Valves, Pumps,
Zones, Thermocouples, Pressure settings; for a supply, one group per channel, named
with what it feeds. Valves read Open or Closed, pumps On or Off.

Each card says whether it is *live*, and why not when it is not: no run is going, its owner has not updated
it recently (is its rScript in the plan's `load` line?), or the chamber says it
is not connected. A block that is not live is greyed with its age; its numbers
are the last ones seen, not current readings.

**Chamber.** The HVC-3500's own Manual screen, redrawn live: the chamber with
its pressure and the three zone blocks (platen, shroud, t2) with temperature
and setpoint, the vent, fill, gate, vacuum (rough) and foreline valves, the
vacuum and turbo pumps, in the symbols used across the GUI (a valve is a
bowtie, a pump a circle with a triangle), green when open or on, red when closed
or off, with the state written beside it, the fault banner, and every other thermocouple below. Temperatures can
be shown in Celsius or Kelvin. It follows the same rule as the Status cards:
when no run is going, or the chamber is not connected, the drawing is greyed
and says why, because those are the last values seen, not current readings.

Some things on the controller's own screen are not available to formsLabCLI
and are drawn as "n/a" rather than guessed: heater output %, turbo speed %,
foreline pressure, and each zone's own on/off (the controller reports one
chamber-wide "holding temperature" state).

**Plans.** A plan editor for people who do not want to remember the words.
Pick a plan on the left (a green page before its name); a plan you open is shown line by line:

- the `load` line is a row of checkboxes, one per rScript;
- `record every` is a number and a unit;
- `load` and `record` are header lines, so their place is not yours to pick: a plan
  starts with `load`, then `record`. If you delete one, the step chooser offers it
  again (`load  (always first)`, `record  (after load)`) and puts it back where it
  belongs, whichever row you asked from. A restored `load` already ticks the rScripts
  your steps use (the one that owns `hvc`, the one that publishes `chamberP`...);
- every step is a chain of dropdowns. Choosing `hvc` narrows the next choice to
  hvc's commands, choosing `platen` makes the next box a number with its limits
  and unit (`<C -180..200 C>`), and a value that does not fit is flagged under
  the row with what would. The choices come from the same declarations the cast
  tab and `labcli check` use, so they cannot disagree;
- a unit the plan writes after a number (`12 V`, `270 ohm`, `5 Torr`) is drawn inside the
  number's box and written for you: a fixed unit as text, a choice (C or K for a
  temperature, Celsius first) as a small list in the box. A time's s, min or h stays a
  choice of its own, after the box;
- comments and blank lines are kept. Each row's ⋯ menu moves it, inserts a step or a
  comment below it, or deletes it. *Edit as text* shows the plain file for pasting or
  fine changes;
- a finished step offers what may still follow it (`or go on` after a wait's time) in a
  small … box, shown on the row you are on;
- a loop (docs/SEQUENCE.md, Loops) is a `repeat` row and an `end` row, each with a
  circular arrow. Choosing `repeat` adds its `end`, with an empty step between them;
  `end` is offered only inside an open loop; deleting either deletes both and keeps
  the steps between them. The rows inside are set in, with a bar for each loop around
  them, and the file is saved with those steps two spaces in;
- a rule (`when … then …`, docs/SEQUENCE.md, Rules) is drawn as two steps on one row:
  the condition in the step colour, then the command after `then` in its own
  instrument's colour, with its part's symbol;
- a wait's `within` says, when you point at it, what happens when its time runs out;
- beside the rows, a help card follows the row you are on: what the step does,
  its inputs, and what it needs first, as the plan leaves things at that line
  (✓ the plan establishes it, ✗ the plan breaks it, ? it depends on the chamber at
  the start and is checked when the step runs, • checked only then);
- a step that breaks a prerequisite is red and the plan cannot start; one that
  depends on the chamber at the start is amber, and the plan can run (the Start
  list marks it "checks at the start").

Every step is drawn in the same four shapes, in the editor, in a read-only
plan and in the command box's suggestions, so the grammar can be seen:

| Shape | Means | Example |
|---|---|---|
| solid block, in the instrument's colour | the first word: an instrument (`hvc` blue, `psu1`/`psu2` amber, `cryo` teal, `slta` violet, `tc` green) or a step (`hold`, `until`, `log`, `load`, `record`, slate) | `hvc` |
| tinted pill, same colour | a fixed keyword, so a command reads as one phrase | `platen`, `on`, `<=`, `within` |
| shaded box, its unit inside | a value you type | `25 °C`, `30` |
| dashed underline | free text | a `log` message |

A chamber command also shows which kind of part it is about, as a small
engineering symbol in the instrument's colour before its first word: a bowtie for a
valve, a circle with a triangle for a pump, a thermometer for a zone (platen,
shroud), a gauge for a setting (pressure setpoint, hold time, recipe). The part's
name is in the tooltip and in what a screen reader says. Cycle commands (`start`,
`abort`, `stop`) carry no symbol.

A word that does not fit (an unknown keyword, a number out of range, an
instrument whose rScript is not loaded) turns red, and the reason is under the
row. A legend above the plan shows the four shapes.

Plans that ship with formsLabCLI (and anything in the folder you started from)
are read-only here, marked *shipped*. Two buttons move a file between the
shipped plans and yours, so a name is only ever in one place:

- *Edit* takes a shipped file out to `~/.formslab/plans/`, where you can change
  it. It keeps its name, so `run <name>` still finds it. In the checkout, git sees
  it as removed from `plans/` until you ship it again.
- *Ship* puts one of yours into formsLabCLI's own `plans/` folder, read-only
  again. Git sees it as changed or new: commit it to share it. A file with
  problems is not shipped, and neither is the plan that is running.

*Rename* renames one of yours, and what refers to it in your plans and blocks: the
calls to a block, the `orbit follow` and `orbit replay` lines of an orbit. If a shipped
file refers to it, the rename is refused and names that file: Edit it first. A
block's name is also the first box on its `block` line; changing it there renames it
the same way.

*Save as...* makes your own copy in
`~/.formslab/plans/`, which never changes what `run <name>` does for anyone
else, and a name already taken by any plan is refused. Saving writes the file
in one step and refuses to overwrite a plan that changed on disk since you
opened it. A draft with mistakes can be saved; it is listed as "cannot run"
until they are fixed. A saved plan appears in the Start list on the Status tab. *Delete* (your own plans only, and not
the plan that is running) moves the file to `~/.formslab/plans/.trash`, where it
is kept with the time it was deleted; move it back to restore it.

Orbit files (`.orbit`, docs/ORBIT.md) are in the same list, with an ellipse before
the name, and open in the same editor: one Keplerian element per line, drawn in
the orbit's colour (deep blue), each with a symbol for what it describes (size and
shape, the plane, the place on the orbit, time). The step chooser offers only the
elements still missing. Beside them, a panel shows the orbit now, propagated once
a second: sunlit or in umbra and when that changes, beta angle, altitude, speed,
and the coming orbit as a strip. *New orbit* starts one. An orbit is never in the
Start list: a plan follows it. The panel says how (`load rOrbit`, then `orbit
follow <orbit>`), and *Use in a plan* makes a plan that follows the orbit and
waits for its umbra (docs/ORBIT.md, In a run).

A **block** (docs/SEQUENCE.md, Blocks) is listed with a purple square and opens in
the same editor: its `block` line is the call, each `{input}` an amber value box.
*New block* starts one; *Register as block* makes one from the open plan (its
comments, `load` and steps; `record` is the calling plan's). In a plan, a call is
drawn in the block colour, slate-violet, with the same symbol, and its help card
lists the steps it runs and every prerequisite inside it. A block is never in the
Start list: a plan runs it.

**Plots.** Pick a recorded run and any of its variables; each unit gets its own
chart. Wheel zooms, drag pans, double-click resets, hovering reads values.
Kelvin variables can be shown in Celsius. *Follow the run live* refreshes every
5 s (the recorder writes a row every `record every ...`, 30 s for `tvac`).
PNG saves the charts, CSV saves the selected variables.

Runs are the CSVs the host's recorder writes (`<plan>_<UTC>.csv`; a variable
that appears mid-run continues in `_1`, `_2` and is stitched into one run).
Older files in the output folder with other layouts are not listed.

## What is not here yet

The rest of the space-environment tool (view factors, environment temperature,
a 3D view, the satellite in the chamber) is planned (`Notebook/gui.md`); the
orbit files above are its first part.

## Safeguards

The status and plot screens only read; CAST is written only by a command you send.
Plans are started by name from the server's own list, never from a path in a
request. Every state-changing request must carry a header a web page on another site
cannot set; the cookie is `SameSite=Strict`; request bodies are capped; a Host
header the server does not expect is refused. Tests assert that serial ports,
VISA, outbound sockets and new processes are all shut while every screen's data
is fetched.
