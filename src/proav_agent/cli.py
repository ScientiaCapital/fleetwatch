"""proav-agent: login | once | run | status. Observe-only in v0.1."""

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from proav_agent.config import Settings
from proav_agent.epiphan.auth import FileTokenStorage
from proav_agent.epiphan.mcp import EpiphanClient
from proav_agent.heartbeat import tick
from proav_agent.notify.slack import Notifier
from proav_agent.policy import load_policy, load_tool_policy
from proav_agent.state import State


def _build(settings: Settings, interactive: bool) -> tuple[EpiphanClient, State, Notifier]:
    tools = load_tool_policy(settings.tool_policy_file)
    storage = FileTokenStorage(settings.token_file)
    client = EpiphanClient(
        settings.epiphan_mcp_url,
        tools,
        storage=storage,
        static_token=settings.epiphan_token,
        callback_port=settings.oauth_callback_port,
        interactive=interactive,
    )
    return client, State(settings.state_db), Notifier(settings.slack_bot_token, settings.slack_channel)


async def _login(settings: Settings) -> None:
    client, _, _ = _build(settings, interactive=True)
    async with client:
        fleet = await client.call("get_devices_in_my_team")
    n = len(fleet.get("devices", fleet)) if isinstance(fleet, (dict, list)) else 0
    print(f"Signed in. This team has {n} devices. Token saved to {settings.token_file}.")


async def _once(settings: Settings) -> None:
    client, state, notifier = _build(settings, interactive=False)
    policy = load_policy(settings.policy_file)
    async with client:
        text = await tick(client, state, policy, notifier, first_run=True)
    if text is None:
        print("Nothing new to post.")


async def _run(settings: Settings) -> None:
    client, state, notifier = _build(settings, interactive=False)
    policy = load_policy(settings.policy_file)
    first = not state.open_findings()
    async with client:
        while True:
            try:
                await tick(client, state, policy, notifier, first_run=first)
            except Exception as e:  # noqa: BLE001  (keep the loop alive; the next beat retries)
                logging.getLogger("proav").warning("heartbeat failed: %s", e)
            first = False
            await asyncio.sleep(policy.heartbeat_seconds)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="proav-agent", description="Always-on, read-only watcher for an Epiphan Edge fleet."
    )
    p.add_argument("command", choices=["login", "once", "run", "status", "logout"])
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    settings = Settings()
    if args.command == "login":
        asyncio.run(_login(settings))
    elif args.command == "logout":
        FileTokenStorage(settings.token_file).clear()
        print("Signed out.")
    elif args.command == "once":
        asyncio.run(_once(settings))
    elif args.command == "run":
        asyncio.run(_run(settings))
    else:
        state = State(settings.state_db)
        items = state.open_findings()
        signed_in = FileTokenStorage(settings.token_file).has_tokens() or bool(settings.epiphan_token)
        print(
            f"Signed in: {'yes' if signed_in else 'no (run proav-agent login)'}\nOpen items: {len(items)}  ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)"
        )
        for f in items:
            print(f"  {f.priority.value}: {f.what}")


if __name__ == "__main__":
    main()
