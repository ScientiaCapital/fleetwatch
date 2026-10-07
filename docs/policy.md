# Policy

Two files control behaviour. Both are plain YAML in the repo folder.

## policy.yaml: how it behaves

| Setting | Default | Meaning |
|---|---|---|
| `heartbeat_seconds` | `180` | How often to look at the fleet |
| `preclass_lead_minutes` | `30` | Post Ready / Not ready this long before each class |
| `remind_after_minutes` | `240` | Repeat an open item at most this often |
| `quiet_hours.start` / `.end` | `22:00` / `06:30` | Only *Fix first* items are posted in this window, in the machine's local time |
| `scope.groups` | `[]` | Only watch these Edge groups; empty watches the whole team |
| `scope.exclude_devices` | `[]` | Device names to ignore |
| `thresholds.cpu_load_pct` | `90` | CPU load that counts as busy |
| `thresholds.cpu_temp_c` | `80` | Temperature that counts as running hot |
| `thresholds.recent_reboot_minutes` | `30` | Uptime below this counts as a recent restart |
| `autonomy` | `observe` | v0.1 accepts only `observe`; any other value stops start-up |
| `dry_run` | `true` | Forced to `true` in v0.1 |

## tool_policy.yaml: what it may call

Only tools under `read` are ever called. Anything else, including tools Epiphan adds later, is refused by the
client guard. The `write` and `disruptive` lists are kept for the approval flow planned after v0.1.
