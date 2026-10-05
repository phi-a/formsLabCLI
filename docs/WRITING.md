# Writing

How formsLabCLI writes: its help, its labels, its messages and these docs. The
governing lens is the author's; the rules after it apply that lens to the text the
application shows, and `test/test_writing.py` checks the parts a machine can check.

## Maniacal Simplicity

### Purpose and status

Maniacal Simplicity is a cross-cutting editorial lens: reduce a piece to the clearest sufficient expression of its purpose. Apply it when drafting or revising in the author's voice, while preserving the requirements of genre, audience, evidence, and form. It is a working principle, not a separate voice or a structural template.

The name and proposed integration were raised by the author on 2026-10-01. The definition below is a working interpretation developed in that discussion; its fit should be refined through use. Do not treat the phrase as a demand for minimum length or maximum compression.

### Governing rule

Remove what does no necessary work. Preserve what the reader needs to understand the claim, its basis, its limits, and its consequence.

Simplicity is sufficiency. It includes the work needed to explain a difficult idea accurately. A short formulation that hides a condition, collapses a meaningful distinction, or makes an incomplete argument appear settled is not simple in this sense.

### Application

- Identify the piece's purpose before cutting.
- Prefer exact words, direct relationships, and clear sentence tasks over verbal display.
- Remove repetition, throat-clearing, decorative emphasis, and scope that does not serve the purpose.
- Keep definitions, qualifications, examples, transitions, and repetition when the audience or subject needs them.
- Preserve evidence, uncertainty, causal order, and distinctions required by the governing genre or technical method.
- Stop revising when each remaining element serves the purpose and further reduction would make the piece less accurate, complete, or usable.

### Relationship to other modes

Meilin applies professional discipline to formal diction, grammatical subjects, evidentiary basis, and technical relationships. Closed Form governs the obligation and completion of a bounded piece. Maniacal Simplicity can guide choices within either mode; it does not override their rules, the evidence, or the user's requested form. It also does not impose Closed Form's definite ending on creative work.

### Review questions

1. What is this piece required to do?
2. Does each part help do it?
3. Would removing this part obscure a necessary relationship, condition, distinction, or limit?
4. Does the ending complete the requested work, or does it claim more than the evidence supports?

## The application's text

The reader is an operator at the bench, often a student, who may not know the
controller's protocol. Every piece of text tells them what a thing does, what it
needs, and what to expect.

### Command help

A command's help is a summary line, then details (`rscripts/grammar.py`).

- **Summary**: what the command does, in one plain phrase, sentence case, at most
  50 characters. No period, controller code, parenthesis, slash, semicolon or `<`.
  It is the tooltip and the help table's line. `Open or close a valve`.
- **Details**: whole sentences, each ending with a period. What it does, what each
  input means with its unit and range, and what to expect. A controller code, useful
  to an expert, comes last: `Controller command: !ZS1, !ZO1.`
- **Unknowns stay unknown.** Where a setting's effect is not confirmed, the details
  say what is known and say that the rest is not documented here. They do not guess.

### Input names

An input is named by a word, never a symbol: `temperature`, `volts`, `amps`,
`pressure`, `seconds`, `max_volts`, `recipe`, `channel`. The unit belongs to the
input's unit field, not its name. Names are `\w+`, at least three letters.

### Names of parts

One name per part, everywhere: the help, the status cards, the Chamber view, the
rules. The chamber's parts use the names on its own screen (the HMI):

| command word | name |
|---|---|
| `rough` | Vacuum valve |
| `vent` | Vent valve |
| `fill` | Fill valve |
| `foreline` | Foreline valve |
| `gate` | Gate valve |
| `pump` | Vacuum pump |
| `turbo` | Turbo pump |
| `platen`, `shroud` | Platen, Shroud |
| `vacuum` (a setting) | Pressure setpoint |

Because "vacuum" names a valve and a pump, the `hvc vacuum <pressure>` setting is
called the pressure setpoint. Units are written `°C`, `K`, `V`, `A`, `Torr`, `s`.

### Orbit elements

An orbit file's keywords are the classical element symbols (`a`, `e`, `i`, `raan`,
`argp`, `nu`), because they are the names the field uses and the ones a reader
will meet everywhere else. Their inputs are still named by words (`semimajor`,
`inclination`), and each element's help says what the symbol stands for.

### Rules and their reasons

A rule's reason is one sentence, sentence case, ending with a period. A condition
names the part and the state it needs: `Vacuum valve closed`, `Platen at least 10 °C`.

### What is not yet held to this

Parse errors (`GrammarError`) and plan errors keep their current wording for now.
