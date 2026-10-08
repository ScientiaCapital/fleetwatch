# Fleetwatch for Epiphan Edge

An always-on, read-only watcher for an Epiphan Edge fleet. It checks every room on a heartbeat, posts a calm Slack
or Microsoft Teams digest when something changes, and says Ready or Not ready 30 minutes before each scheduled event.

Version 0.1 is observe-only. Fleetwatch never calls a write tool: its client refuses them before any request
leaves the machine. The Epiphan sign-in it stores can write, so use a least-access account.

```mermaid
flowchart LR
  E[Epiphan Edge<br/>MCP server] -->|read tools only| G[Guard and<br/>redaction]
  G --> S[Scanner and<br/>pre-event readiness]
  S --> D[(SQLite<br/>open items)]
  D -->|only what changed| N[Slack digest<br/>or console]
```

- New here? Start with [Install](install.md), or try it with no account in [Replay mode](replay.md).
- Deciding whether to trust it? Read the [Security model](security.md).
