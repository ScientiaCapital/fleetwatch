# Policy

Two files control behaviour. Both are plain YAML in the repo folder.

## policy.yaml: how it behaves

| Setting | Default | Meaning |
|---|---|---|
| `heartbeat_seconds` | `180` | How often to look at the fleet |
| `vertical` | `events` | The word for a scheduled recording: `events` (event), `education` (class), `business` (meeting), `courts` (hearing), `worship` (service) |
| `lead_minutes` | `30` | Post Ready / Not ready this long before each event (`preclass_lead_minutes` still works) |
| `remind_after_minutes` | `240` | Repeat an open item at most this often |
| `sweep_at` | `"03:00"` | Nightly sweep, local time. In quiet hours, the summary waits until they end. `""` turns it off |
| `quiet_hours.start` / `.end` | `22:00` / `06:30` | Only *Fix first* items are posted in this window, in the machine's local time |
| `scope.groups` | `[]` | Only watch these Edge groups; empty watches the whole team |
| `scope.exclude_devices` | `[]` | Device names to ignore |
| `thresholds.cpu_load_pct` | `90` | CPU load that counts as busy |
| `thresholds.cpu_temp_c` | `80` | Temperature that counts as running hot |
| `thresholds.recent_reboot_minutes` | `30` | Uptime below this counts as a recent restart |
| `slack.allowed_user_ids` | `[]` | Slack member IDs that may use `/fleetwatch` ([Slack commands](slack-commands.md)); empty with no group means nobody |
| `slack.allowed_usergroup` | none | A Slack user group ID whose members may use `/fleetwatch`; needs `usergroups:read` |
| `autonomy` | `observe` | v0.1 accepts only `observe`; any other value stops start-up |
| `dry_run` | `true` | Forced to `true` in v0.1 |

## tool_policy.yaml: what it may call

Only tools under `read` are ever called. Anything else, including tools Epiphan adds later, is refused by the
client guard. The `write` and `disruptive` lists are kept for the approval flow planned after v0.1.

The file ships inside the package (`src/fleetwatch/tool_policy.yaml`; the copy at the repo root is a link to it),
so the list never depends on the folder Fleetwatch starts in. Loading stops with an error if a known write tool is
under `read`, or a `read` entry doesn't start with `get_` or `kb_`.

To call fewer tools, point `FLEETWATCH_TOOL_POLICY_FILE` at your own file with a shorter `read` list. It can only
remove tools: a tool that isn't on the shipped list stops start-up with an error.
