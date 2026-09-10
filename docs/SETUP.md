# Set up a new computer

## 1. Install the console

On Windows, install 64-bit Python 3.10 or newer and Git. Open PowerShell:

```powershell
git clone https://github.com/phi-a/formsLabCLI.git
cd formsLabCLI
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

If you already have the checkout, start with `cd` into it. Create a fresh
`.venv` on each computer; do not copy the old one. These commands need no
PowerShell activation or execution-policy changes.

Install extras only for the equipment/features you use:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[pico]"     # cryo Pico bridge
.\.venv\Scripts\python.exe -m pip install -e ".[forms]"    # missions/Book/catalog
.\.venv\Scripts\python.exe -m pip install -e ".[analysis]" # plots
```

For Linux, use `python3 -m venv .venv`, then `.venv/bin/python` and
`.venv/bin/labcli` in place of the Windows paths below. Serial access needs
your distribution's device permissions; hub paths also differ between computers.

## 2. Install NI-VISA for a native USB Rigol PSU

`pip` installs PyVISA, the Python interface; it does **not** install the native
VISA library. Install [NI-VISA from NI](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html)
for your operating system, including USB support and NI MAX on Windows.
Restart if the installer requests it. Match the VISA library to Python's
32/64-bit architecture. See [PyVISA installation](https://pyvisa.readthedocs.io/en/latest/introduction/getting.html).

Connect and power the PSU. In NI MAX, check **Devices and Interfaces** and
copy its full VISA resource name. Verify the backend from this same venv:

```powershell
.\.venv\Scripts\pyvisa-info.exe
.\.venv\Scripts\python.exe -c "import pyvisa; rm = pyvisa.ResourceManager(); print(rm.visalib); print(rm.list_resources()); rm.close()"
```

Expect a resource like `USB0::0x1AB1::0x0E11::<YOUR_SERIAL>::INSTR`.
Native USB PSUs use VISA automatically; no `FORMS_PSU_BACKEND` override is
needed. A serial PSU uses pyserial by default and does not require NI-VISA.
A native USB instrument may have no COM port.

## 3. Identify ports and edit the live map

Run these from the checkout:

```powershell
.\.venv\Scripts\python.exe -m serial.tools.list_ports -v
.\.venv\Scripts\python.exe -c "from formslab.config import usbmap_path; print(usbmap_path())"
```

The second command creates the map if absent and prints its path (normally
`C:\Users\<you>\.formslab\usbmap.json`). Edit **that file**, not the packaged
default. Existing settings survive reinstalls. To use a different bench folder,
set `$env:FORMSLAB_CONFIG_DIR = 'C:\Lab\bench-a'` before these commands and
before launching the console.

COM numbers belong to this computer and may change when USB connections move.
Match devices using the serial numbers/descriptions in the port listing or
Windows Device Manager; connect one at a time if necessary. Do not reuse the
old computer's COM numbers or the shipped PSU serial number without checking.

Change only the relevant fields, keeping the rest of each record:

| Device record | Field to set on Windows | Example only |
|---|---|---|
| `psu1` / `psu2`, native USB | `resource_windows` | Full `USB0::…::INSTR` name from VISA |
| `psu1` / `psu2`, serial adapter | `resource_windows` | `ASRLCOM12::INSTR` for COM12 (default pyserial driver) |
| `RTD1` | `resource` | `COM7` |
| `SMTC08_A` / `SMTC08_B` | `resource` | `COM8` / `COM9` |
| `cryo_board` | `port_windows` | `COM10` |

Set `enabled` to `false` on unused PSU records. On Linux, PSU addresses use
`resource`; temperature readers accept `/dev/tty…` paths. The cryo bridge uses
its Linux `interface_id`; remove a Windows `port_windows` override when moving
that map to Linux.

For the cryo bridge, you can instead set its USB `serial_number` and remove
`port_windows` to discover its current Windows COM number automatically.
The RTD Pico and cryo Pico can share VID/PID, so identify the correct board.
The Pico needs MicroPython; the `[pico]` extra supplies mpremote, and the driver
can deploy its bridge script on connection. Confirm `cryo_board.i2c` matches
your wiring before using it.

If used, also update `PS` host/user/adapter/local IP for your PowerSwitch and
set the environment variable named by `PS.password_env` (normally
`FORMS_POWERSWITCH_PASSWORD`). See [PowerSwitch setup](POWERSWITCH.md).
For sLTA, review `slta` MAC/IP fields and its separate host software. Linux
USB reset/recovery also needs the actual hub locations, not the shipped ones.

## 4. Launch and check

```powershell
.\.venv\Scripts\labcli.exe
```

Use `help`, `help all`, `--psu` to switch tabs, and `exit` to quit. In the PSU
tab, `--status` checks configured supplies. Restart the console after editing
the map. For a desktop shortcut, target `.venv\Scripts\labcli.exe` and set
**Start in** to the checkout folder.
Outputs default to `outputs/` under the working directory; set
`FORMSLAB_OUTPUT_DIR` to choose another folder. Environment overrides must also
be present in the session that launches a shortcut.

If VISA cannot find its library, check NI-VISA installation and bitness with
`.\.venv\Scripts\pyvisa-info.exe`. If the resource list is
empty, check the cable, power, and detection in NI MAX. If a COM port is missing
or busy, check Device Manager, install the adapter manufacturer's driver if
needed, and close other serial terminals. Re-run port discovery before editing
the map; listing ports/resources does not change instrument outputs.
