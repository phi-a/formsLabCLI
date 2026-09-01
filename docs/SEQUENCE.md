# FORMS Sequence

The **Sequence** is the canonical runtime model of a simulation: an ordered list
of **Segments** executed against the `forms` handle by a **SequenceRunner**,
which emits a typed **event stream** so Astrid can see *how a
mission was built* and *what it is doing while it runs*.

`.zen` stays the authoring surface — it **compiles into** a Sequence. The agent
keeps writing `.zen`; nothing about how missions are written changes. The
Sequence is what the engine runs underneath.

An external `routines.load` reference does not create another mission format.
The `.zen` remains the canonical document; portability then means carrying the
workspace bundle that supplies it: `.zen`, `routines/`, `astrid.toml`, and
`astrid.lock`. Before an extension is imported, the mission process activates
the synchronized workspace environment and the loader admits `ROUTINE_SPEC`
modules through the verified-routine policy. The per-step call still receives
the same live `forms` handle as every other routine.

When Astrid creates that extension, Luna first calls the non-writing `spawn`
derive capability with a structured contract. A bounded Sol worker returns
source as a typed draft; validation and a disposable Sequence preflight must
pass before the draft becomes eligible for `author_routine`. The sole writer
then commits the module, `routines.load`, and output registrations as one
workspace-confined transaction, rolling every file back on failure. Neither
the worker nor preflight writes the active Sequence or mission.

> Vocabulary: a Sequence's unit is a **Segment**, never a "Step". The agent
> layer (`GLOSSARY.md`) owns *Step* and *Plan* for capability planning. Keep the
> two distinct.

## The grammar (minimalist)

A Sequence is `setup` plus an ordered list of segments. A segment is just a
`verb` and a `params` dict — the verb selects an executor from a registry, so
the verb set is a thin, swappable layer, not a fixed enum.

| verb | role | compiled from |
| --- | --- | --- |
| `setup` | establish initial state at t0 (orbit, time, propagator, forces, recording, attitude); prime the first `derive` | the `.zen` config blocks |
| `propagate` | advance the state until a **stop condition** | today's loop body |
| `routine` | declare the per-step computations active during propagation | `@routine`, `routines.load` |
| `stream` | declare the metadata surface the run emits | `recording` + telemetry defaults |

`propagate` stop conditions live in `sequence/stops.py`:

- `stop=None` → run to the propagator's own `finished` flag (the configured
  span). **This is the behavior-preservation default** — today's single-loop
  missions compile to one full-span propagate.
- `for_steps(n)` → stop after `n` integration steps (deterministic; used to
  split a span).
- `for_duration(seconds)` → stop after `seconds` of sim-time.
- `until_event(event, ...)` → stop at the earliest supported full-force event
  crossing inside an accepted RK45 step. Multiple event descriptors may be
  supplied; `while_cond(...)` remains deferred.

### How today's missions map

Every existing `.zen` compiles to the degenerate, behavior-identical form:

```
setup  →  [routine]  →  propagate(full span)
```

