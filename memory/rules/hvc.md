# Talking to the HVC-3500

Rules for any code or person that sends the controller a command over its ASCII
port. They are why the driver (`src/formslab/devices/hvc3500/`) is built the way
it is; break one and the chamber can do something nobody asked for. The protocol
and the installed controller's facts: [hvc3500](../instruments/hvc3500.md).

## The controller is the safety authority

- The PLC keeps its interlocks in every mode, and formsLAB leaves them to it. Ask
  for operations and setpoints; never reimplement or work around its sequences.
- formsLAB's own rules ([tvac](tvac.md)) only refuse earlier and say why in words.
  They never allow what the controller refuses.

## One client at a time

- The controller takes **one TCP client**. During a run rLACO holds it; between
  runs formsLAB's chamber monitor holds it, read-only, while the GUI is open
  ([gui](../use/gui.md), Between runs).
- `scripts/pumpdown.py` and `scripts/vent_test.py` cannot connect while either holds
  it. Start the GUI with `--no-monitor` to use them.

## Never retry a write

- A **toggle** (`!OR`, `!OV`, `!OF`, `!O4`, `!OG`, `!OP`, `!OT`) flips a valve or
  pump; it does not set a state. The reply is the state when the command arrived;
  the change comes about a second later. So: read the state first, send one toggle,
  wait, read again to verify. A toggle retried after a timeout can undo itself.
- **`!CS` (start)** is contextual: it starts a cycle, or continues a recipe step
  that is holding. Retried, it can advance a step instead of confirming a start.
- An **action** (start, abort, reset, recipe run, vacuum operations, zone on or off)
  is never retried. Read the status after it.
- A timeout raises and marks the connection dirty; nothing is resent on its own.

## Verify every setpoint by its echo

Each zone has two setpoints. `!Zn:value` writes the **commanded** one, which the
HMI shows and the controller uses once the zone is on. `?Zn` returns the
**effective** one, which follows the zone's own sensor while the zone is off. So a
write is confirmed by the echo of the write (`Z n: value`), never by reading `?Zn`
afterwards (found 2026-09-24).

## Commands to treat with care

| Command | What it really does |
|---|---|
| `hvc abort` (`!CA`) | raises fault 0 and runs the recipe's home steps, which may open valves. End never sends it ([end](../design/end.md), D9) |
| `hvc reset` (`!CR`) | runs the controller's recovery sequence. A hard over-temperature also needs the Watlow reset by hand |
| `hvc start` (`!CS`) | starts a cycle or continues a held recipe step: never retry |
| `hvc vent2atm`, `fill2atm`, `purge`, `closeall` (`!VA`, `!FA`, `!PS`, `!NA`) | act only inside a running cycle. On an idle chamber in Manual mode, use the valves |
| `hvc recipe start` | hands the chamber to a stored recipe: see [recipes](recipes.md) |

After any of these, a plan can no longer know the state of the valves and pumps:
the plan checker treats them as able to change anything.

## Units and numbers

- Units are set on the HMI and cannot be read over ASCII. This chamber: Torr, °C,
  seconds (`tvac_bench.json`). If someone changes them on the HMI, change the
  profile too.
- Temperatures go over the wire in tenths with no decimal point: `855` is 85.5.
- Lines end in carriage return (`0x0D`); the manual's `0x13` is a typo. A line
  feed gets `ER`.
- Replies are untidy (padding, leading spaces, free text after `?TC`);
  `hvc3500/protocol.py` normalizes them.

## Faults

`?ES` returns a severity (`N`, `W`, `F`) and a bit mask: bit *n* is fault *n*
([hvc3500](../instruments/hvc3500.md), Faults and warnings). The manual's own
example disagrees with its rule; formsLAB follows the rule and keeps unknown bits.
Warnings can be configured to become faults after a delay (HVC p. 19-20).

## Things not to change

- The Panel and CPU IP addresses (10.1.2.120, 10.1.2.121): other parts of the
  chamber may depend on them. Record the old values first.
- *Enable ASCII in Recipe* is off on this chamber, and formsLAB does not need it:
  it drives the chamber in Manual mode.
- The VNC session is view-only. That does not affect automation.
