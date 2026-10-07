# Running at a trade-show booth

A Raspberry Pi on a phone's hotspot is enough to show Fleetwatch live. Fleetwatch only makes outgoing
connections, to Epiphan and Slack, so the Pi needs internet access and nothing else. The encoders on the stand
report to Epiphan Edge over their own connection; the Pi never talks to them directly.

## Before the show

1. **Phone hotspot.** Give it a fixed name and password. Set its timeout to never, so it stays on when idle.
   Check that your mobile plan allows hotspot data.
2. **Put the Pi on the hotspot.** With a keyboard and screen on the Pi:

    ```bash
    sudo nmcli dev wifi connect "HOTSPOT-NAME" password "HOTSPOT-PASSWORD"
    sudo raspi-config nonint do_ssh 0
    sudo raspi-config nonint do_hostname fleetwatch
    sudo reboot
    ```

    Add a second network, such as a travel router or the venue Wi-Fi, the same way as a fallback. The Pi joins
    whichever it finds.

3. **Install and sign in:**

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
    ```

    Open the sign-in link on the phone and pick the team to show. When the last page fails to load, copy its
    address and paste it into the Pi's terminal. The installer ends with `fleetwatch doctor`.

4. **Keep it running after a reboot:** `sudo loginctl enable-linger $USER`.

Use a team made for demos where you can. The digest names rooms and devices, and people at the booth will see it.

## At the show

| To | Run on the Pi |
|---|---|
| Check everything is ready | `cd ~/fleetwatch && uv run fleetwatch doctor` |
| Watch the digest live | `journalctl --user -u fleetwatch -f` |
| Show a heartbeat now | `cd ~/fleetwatch && uv run fleetwatch digest` |
| See open items | `cd ~/fleetwatch && uv run fleetwatch status` |

With a Slack token in `.env`, the digest and the Ready / Not ready checks also show in the Slack app on the phone.

## If the network fails

Show the offline demo. It runs a full heartbeat on the bundled sample fleet, including a *Ready* and a
*Not ready* check before an event, with no network at all:

```bash
cd ~/fleetwatch && uv run fleetwatch digest --replay tests/fixtures
```

See [Replay mode](replay.md) for what the sample contains.
