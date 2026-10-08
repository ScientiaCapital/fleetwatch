# What it posts

- Needs attention, with priority in words:
    - Fix first: an event won't record. The Pearl is offline, or a channel has no picture.
    - Fix soon: an EC20 camera is offline, firmware is behind the rest of its family, a unit restarted recently, or
      it's running hot. An EC20 doesn't record or stream by itself. When a Pearl channel loses its
      picture, that channel is its own Fix first item.
    - When convenient: everything else worth knowing.
- The same problem is posted once, reminded at most every 4 hours, and closed with Back to normal.
- Storage warnings are an FYI line, never a problem. Pearls on a CMS record locally and upload afterwards.
- Quiet hours (22:00 to 06:30 by default) only let Fix first items through.
- Before each event: `Ballroom B · Opening keynote at 9:00 AM: Ready, with notes`. A room is Not ready when it's offline
  or a channel it records has no picture. Low storage never makes a room Not ready.
- If the verdict changes before the event starts, Fleetwatch posts once more for each change, with the reasons:
  `Ballroom B · Opening keynote at 9:00 AM: Now not ready (was Ready)`, or after a fix,
  `Ballroom B · Opening keynote at 9:00 AM: Ready now (was Not ready)`. The same verdict is never posted twice,
  even when the reasons change. Nothing is posted after the event starts.
- Readiness lines, first and changed, also post in quiet hours: an early event has people in the room who need to
  know.
- Once a day (`sweep_at`, 03:00 by default): a Nightly sweep line with who is offline, who is behind on firmware,
  and what changed since the last sweep. It opens no new items, and in quiet hours it waits for the morning.
  `fleetwatch history` shows the days side by side.
- Room notes: `fleetwatch note "Room 204 Pearl Mini" "Bulb replaced"` saves a note on one room. It shows as a
  quoted line under that room's item in the digest, and in `fleetwatch ask` answers about that room. If the name
  matches more than one room, nothing is saved and the matches are listed. `fleetwatch notes` lists them, newest
  first; add a room name or `--search text` to narrow it. The author is your login name unless you pass
  `--author`. Notes are redacted like everything else, kept to one line of 280 characters, and stay in the local
  state DB: adding one never signs in or calls Epiphan.

Example from the sample fleet in [Replay mode](replay.md):

```text
*Fleet check*
• *Fix first*: Room 312 Pearl Mini is offline. Events in that room won't record or stream until it's back.
  _Check power and the network cable at the unit._
• *Fix soon*: Hall A Auditorium runs firmware 4.24.5; others like it run 4.24.6. Works fine today; keeps the
  fleet consistent. _Update firmware when the room is free._
FYI: 3 Pearls have little or no local space left. That's normal when recordings upload to your CMS.
```

With a note on the room (the date is when it was left):

```text
• *Fix first*: Room 312 Pearl Mini is offline. Events in that room won't record or stream until it's back.
  _Check power and the network cable at the unit._
  ↳ “Power strip under the lectern was switched off” (alex, Oct 7)
```

## When a heartbeat can't read the fleet

A read that fails, comes back as text instead of a device list, or suddenly lists 0 devices after a real fleet,
posts nothing. Fleetwatch logs a warning and keeps every open item open, so a bad read never shows up as
Back to normal. The next heartbeat opens a fresh connection to Epiphan. After three failed heartbeats in a row,
`fleetwatch run` exits with code 75, and launchd, systemd, or Docker starts it again.

`fleetwatch ask` and the `ask --serve` page show when the fleet was last read ("Last checked 14:05"), so an old
answer looks old.
