# Approving changes (v0.2)

!!! note "In progress"
    This is part of v0.2 and is tested only against a fake Epiphan server and sample data. It hasn't run against a
    real team. Fleetwatch v0.1 stays read-only.

When the assistant wants a device changed, it can only propose the change. A person approves each one on a local
page, one at a time. Nothing runs until you press Approve.

## Start the page

```
fleetwatch approve --serve
```

It listens on `127.0.0.1` at port 8767 (`FLEETWATCH_APPROVE_PORT`) and nowhere else. It won't start unless
`policy.yaml` says `autonomy: propose`, a sandbox sign-in exists (`fleetwatch login --sandbox`), and the write
fence is set: the sandbox devices in `FLEETWATCH_WRITE_DEVICE_IDS`, or `FLEETWATCH_WRITE_TEAM_ID`. To try it
without signing in to anything, add `--replay tests/fixtures`. A replay run records the change in memory and
sends nothing to Epiphan.

## The page secret

On start, the page makes a secret. In a terminal, it prints once on the console. With no terminal (launchd or
systemd), it goes to a file named `approve-page-secret` in the state folder, readable only by you, and only the path
is printed. The file is deleted when the page stops. Type the secret on the first screen. It is never put in a URL
or a log. The browser then keeps a random session ID, not the secret.

Wrong entries slow down. Each address gets three wrong entries free. Every wrong entry after that starts a wait: 2
seconds, then 4, 8 and so on up to a minute. Entries made during a wait aren't checked. A correct secret is not accepted during a wait,
because that would make guessing unlimited. On your own computer every program shares one address, so a program that
keeps guessing can still take the slot after each wait. The secret is long and random, so guessing it is not
realistic. If you're ever unsure, stop the page and start it again: it makes a new secret.

## What a card shows

- The action in plain words, in English or in Spanish if your browser asks for it.
- Each target device: ID, current name, model, online state and recording state, read fresh just now.
- Every argument as exact JSON, so you see what would be sent.
- What will happen and how to undo it.
- The assistant's reason, in a box marked "Written by the assistant, not checked".

Cards come oldest first, one at a time. Deny comes first and has the focus. Approve is never focused. Disruptive
actions, such as a reboot, a firmware update or stopping a recording, ask for one more confirmation that names the
room. Starting a recording isn't disruptive, so it doesn't.

When the room is recording, or has an event on now or starting soon, a disruptive card says so in plain words, for
example "Room 204 has an event that starts in 12 minutes, so this change would be blocked." That's information: Fleetwatch
checks again the moment you approve, and refuses then if the room is still busy. Stopping a recording is the one
exception to the recording rule: a room that is recording can be stopped, unless an event is on now or starts soon. A firmware update looks further ahead
(120 minutes) than other changes, because the update can still be running when the event starts.

If no device returned an event schedule at all (a schedule from a content management system that Epiphan doesn't
report, for example), a disruptive card says "No event schedule came back for any device" and asks you to check the
room yourself, because Fleetwatch can't see an event that's about to start.

## When only Deny is offered

The page shows only Deny when the arguments can't be shown in full (hidden characters, too long), the change doesn't
match its schema, the fresh read failed, a target is missing, or the device state changed since the proposal.

## Limits

- An approval works once, expires after five minutes, and is bound to the exact tool and arguments.
- Changes run only on the sandbox team. Every other team stays read-only.
- A write never retries. If the result is unknown, the page says "may or may not have run" so you can check the device.
- After five approvals in one session, the page suggests a break. That's a warning, not a limit.
- At most 3 proposals wait at once and 10 can be made per hour. When the queue is full, the page says "Proposal
  queue full", and `fleetwatch doctor` shows the counts under "Proposal queue". A proposal a person denied doesn't
  count toward the hourly 10. After three denials of the same change on the same devices, it's paused for an hour.
- "Deny all pending" clears the queue in one step. There is no approve-all: every approval is one card, one click.
- Browsers send cookies to every port on the same host. Don't browse other local web servers, such as a dev server
  on `localhost:3000`, while the approval page is open, and use `127.0.0.1` rather than `localhost`.

See [the design](design/approved-writes.md) for the full reasoning and [Security model](security.md) for what the
guard covers.
