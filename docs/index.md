# Fleetwatch for Epiphan Edge

An always-on, read-only watcher for an Epiphan Edge fleet. It checks every room on a heartbeat, posts a calm Slack
digest when something changes, and says **Ready** or **Not ready** 30 minutes before each scheduled class.

v0.1 is **observe-only**. It can't change a device: write tools are refused inside the client before any request
leaves the machine.

```mermaid
flowchart LR
  E[Epiphan Edge<br/>MCP server] -->|read tools only| G[Guard and<br/>redaction]
  G --> S[Scanner and<br/>pre-class readiness]
  S --> D[(SQLite<br/>open items)]
  D -->|only what changed| N[Slack digest<br/>or console]
```

- New here? Start with [Install](install.md), or try it with no account in [Replay mode](replay.md).
- Deciding whether to trust it? Read the [Security model](security.md).
