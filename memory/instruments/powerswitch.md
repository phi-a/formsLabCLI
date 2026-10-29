# Lab PowerSwitch

The labcli PSU tab treats the Digital Loggers PowerSwitch as logical device
`PS`. Its permanent machine and network identity is the `PS` record in
the live `usbmap.json` (`~/.formslab/usbmap.json`); outlet control and network setup are implemented by
`src/formslab/devices/powerswitch/`; run it directly with
`python -m formslab.devices.powerswitch status`.

## On the network

The PowerSwitch is at `192.168.0.100` (hardware address `7C-E1-FF-04-8E-54`),
on the bench switch with the chamber controller. The computer reaches it from
`192.168.0.50/24` on its adapter `Ethernet 2`, beside the chamber's
`10.1.2.50/24`. The whole layout is in [network](network.md).

From labcli:

```text
--psu
--device ps
--setup --dry-run
--setup
--status
```

`--setup` adds `192.168.0.50` to the adapter and keeps its other addresses, so
the chamber's stays. With the address already there it changes nothing; only an
adapter on DHCP, which holds no fixed address to lose, is switched to static.
The address is persistent, with no gateway. `--setup --dry-run` shows the
change without making it.

Adding the address needs an elevated terminal: right-click the labcli CLI
launcher and choose **Run as administrator** before `--setup`. A disconnected
adapter can retain the configured address while reporting no usable link;
connect and power the switch before expecting `--status` to succeed.

## Outlet control

After `--status` succeeds:

```text
--ch 1 --on
--ch 1 --off
--ch 1 --cycle --delay 5
```

Outlets are limited to the configured `1..8` range. A cycle stops if its OFF
step fails and does not claim success unless the ON step also succeeds.

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
