# Running on a Raspberry Pi

A Raspberry Pi 5 with Raspberry Pi OS (64-bit) is the reference Linux target. Any systemd distro works.

Taking it to a trade show on a phone hotspot? See [At a booth](booth.md).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/ScientiaCapital/fleetwatch.git && cd fleetwatch
uv sync && uv run fleetwatch login     # headless: open the link on your laptop, paste the redirect back
deploy/install.sh
```

The installer writes a systemd user unit and enables it. To start at boot without anyone logged in, run
`sudo loginctl enable-linger $USER` once.

| Task | Command |
|---|---|
| Logs | `journalctl --user -u fleetwatch -f` |
| Stop | `systemctl --user stop fleetwatch` |
| Status | `systemctl --user status fleetwatch` |

Something not working? Start here:

```bash
cd ~/fleetwatch && uv run fleetwatch doctor
```

It checks the policy, the read-only guard, redaction, the sign-in and token file, the network route to Epiphan
(and Slack, if set), and the service. Each line is OK, WARN or FAIL; it exits non-zero on any FAIL.

The unit runs with `NoNewPrivileges`, `ProtectSystem=strict` and `PrivateTmp`, and can write only to
`~/.fleetwatch`, its virtual environment and uv's cache.
