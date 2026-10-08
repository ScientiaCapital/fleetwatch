# Slack commands

Ask Fleetwatch about a room from Slack:

```
/fleetwatch check Main Stage
/fleetwatch what's offline
/fleetwatch what needs attention
```

The reply is **ephemeral**, so only the person who asked sees it. It is the same answer `fleetwatch ask` gives:
the room's Ready / Not ready check for its next event, or what is open on it. The command is read-only. It reads
what the heartbeat already saw and never calls Epiphan or changes a device. What you type is treated as a
question and nothing else.

## How it connects

Fleetwatch uses Slack **Socket Mode**: `fleetwatch run` opens an outbound websocket to Slack, so nothing listens
on an inbound port and you don't need a public URL. If Slack is unreachable, the heartbeat carries on and the
connection is retried on each beat.

## Set it up

1. Create a Slack app from the manifest below (api.slack.com/apps > Create New App > From a manifest), or add
   the same settings to the app you already use for the digest.
2. **Basic Information > App-Level Tokens**: generate a token with the `connections:write` scope. It starts
   `xapp-`. Put it in `.env`:

    ```
    FLEETWATCH_SLACK_APP_TOKEN=xapp-...
    ```

3. Reinstall the app to the workspace so the bot token picks up the `commands` scope (and `usergroups:read` if
   you use a group).
4. Say who may ask, in `policy.yaml`. Empty means nobody, so the command answers no one until you add people:

    ```yaml
    slack:
      allowed_user_ids: [U012ABCDEF]   # Slack profile > ... > Copy member ID
      allowed_usergroup: S012ABCDEF    # optional: everyone in this user group
    ```

    Group membership is looked up with `usergroups.users.list` and cached for five minutes. If the lookup fails,
    nobody from the group is let in until it works again.

5. Run `fleetwatch doctor`. The *Slack commands* row checks the token is an `xapp-` token and that someone is
   allowed. It doesn't connect to Slack.
6. Restart the service (`fleetwatch run`). The command only runs inside `fleetwatch run`.

## App manifest

```yaml
display_information:
  name: Fleetwatch
  description: Read-only watcher for an Epiphan Edge fleet
features:
  bot_user:
    display_name: Fleetwatch
    always_online: true
  slash_commands:
    - command: /fleetwatch
      description: Ask Fleetwatch about a room (only you see the answer)
      usage_hint: check Main Stage
      should_escape: false
oauth_config:
  scopes:
    bot:
      - chat:write          # the digest and Ready / Not ready posts
      - commands            # /fleetwatch
      - usergroups:read     # only if you use slack.allowed_usergroup
settings:
  interactivity:
    is_enabled: false
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

The app-level `xapp-` token with `connections:write` is created by hand in step 2. Manifests can't create it.

## Who sees what

| Person | Reply |
|---|---|
| On `allowed_user_ids`, or in `allowed_usergroup` | The answer, visible only to them |
| Anyone else | "Fleetwatch only answers people on its allowlist." Nothing about the fleet |

Each command is written to the local audit log with the Slack member ID and whether it was answered. The text
of the question is not stored.
