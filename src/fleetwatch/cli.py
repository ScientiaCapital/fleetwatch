"""fleetwatch: login | digest | run | status | doctor | logout. Observe-only in v0.1."""

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from fleetwatch.config import Settings
from fleetwatch.epiphan.auth import FileTokenStorage
from fleetwatch.epiphan.mcp import EpiphanClient
from fleetwatch.epiphan.replay import ReplayClient
from fleetwatch.heartbeat import tick
from fleetwatch.notify.slack import Notifier
from fleetwatch.policy import load_policy, load_tool_policy
from fleetwatch.state import State


def _build(settings: Settings, interactive: bool, replay: str | None = None):
    tools = load_tool_policy(settings.tool_policy_file)
    if replay:
        client = ReplayClient(Path(replay), tools)
        state = State(":memory:")  # a replay never touches the real history
    else:
        storage = FileTokenStorage(settings.token_file)
        client = EpiphanClient(
            settings.epiphan_mcp_url,
            tools,
            storage=storage,
            static_token=settings.epiphan_token,
            callback_port=settings.oauth_callback_port,
            interactive=interactive,
        )
        state = State(settings.state_db)
    return client, state, Notifier(settings.slack_bot_token, settings.slack_channel)


async def _login(settings: Settings) -> None:
    client, _, _ = _build(settings, interactive=True)
    async with client:
        fleet = await client.call("get_devices_in_my_team")
    n = len(fleet.get("devices", fleet)) if isinstance(fleet, (dict, list)) else 0
    print(f"Signed in. This team has {n} devices. Token saved to {settings.token_file}.")


async def _digest(settings: Settings, replay: str | None) -> None:
    client, state, notifier = _build(settings, interactive=False, replay=replay)
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
                logging.getLogger("fleetwatch").warning("heartbeat failed: %s", e)
            first = False
            await asyncio.sleep(policy.heartbeat_seconds)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="fleetwatch",
        description="Fleetwatch for Epiphan Edge: an always-on, read-only watcher for your Pearl and EC20 fleet.",
    )
    p.add_argument("command", choices=["login", "digest", "run", "status", "doctor", "logout"])
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--check", action="store_true", help="status: exit 1 unless a heartbeat read the fleet recently (health check)"
    )
    p.add_argument(
        "--replay", metavar="DIR", help="digest: use saved tool results from DIR instead of Epiphan (no sign-in)"
    )
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
    elif args.command == "digest":
        asyncio.run(_digest(settings, args.replay))
    elif args.command == "run":
        asyncio.run(_run(settings))
    elif args.command == "doctor":
        from fleetwatch.doctor import exit_code, print_report, run_checks

        checks = run_checks(settings)
        print_report(checks)
        raise SystemExit(exit_code(checks))
    elif args.check:
        from fleetwatch.heartbeat import health

        ok, msg = health(State(settings.state_db), load_policy(settings.policy_file).heartbeat_seconds)
        print(msg)
        raise SystemExit(0 if ok else 1)
    else:
        state = State(settings.state_db)
        items = state.open_findings()
        signed_in = FileTokenStorage(settings.token_file).has_tokens() or bool(settings.epiphan_token)
        print(
            f"Signed in: {'yes' if signed_in else 'no (run fleetwatch login)'}\nOpen items: {len(items)}  ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)"
        )
        for f in items:
            print(f"  {f.priority.value}: {f.what}")


if __name__ == "__main__":
    main()
