# The bench network

The wired network that joins the bench computer to the instruments that speak
Ethernet: the [powerswitch](powerswitch.md) and the chamber's
[hvc3500](hvc3500.md) controller. As set up and checked on 2026-10-29.

## What is on it

One cable runs from the computer to a Netgear switch; the PowerSwitch and the
controller's RJ45 port labelled **Ethernet** plug into the same switch.

```text
bench computer (Ethernet 2) ── Netgear switch ─┬── PowerSwitch
                                               └── HVC-3500 (Ethernet port)
```

| Device | Address | Service | Hardware address |
|---|---|---|---|
| Bench computer, adapter `Ethernet 2` | `192.168.0.50/24` and `10.1.2.50/24` | | `B0-41-6F-15-8C-F3` |
| PowerSwitch | `192.168.0.100` | web control, HTTP port 80 | `7C-E1-FF-04-8E-54` |
| HVC-3500 CPU | `10.1.2.121` | ASCII commands, TCP port 1 | `00-0D-22-82-08-9C` |
| HVC-3500 Panel | `10.1.2.120` | VNC 5900, FTP 21 | not recorded |

Two subnets share the switch, so the computer holds an address on each. The
adapter has no gateway: nothing on it routes anywhere else, and it cannot take
over the computer's Wi-Fi or Tailscale traffic. The campus Wi-Fi
(`10.192.0.0/14`) does not overlap either subnet.

Do not change the controller's addresses ([hvc](../rules/hvc.md), Things not to
change).

## The switch

A Netgear Ethernet switch. Its model, whether it is managed, and which port
each cable uses are not recorded. Any port works while all ports are one
network, as on an unmanaged switch or a managed one at its factory settings.

## Setting up the computer

Both addresses are persistent: they survive a reboot. Adding one needs an
administrator PowerShell: right-click PowerShell, **Run as administrator**, and
check that the title bar starts with *Administrator:*. A window that merely
opens in `C:\WINDOWS\system32` is not elevated and answers "Access is denied".

- **PowerSwitch address:** the PSU tab's `--setup` (see [powerswitch](powerswitch.md)).
  It adds `192.168.0.50` and keeps the adapter's other addresses; with the
  address already there it changes nothing.
- **Chamber address:**

  ```powershell
  New-NetIPAddress -InterfaceAlias 'Ethernet 2' -IPAddress 10.1.2.50 -PrefixLength 24
  ```

Do not run `netsh interface ipv4 set address` on this adapter: it replaces
every address, and the other subnet goes dark. `--setup` did exactly that
before 2026-10-29; it now adds instead.

On another computer the adapter has another name: put it in the live map's
`PS.adapter` and use it in the command above.

## Checking it

None of these needs an administrator or changes anything.

```powershell
Get-NetIPAddress -InterfaceAlias 'Ethernet 2' -AddressFamily IPv4   # both addresses
.\.venv\Scripts\python.exe -m formslab.devices.powerswitch status    # [status] PS GET / -> HTTP 200
.\.venv\Scripts\python.exe -m formslab.devices.hvc3500 snapshot      # every line "ok"
```

The controller takes one client at a time: stop a run or the GUI's monitor
before `snapshot` ([hvc](../rules/hvc.md)).

## When something does not answer

1. **Is the address there?** Without `10.1.2.50` the computer sends the
   chamber's traffic out over Wi-Fi, where nothing answers.
2. **Did anything answer on the wire?** `Get-NetNeighbor -InterfaceAlias 'Ethernet 2'`
   lists each address the computer has tried. *Reachable* or *Stale* with a
   hardware address means the device is there. *Incomplete* or *Unreachable*
   means nothing replied: check its power, its cable and the switch port's
   light, or give it time to start.
3. **Is the device busy?** The controller refuses a second client while a run
   or the GUI holds it.

## Found at the bench

- **2026-10-29:** the cables were moved onto the Netgear switch. The computer
  had only `192.168.0.50`, so the chamber could not be reached; `10.1.2.50` was
  added. The PowerSwitch did not answer at first, then answered a few minutes
  later with nothing changed on the computer. Afterwards: PowerSwitch status
  HTTP 200; controller `snapshot` 38 of 38 reads, about 100 ms each; the Panel
  accepts connections on 5900 and 21.
