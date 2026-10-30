# Lab PowerSwitch

The Digital Loggers PowerSwitch is logical device `PS`. Its permanent machine
and network identity is the `PS` record in the live `usbmap.json`
(`~/.formslab/usbmap.json`); outlet control and network setup are implemented
by `src/formslab/devices/powerswitch/`, reached from `labcli power`
(`src/formslab/safety.py`). The driver also runs on its own:
`python -m formslab.devices.powerswitch status`.

Its place in the order of off switches is in [safety](../rules/safety.md), *When
something must go off*: the second layer, for a supply that does not answer.

## On the network

The PowerSwitch is at `192.168.0.100` (hardware address `7C-E1-FF-04-8E-54`),
on the bench switch with the chamber controller. The computer reaches it from
`192.168.0.50/24` on its adapter `Ethernet 2`, beside the chamber's
`10.1.2.50/24`. The whole layout is in [network](network.md).

```text
labcli power                  the switch, whether it answers, each outlet and what it feeds
labcli power setup --dry-run  what adding this computer's address would change
labcli power setup            add it
```

`power setup` adds `192.168.0.50` to the adapter and keeps its other addresses,
so the chamber's stays. With the address already there it changes nothing; only
an adapter on DHCP, which holds no fixed address to lose, is switched to static.
The address is persistent, with no gateway.

Adding the address needs an elevated terminal: right-click the labcli launcher
and choose **Run as administrator** before `power setup`. A disconnected adapter
can retain the configured address while reporting no usable link; connect and
power the switch before expecting `labcli power` to say it is reached.

## Outlet control

```text
labcli power 3 on
labcli power 3 off
labcli power 3 cycle          off, five seconds, on
```

Outlets are `1` to the map's `outlet_count` (8). `off` and `cycle` ask `y/n`
first; from a script, add the word `yes`. A cycle stops if its off step fails
and does not claim success unless the on step also succeeds. Nothing is sent
when the switch does not answer. Both are refused while a run is going: a run's
routines own the supplies, and End turns them off.

**What each outlet feeds** is written in the map's `PS.outlets`
(`{"3": "psu1"}`), so `labcli power` can say it. Shipped empty: the numbers are
to be read off the switch at the bench and recorded in the live map.

## Credentials and machine changes

The tracked lab record owns the host, username, adapter, local address, and
outlet count. Set `FORMS_POWERSWITCH_PASSWORD` in the launcher environment to
override the legacy device password without putting a new credential in Git.

If the dedicated NIC changes, update only these fields under `PS` in
the live `usbmap.json` (`~/.formslab/usbmap.json`):

```json
{
  "adapter": "Ethernet 2",
  "local_ip": "192.168.0.50",
  "prefix_length": 24,
  "host": "192.168.0.100"
}
```
