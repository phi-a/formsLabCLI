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

## 3. Find your devices and edit the live map

With everything plugged in, run this from the checkout:

```powershell
.\.venv\Scripts\labcli.exe hardware
```

It prints the live map's path (normally `C:\Users\<you>\.formslab\usbmap.json`),
each entry found or missing, and what is plugged in that the map does not name.
The GUI's Hardware page shows the same. Edit **that file**, not the packaged
default. Existing settings survive reinstalls. To use a different bench folder,
set `$env:FORMSLAB_CONFIG_DIR = 'C:\Lab\bench-a'` before these commands and
before launching the console.

Each device not in the map comes with its line for the map, "in the map as".
To tell two alike apart, unplug one and look again; the page's *Look again*
names what went. Copy the line into the device's entry, keeping the rest:

```json
"psu1":       {"usb": "1ab1:0e11", "serial": "DP8B279M00280", "channels": {...}},
"SMTC08_A":   {"usb": "1a86:7523", "path": "1-2.3.2", "baud": 9600},
"cryo_board": {"usb": "2e8a:0005", "serial": "E66480454F333A2F", "i2c": {...}}
```

A `serial` finds the device on any port. An adapter without one, like the
SMTC08's CH340, is found by its `path`, the place on the USB tree it is plugged
in: plug it into the same socket each time. Each computer numbers its own hubs,
so each computer keeps its own map. `"address": "COM3"` pins a port outright,
for a port with no USB identity; COM numbers change as things are plugged in,
so prefer a serial or a path. Set `enabled` to `false` on unused PSU records.

A map in the old form (`resource`, `resource_windows`, `port_windows`,
`interface_id`, `serial_number`) is converted the first time it is read, using
what is plugged in then; the old one stays beside it as `usbmap.json.old`.

The Pico needs MicroPython; the `[pico]` extra supplies mpremote, and the driver
can deploy its bridge script on connection. Confirm `cryo_board.i2c` matches
your wiring before using it.

If used, also update `PS` host/user/adapter/local IP for your PowerSwitch and
set the environment variable named by `PS.password_env` (normally
`FORMS_POWERSWITCH_PASSWORD`). See [PowerSwitch setup](../instruments/powerswitch.md).
The chamber shares the PowerSwitch's switch on another subnet, so the same
adapter also needs `10.1.2.50/24`: see [network](../instruments/network.md).

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
