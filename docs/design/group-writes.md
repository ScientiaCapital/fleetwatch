# Design: group changes and the write fence

Status: a proposal. Nothing here is built. The group tools are on the `write` list in `tool_policy.yaml` so the
guard refuses them, and none is under `propose`, so none can be proposed. This page says what has to be true before
one can be.

## Why groups need their own rule

Epiphan Edge teams can organize devices into groups, and the groups can sit inside other groups. A device can belong
to more than one group. Changing a group changes who can see a device and what a policy scope reaches, and one change
can touch many devices at once. The [approved-writes design](approved-writes.md) fences writes by device, so a group
change needs a rule of its own.

## What the current fence already does

- Writes use the sandbox sign-in, which reaches the sandbox team only. A group ID that sign-in can name belongs to
  the sandbox team.
- If `FLEETWATCH_WRITE_TEAM_ID` is set, the executor also checks the team the sign-in reports.

What it doesn't do: stop a group change from touching a device that is in the sandbox team but not on
`FLEETWATCH_WRITE_DEVICE_IDS`. Moving or deleting a group can change the membership of devices that were never
meant for testing. The device allowlist can't see that, because the tool's arguments name a group.

## Proposed rule

1. **A group allowlist.** `FLEETWATCH_WRITE_GROUP_IDS` lists the sandbox groups a change may name. Like the other
   fence settings, it fails closed: with no group allowlist, no group tool can be proposed or run.
2. **Every device a change touches must be in the device fence too.** For a change that names devices
   (`add_devices_to_group`, `remove_devices_from_groups`, `move_devices_to_group`), each device ID must pass the
   existing device check. For a change that names only groups (`rename_group`, `move_group`, `delete_groups`), the
   executor reads the members of each group (`get_group_devices`) first and refuses unless every member passes the
   device check.
3. **Both ends of a move.** `move_group` and `move_devices_to_group` check the source and the destination against
   the group allowlist. `create_group` checks the parent. A group with no parent (a root group) is refused.
4. **A fresh read, as for every write.** The executor reads `get_groups` and `get_group_devices` with the sandbox
   sign-in right before the change, and refuses if a group isn't there, has moved, or has members that weren't on the
   card.
5. **One group per proposal.** `max_targets` stays at 1. `delete_groups` takes a list, but a proposal names one group.
6. **The card shows the path.** The approval card shows the group's name and where it sits in the tree, the devices
   affected by name and ID, and for a removal whether it removes the device from this group only or from every group.
   Group names are untrusted text, like device names.

## Order of work

Calm changes first, because they add structure without removing any: `create_group`, `rename_group`, then
`add_devices_to_group`, which keeps a device's other memberships. The changes that take things away or move them
(`remove_devices_from_groups`, `move_devices_to_group`, `move_group`, `delete_groups`) stay on the disruptive list
until the calm ones have run on the sandbox team. Each tool gets a reviewed schema with a `version`, as the others do.

## What reading groups needs

Fleetwatch already scopes by a device's group (`scope.groups`), matching the single group ID on the device. With
nested groups and more than one membership per device, that match can miss devices. The group read tools
(`get_groups`, `get_group_devices`, and the `group` filter on `get_devices_in_my_team`) are on the `read` list, but
nothing calls them yet.

Open until a sandbox read:

- The shape of `get_groups` and `get_group_devices` results, and whether a device in the device list carries every
  group it belongs to or only one.
- Whether `scope.groups` should include a group's subgroups. The tool's `group` filter does.
- Whether a limited-access sign-in changes what these tools return.

The parsers will be written from a captured, anonymized result, as the others were, and not before. Fixtures use
neutral names and made-up IDs.

## Tests the group fence needs

- A group change is refused with no group allowlist, a group outside it, or a member outside the device fence.
- A move is refused when either end is outside the allowlist.
- A group that changed between the card and the approval is refused, and the approval is spent.
- `guard()` still refuses every group tool through the read-only client.
