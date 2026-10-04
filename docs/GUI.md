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
- *End run* is in the header on every tab while a run is going. It asks the
  host to stop, so each instrument's shutdown runs (supplies off, pumping the
  run started stopped), and it is sent again every few seconds until the host
  is gone. The GUI never kills the host.
- The command box takes the cast tab's words (`hvc platen 20`, `psu1 ch1 on`).
  The buttons under it are what the instruments' own grammar allows next, with
  limits and units; click one or type. A bad command is refused with what would
  fit and a "did you mean". A command is refused too when no run is going or the
  instrument is not live, since nothing would take it, and it reports whether the
  rScript has taken it.

Every action is noted with the user name in `~/.formslab/.run/gui.log`.

Each card says whether it is *live*, and why not when it is not: no run is going, its owner has not updated
it recently (is its rScript in the plan's `load` line?), or the chamber says it
is not connected. A block that is not live is greyed with its age; its numbers
are the last ones seen, not current readings.

**Plots.** Pick a recorded run and any of its variables; each unit gets its own
chart. Wheel zooms, drag pans, double-click resets, hovering reads values.
Kelvin variables can be shown in Celsius. *Follow the run live* refreshes every
5 s (the recorder writes a row every `record every ...`, 30 s for `tvac`).
PNG saves the charts, CSV saves the selected variables.

Runs are the CSVs the host's recorder writes (`<plan>_<UTC>.csv`; a variable
that appears mid-run continues in `_1`, `_2` and is stitched into one run).
Older files in the output folder with other layouts are not listed.

## What is not here yet

The plan editor, the chamber diagram and the space-environment view are planned
(`Notebook/gui.md`).

## Safeguards

The status and plot screens only read; CAST is written only by a command you send.
Plans are started by name from the server's own list, never from a path in a
request. Every state-changing request must carry a header a web page on another site
cannot set; the cookie is `SameSite=Strict`; request bodies are capped; a Host
header the server does not expect is refused. Tests assert that serial ports,
VISA, outbound sockets and new processes are all shut while every screen's data
is fetched.