with all `@routine`s / loaded routines running each step (driven by the
runner's injected `tick`). Multi-segment timelines — e.g.
`propagate until eclipse_entry → maneuver → propagate for 1 orbit` — become
expressible at the propagation-stop seam; state-changing action/reset verbs
remain later work.

## The event stream (why the agent isn't blind)

Two channels, both carried by `EventSink` (`sequence/events.py`):

1. **Structure** — `SequenceStarted` carries `Sequence.to_manifest()`: the full
   segment tree with the applied config. Read this to see *what was built*.
2. **Progress** — `SegmentStarted` · `Progress` (step, sim-seconds, fraction) ·
   `EventDetected` · `SegmentFinished` · `SequenceFinished`: *what it's doing*.

`EventSink` is the transport seam:

| sink | use |
| --- | --- |
| `NullSink` | default — headless runs, tests that don't look |
| `ListSink` | tests — collect events and assert the stream |
| `JsonlEventSink` | the host — append one JSON line per event to `data/sequence.events.jsonl` (parallel to `streamfile.json` state telemetry); `Progress` is wall-clock-throttled |

A transport subscriber is a drop-in `EventSink` plus a reader of
`to_manifest()` — no engine changes required.

## Where it runs

`python/sequence.py` (the host) builds `forms` as before, then:

```python
seq    = compile_sequence(zen_runtime_or_forms)
runner = SequenceRunner(forms, seq,
                        sink=JsonlEventSink(...),
                        tick=zen_runtime.tick,    # per-step routines
                        write=telemetry.write,    # optional host telemetry
                        control=_control,         # ctrl/cmd poll + pause
                        after_step=...)           # host mode transition
runner.run()
```

Host-only concerns (lock, control I/O, telemetry, mode transition) are
**injected callbacks**. The library `SequenceRunner` never imports `cli/`/`app/`
— it touches only the `forms` handle and these hooks, so it runs headless (the
library calls and tests) and stays layer-clean (`tools/check_layers.py`; `sequence/` is
registered at the `skills` tier).

## Layout (compartmentalized — one concern per file)

```
sequence/
  spec.py            Sequence + Segment (typed, serializable; to_manifest)
  events.py          event records + EventSink (NullSink/ListSink/JsonlEventSink)
  stops.py           propagate stop predicates
  runner.py          SequenceRunner — iterates segments, emits events
  compile.py         .zen / forms  ->  Sequence
  segments/
    registry.py      verb -> executor map (@register)
    setup.py         prime the first derive
    propagate.py     the per-step loop (parity with the legacy mission loop)
    routine.py       declarative (folds into propagate's tick today)
    stream.py        declarative placeholder
```

## Adding a segment verb

1. Add `segments/<verb>.py` with an executor decorated `@register("<verb>")`:

   ```python
   from forms.sequence.segments.registry import register

   @register("maneuver")
   def execute(runner, forms, segment) -> int:
       ...           # touch only `forms` (+ runner hooks); return steps taken
       return 0
   ```

2. Import it from `segments/__init__.py` so registration happens on import.
3. Teach `compile.py` to emit the new segment (if it should be authored).

That is the whole extension surface — `spec.py`, `runner.py`, and `events.py`
never change to add a verb.

## Accepted-step event stops

`stops.until_event("node_descending")` and ordinary no-lead trigger actions
contribute predicates to one accepted-step locator used by the `propagate`
executor. The event engine inspects immutable dense state, refines an exact end
hit or strict sign-change bracket, and closes the span at the earliest root
across all consumers. It never installs an interior state on the live handle.
Interval ownership is `(start, end]`, so a shared endpoint is not emitted twice;
an exact level at `t0` is not a directional crossing.

The contract is intentionally narrower than general root discovery:

- forward simulated RK45 only; propagators without dense step truncation refuse;
- finite continuous provider-aware event scalars only; step-quantized events and
  lead offsets refuse;
- one endpoint bracket per predicate; arbitrary interior tangencies, an even
  number of roots, discontinuities, and D6 backward semantics are not claimed;
- evaluator/nonfinite/refinement failures fail closed before committed state
  changes;
- independently refined coincident roots keep stable input order and their own
  epochs; the earliest epoch is never averaged;
- terminal location ends the Sequence segment after its ordinary reached
  boundary; coincident trigger actions fire there in priority order. Ordinary
  no-lead action boundaries continue and snap back to the nominal grid. A
  discontinuous reset at that epoch is D5 and is not performed here;
- lead-time trigger actions retain their bounded two-body future predictor, and
  step-quantized events retain boundary sampling.

The owning Book page is `events.propagation`.

## Deferred (seams left open, not built this pass)

- `.zen` authoring of `until:` and `while:` stops; the Python `until_event`
  runtime is wired, while `while_cond` remains an explicit seam.
- An action verb (`do` / `maneuver` / `set`) for instantaneous state changes
  between propagation spans.
- The MCP server subscribing to `EventSink` + `to_manifest()`.
- Astrid authoring multi-segment Sequences (it keeps writing `.zen`).
