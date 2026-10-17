# Policy

Two files control behavior. Both are plain YAML in the repo folder.

## policy.yaml: how it behaves

| Setting | Default | Meaning |
|---|---|---|
| `heartbeat_seconds` | `180` | How often to look at the fleet |
| `vertical` | `events` | The word for a scheduled recording: `events` (event), `education` (class), `business` (meeting), `courts` (hearing), `worship` (service) |
| `lead_minutes` | `30` | Post Ready / Not ready this long before each event (`preclass_lead_minutes` still works) |
| `remind_after_minutes` | `240` | Repeat an open item at most this often |
| `sweep_at` | `"03:00"` | Nightly sweep, local time. In quiet hours, the summary waits until they end. `""` turns it off |
| `quiet_hours.start` / `.end` | `22:00` / `06:30` | Only Fix first items are posted in this window, in the machine's local time |
| `scope.groups` | `[]` | Only watch these Edge groups; empty watches the whole team |
| `scope.exclude_devices` | `[]` | Device names to ignore |
| `thresholds.cpu_load_pct` | `90` | CPU load that counts as busy |
| `thresholds.cpu_temp_c` | `80` | Temperature that counts as running hot |
| `thresholds.recent_reboot_minutes` | `30` | Uptime below this counts as a recent restart |
| `slack.allowed_user_ids` | `[]` | Slack member IDs that may use `/fleetwatch` ([Slack commands](slack-commands.md)); empty with no group means nobody |
| `slack.allowed_usergroup` | none | A Slack user group ID whose members may use `/fleetwatch`; needs `usergroups:read` |
| `autonomy` | `observe` | `observe` or `propose`. `propose` is for the v0.2 assistant and changes nothing yet; any other value stops start-up |
| `dry_run` | `true` | Forced to `true` in v0.1 |

## tool_policy.yaml: what it may call

Only tools under `read` are ever called. Anything else, including tools Epiphan adds later, is refused by the
client guard. The `write` and `disruptive` lists and the `propose` section feed the v0.2 approval flow.

The file ships inside the package (`src/fleetwatch/tool_policy.yaml`; the copy at the repo root is a link to it),
so the list never depends on the folder Fleetwatch starts in. Loading stops with an error if a known write tool is
under `read`, or a `read` entry doesn't start with `get_` or `kb_`.

To call fewer tools, point `FLEETWATCH_TOOL_POLICY_FILE` at your own file with a shorter `read` list. It can only
remove tools: a tool that isn't on the shipped list stops start-up with an error.

## tool_policy.yaml: what it may propose (v0.2)

The `propose` section lists the write tools the assistant may *suggest*. A person still approves each one on the
[approval page](approving-changes.md), and the guard still refuses every write tool outside the approved path. A
write tool that isn't listed here can never be proposed. Each entry has:

| Key | What it means |
|---|---|
| `max_targets` | How many devices one proposal may touch. |
| `disruptive` | `true` or `false`. Left out means `true`. A tool on the `disruptive` list can't say `false`. |
| `disruptive_when` | Optional. One enum argument and the values of it that are disruptive, as in `{action: [stop]}`. Any other known value is calm. |
| `stops_recording` | Optional. The argument value that ends a recording. That change is refused while the room is recording, and for an event that is on now or inside the window. |
| `lead_minutes` | Optional. How long before an event a disruptive change is refused. Left out means the `lead_minutes` in `policy.yaml`. `batch_firmware_update` uses 120. |
| `schema` | The arguments, with a `version`. `pending-live-run` loads but can't be proposed. Raise `version` whenever the fields change, because an approval is bound to the version it was made under. |

A disruptive change is refused when a room is live or an event is about to start. Writes that take a room offline
or interrupt a class are on the `disruptive` list.

`FLEETWATCH_TOOL_POLICY_FILE` can narrow all of this and never widen it. It can remove entries, lower `max_targets`,
turn `disruptive` on, widen `disruptive_when`, raise `lead_minutes`, or set a schema back to pending. It can't add an
entry, raise `max_targets`, turn `disruptive` off, add `stops_recording`, or change a schema.
