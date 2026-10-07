# What it posts

- **Needs attention**, with priority in words:
    - *Fix first*: an event won't record. The unit is offline, or a channel has no picture.
    - *Fix soon*: firmware behind the rest of its family, restarted recently, or running hot.
    - *When convenient*: everything else worth knowing.
- The same problem is posted once, reminded at most every 4 hours, and closed with **Back to normal**.
- Storage warnings are an FYI line, never a problem. Pearls on a CMS record locally and upload afterwards.
- Quiet hours (22:00 to 06:30 by default) only let *Fix first* items through.
- Before each event: `Ballroom B · Opening keynote at 9:00 AM: Ready, with notes`. A room is *Not ready* when it is offline
  or a channel it records has no picture. Low storage never makes a room Not ready.

Example from the sample fleet in [Replay mode](replay.md):

```text
*Fleet check*
• *Fix first*: Room 312 Pearl Mini is offline. Events in that room won't record or stream until it's back.
  _Check power and the network cable at the unit._
• *Fix soon*: Hall A Auditorium runs firmware 4.24.5; others like it run 4.24.6. Works fine today; keeps the
  fleet consistent. _Update firmware when the room is free._
FYI: 3 Pearls have little or no local space left. That's normal when recordings upload to your CMS.
```
