# Cryocooler control

The cryocooler control board carries two I2C devices, documented in
`docs/Cyrocooler_board_docs.pdf`:

| Address | Part | Role |
|---|---|---|
| `0x74` | TI **TPS55288** buck-boost converter | cryocooler drive voltage (CCVOUT) |
| `0x18` | Analog Devices **AD5258BRMZ1** digipot, 1 kΩ / 64 taps | drive resistance (CCVRES) |

## Transport

The FlatSat PC has no I2C bus, and there is **no USB-I2C dongle on this
bench**. The bridge is a Raspberry Pi Pico (USB `2e8a:0005`, `cryo_board` in
the live `usbmap.json` (`~/.formslab/usbmap.json`)) running MicroPython: the PC drives it over USB CDC with
`mpremote`, and the Pico bit-bangs the bus. The board's own bring-up notes
describe exactly this arrangement.

```
FlatSat PC ──USB CDC (mpremote)──▶ Pico ──SoftI2C──┬── 0x74 converter
                                                    └── 0x18 digipot
```

`src/formslab/devices/cryocooler/pico_i2c.py` is the seam. It exposes only `scan()`, `read_register()` and
`write_register()`, so swapping in a native USB-I2C adapter later means writing
one class with those three methods — nothing above it changes.

## Layers

| File (`src/formslab/devices/cryocooler/`) | Responsibility |
|---|---|
| `pico_board_control.py` | MicroPython firmware. A bare I2C bridge: scan/read/write. No calibration, no state, no board knowledge. |
| `pico_i2c.py` | PC-side transport. Serial link, firmware deploy, framed calls. `mpremote` imported lazily. |
| `registers.py` | Register map and every encoding. Pure, no imports, fully testable. |
| `board.py` | `CryoBoard`: board behaviour and state. |
| `config.py` | Operating policy: PSU channel, supply setpoints, the 12–20 V band. |
| `owner.py` | What rCryoBoard needs: bring up the supply, own the board. |
| `rScripts/rCryoBoard.py` | The only owner of a live `CryoBoard`. |

Nothing else touches `CryoBoard`. The console and lab plans command the board
through CAST, as `cryo ...` in the cast tab or the same words (`cryo on`) in a plan;
rCryoBoard applies it.

## Supply, and a channel conflict

The board's input is fed from **psu1 (Rigol DP832A) CH1**, confirmed on the
bench 2026-08-29 and set in `cryocooler/config.py`. `CRYO_PSU_COMPONENT` is derived
from the label so log lines cannot drift from it.

> **One owner for psu1 CH1.** rCryoBoard configures this channel (24 V / 2.0 A,
> `OVP 24.5 V`) and asks rPSU, the owner of psu1, to apply it. Do not command
> psu1 CH1 from a plan or the console while rCryoBoard runs: whichever writes
> last wins, and a stray setpoint would brown out or over-volt the board.

## Power thresholds

These are current engineering figures from bring-up, not electrical
specifications:

| Board input | Expected |
|---|---|
| below ~15 V | bus silent, scan returns nothing |
| above ~15 V | I2C devices answer: `['0x18', '0x74']` |
| above ~20 V | converter output can be enabled |
| 24 V | normal bench condition for full function |

**A successful scan does not mean the converter can drive an output.** The two
are reported separately, on purpose.

## Reading a scan result

`present()` and `status()` both classify the scan rather than just listing it:

| Verdict | Meaning |
|---|---|
| `healthy` | `0x18` and `0x74`, nothing else |
| `sda_stuck_low` | Nearly every address "answers". No bus has 100 devices — SDA is held low, so every address reads as an ACK. Wiring fault. |
| `silent` | Nothing answered: board below ~15 V, or wrong pins in `usbmap.json` |
| `partial` | Somebody answered, but not the expected pair |

A stuck bus makes the converter look present, so `status()` deliberately skips
the STATUS read there rather than reporting noise as telemetry.

## Operating

```python
from lab.CryoBoard import CryoBoard

cryo = CryoBoard("cryo_board")

cryo.scan()             # ['0x18', '0x74']  -- communication health
cryo.present()          # {'converter': True, 'digipot': True, ...}
cryo.read_status()      # decoded STATUS: pgood / intvref / sc / ocp / ovp
cryo.read_registers()   # full dump of both devices
```

Construction opens no link and writes no register. Programming the board and
energising it are separate steps:

```python
cryo.initialize(voltage=17.0, resistance=266.0)   # output stays OFF
cryo.set_resistance(300.0)
cryo.set_output_voltage(15.0)
cryo.enable_output()                              # the only thing that energises
cryo.status()
cryo.disable_output()
cryo.shutdown()                                   # disable + release the link
```

`status()` reports both halves of health:

| Key | Meaning |
|---|---|
| `connected`, `port` | serial link to the bridge |
| `i2c_devices`, `converter_present`, `digipot_present` | bus health |
| `pgood`, `intvref`, `faulted`, `faults`, `output_healthy` | converter output health |
| `enabled`, `output_voltage_v`, `resistance_ohms`, `resistance_code` | what was commanded |

A transport failure raises; a device that simply does not answer is reported
in the dict.

Bring-up from a shell, with the PSU sequenced for you:

```
python test/test_CCboard.py            # power up, scan, report — output stays off
python test/test_CCboard.py --enable   # also drive the output briefly
```

## Known uncertainties

**I2C pins — resolved 2026-08-29.** `SCL = GP17`, `SDA = GP16`, in
`usbmap.json` under `cryo_board.i2c`.

Established by a pull-up sweep on the bench: every GPIO was read with the
internal pull-down engaged and again with the internal pull-up, since an
external ~4.7 kΩ pull-up beats the RP2040's ~50 kΩ internal pull-down.

| GPIO | board off | board on (16 V / 24 V) |
|---|---|---|
| **17** | low | **pulls up** — external pull-up |
| **16** | low | low |
| all others incl. 22, 23 | floating | floating |

Only GP16/GP17 respond to board power, so that is where the harness lands.
GP17 showing its pull-up the moment the board is energised also confirms the
board's 3.3 V rail is healthy from 16 V input.

**Do not use 22/23.** That is the ESP32 default I²C pinout, inherited from the
original bench script `cryo_control_example.py`, which was written for ESP32
silicon (`utime`/`ustruct`, `SoftI2C(scl=22, sda=23)`). GP23 is not broken out
on a Raspberry Pi Pico at all, so that pair was never physically reachable
here.

**Resistance fit.** Two fits for the AD5258 exist in the project's history:

- `62.06 + 16.81·D` — datasheet ideal, used by the original bench script.
- `75.30 + 17.43·D` — measured on this board, printed in the board docs.

formsLabCLI uses the **measured** fit. Two consequences are unresolved and left
alone deliberately:

1. The docs derive the code with `floor()`, formsLabCLI with `round()`. `round()` is
   kept — it lands nearest the request — so a request is met to within half a
   step (~8.7 Ω) either side, not always from below.
2. The declared 62–1120 Ω band matches the *datasheet* fit exactly
   (`62.06 + 16.81·63 = 1121.1`). Under the measured fit the reachable range
   is 75.3–1173.4 Ω, so a request at 62 Ω returns ~75 Ω and codes 61–63 are
   unreachable through `set_resistance()`. Use `set_resistance_code()` if the
   top codes are ever needed. The band is left as-is because it is what CAST
   advertises and what `rCryoBoard` enforces.

**Converter calibration.** `CAL_SLOPE = 1.187`, `CAL_OFFSET = -1.90` appear in
both the bench script and the deployed firmware, so they are treated as current
until a fresh calibration run says otherwise.
