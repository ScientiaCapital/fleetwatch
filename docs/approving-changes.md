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

On start, the console prints a page secret once. Type it on the first screen. It is never put in a URL or a log.
After five wrong tries the form locks for a minute. The browser then keeps a random session ID, not the secret.

## What a card shows

- The action in plain words, in English or in Spanish if your browser asks for it.
- Each target device: ID, current name, model, online state and recording state, read fresh just now.
- Every argument as exact JSON, so you see what would be sent.
- What will happen and how to undo it.
- The assistant's reason, in a box marked "Written by the assistant, not checked".

Cards come oldest first, one at a time. Deny comes first and has the focus. Approve is never focused. Disruptive
actions, such as a reboot or a firmware update, ask for one more confirmation that names the room.

## When only Deny is offered

The page shows only Deny when the arguments can't be shown in full (hidden characters, too long), the change doesn't
match its schema, the fresh read failed, a target is missing, or the device state changed since the proposal.

## Limits

- An approval works once, expires after five minutes, and is bound to the exact tool and arguments.
- Changes run only on the sandbox team. Every other team stays read-only.
- A write never retries. If the result is unknown, the page says "may or may not have run" so you can check the device.
- After five approvals in one session, the page suggests a break.
- Browsers send cookies to every port on the same host. Don't browse other local web servers, such as a dev server
  on `localhost:3000`, while the approval page is open, and use `127.0.0.1` rather than `localhost`.

See [the design](design/approved-writes.md) for the full reasoning and [Security model](security.md) for what the
guard covers.
