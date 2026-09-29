# Lab PowerSwitch

The labcli PSU tab treats the Digital Loggers PowerSwitch as logical device
`PS`. Its permanent machine and network identity is the `PS` record in
`lab/usbmap.json`; outlet control and network setup are implemented by
`lab/powerswitch.py`.

## Windows lab configuration

The current new-lab record assigns the dedicated adapter `Ethernet 2` the
static address `192.168.0.50/24`. The PowerSwitch is expected at
`192.168.0.100`. The adapter has no default gateway, so it cannot take over the
machine's normal Wi-Fi or Tailscale route.

From labcli:

```text
--psu
--device ps
--setup --dry-run
--setup
--status
```

Windows network changes require an elevated terminal. Right-click the FORMS
CLI launcher and choose **Run as administrator** before `--setup`. The setup is
persistent across reboots. A disconnected adapter can retain the configured
address while reporting no usable link; connect and power the switch before
expecting `--status` to succeed.

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
`lab/usbmap.json`:

```json
{
  "adapter": "Ethernet 2",
  "local_ip": "192.168.0.50",
  "prefix_length": 24,
  "host": "192.168.0.100"
}
```
