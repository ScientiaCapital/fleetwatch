"""fleetwatch: login | digest | run | status | doctor | logout. Observe-only in v0.1."""

import argparse
import asyncio
import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fleetwatch.config import Settings, reveal
from fleetwatch.epiphan.capture import CapturingClient
from fleetwatch.epiphan.mcp import EpiphanClient
from fleetwatch.epiphan.replay import ReplayClient
from fleetwatch.epiphan.token_store import make_token_store
from fleetwatch.heartbeat import FailedRead, tick
from fleetwatch.logsetup import configure_logging
from fleetwatch.notify import from_settings
from fleetwatch.policy import load_policy, load_tools
from fleetwatch.state import State


def _make_client(settings: Settings, interactive: bool = False) -> EpiphanClient:
    return EpiphanClient(
        settings.epiphan_mcp_url,
        load_tools(settings.tool_policy_file),
        storage=make_token_store(settings.token_store, settings.token_file),
        static_token=reveal(settings.epiphan_token) or None,
        callback_port=settings.oauth_callback_port,
        interactive=interactive,
    )


def _build(settings: Settings, interactive: bool, replay: str | None = None, capture: str | None = None):
    tools = load_tools(settings.tool_policy_file)
    if replay:
        client = ReplayClient(Path(replay), tools)
        state = State(":memory:")  # a replay never touches the real history
    else:
        client = _make_client(settings, interactive)
        state = State(settings.state_db)
    if capture:  # every result the heartbeat reads, redacted, saved as replay files
        client = CapturingClient(client, Path(capture))
    return client, state, from_settings(settings)


def _sandbox_store(settings: Settings):
    from fleetwatch.epiphan.executor import sandbox_store

    return sandbox_store(settings)


async def _login(settings: Settings, sandbox: bool = False) -> None:
    """Sign in. `sandbox`: the v0.2 sandbox sign-in, in its own token slot, never the normal one or its static
    token. The same OAuth flow; only where the token is kept differs."""
    if sandbox:
        store = _sandbox_store(settings)
        client = EpiphanClient(
            settings.epiphan_mcp_url,
            load_tools(settings.tool_policy_file),
            storage=store,
            callback_port=settings.oauth_callback_port,
            interactive=True,
        )
    else:
        client, _, _ = _build(settings, interactive=True)
        store = make_token_store(settings.token_store, settings.token_file)
    async with client:
        fleet = await client.call("get_devices_in_my_team")
    n = len(fleet.get("devices", fleet)) if isinstance(fleet, (dict, list)) else 0
    if sandbox:
        _refuse_overlapping_sandbox(settings, fleet)
        print(
            f"Signed in to the sandbox team. It has {n} devices. Token saved to {store.where}.\n"
            "Only v0.2 changes a person approves use this sign-in; the heartbeat never loads it."
        )
    else:
        print(f"Signed in. This team has {n} devices. Token saved to {store.where}.")


def _refuse_overlapping_sandbox(settings: Settings, raw: Any) -> None:
    """The sandbox must be a different team from the one the heartbeat watches. If any device it can see is one the
    normal sign-in already saw, the two sign-ins reach the same team: forget the sandbox sign-in and stop. (With no
    heartbeat history there's nothing to compare, so doctor and the fence are what's left; sign in normally and run
    one digest first.)"""
    from fleetwatch.epiphan.parse import parse_devices

    sandbox_ids = {i.lower() for i in parse_devices(raw, datetime.now(UTC)).devices}
    if not sandbox_ids:
        _refuse_sandbox(settings, "it lists no devices, so it can't be told apart from the normal team")
    known = {d.id.lower() for d in State(settings.state_db).devices()}
    if not known:
        print(
            "Warning: the normal sign-in has no saved devices yet, so the two teams can't be compared. Run one "
            "`fleetwatch digest` on the normal sign-in first, then sign in to the sandbox again."
        )
        return
    shared = sorted(sandbox_ids & known)
    if shared:
        _refuse_sandbox(
            settings,
            f"it can see {len(shared)} device(s) the normal sign-in watches (for example {shared[0]}), "
            "so it reaches the same team. Sign in with an account for the sandbox team only",
        )


def _refuse_sandbox(settings: Settings, why: str) -> None:
    """Forget the sandbox sign-in and stop. If it can't be forgotten, say so and how to do it by hand."""
    try:
        _sandbox_store(settings).clear()
        print(f"Sandbox sign-in refused and forgotten: {why}.")
    except Exception as e:  # noqa: BLE001 - a store error must not turn a refusal into a traceback
        from fleetwatch.redact import redact

        print(
            f"Sandbox sign-in refused: {why}. The saved token could not be removed ({redact(type(e).__name__)}): "
            "run  fleetwatch logout --sandbox"
        )
    raise SystemExit(2)


def _logout(settings: Settings, sandbox: bool = False) -> str:
    """Ask Epiphan to revoke the token (RFC 7009) when it offers that, then always delete it here. Revocation never
    stops the sign-out: whatever happens on the network, the store is cleared and a plain message comes back.
    `sandbox`: sign out of the sandbox slot only; the normal sign-in stays."""
    if sandbox:
        return "Sandbox sign-in: " + _logout_store(_sandbox_store(settings))
    return _logout_store(make_token_store(settings.token_store, settings.token_file))


def _logout_store(store) -> str:
    from fleetwatch.epiphan import auth
    from fleetwatch.redact import redact

    try:
        result = asyncio.run(auth.revoke_tokens(store))
    except Exception as e:  # noqa: BLE001 - a bug or a store read error must not leave the token on disk
        result = auth.Revocation("failed", redact(f"{type(e).__name__}: {e}")[: auth.REASON_MAX])
    finally:
        store.clear()
    if result.outcome == "revoked":
        return "Signed out. Epiphan revoked the token."
    if result.outcome == "no_endpoint":
        return "Signed out on this machine. Epiphan doesn't offer token revocation, so a copied token works until it expires."
    if result.outcome == "failed":
        return (
            f"Signed out on this machine. Epiphan didn't confirm the revocation ({result.reason}), "
            "so a copied token may work until it expires."
        )
    return "Signed out."


async def _digest(settings: Settings, replay: str | None, capture: str | None = None) -> None:
    client, state, notifier = _build(settings, interactive=False, replay=replay, capture=capture)
    policy = load_policy(settings.policy_file)
    if replay:  # a demo shows the whole digest at any hour; quiet hours protect real people, not a sample
        policy = dataclasses.replace(policy, quiet_start=None, quiet_end=None)
    async with client:
        try:
            text = await tick(client, state, policy, notifier, first_run=True)
        except FailedRead as e:
            print(f"Couldn't read the fleet, so nothing was posted: {e}")
            raise SystemExit(1) from None
    if text is None:
        print("Nothing new to post.")


class _Quiet:
    """A notifier that says nothing: `ask --replay` only needs the heartbeat to fill the state."""

    def post(self, text: str) -> bool:
        return True


def _sandbox_opener(settings: Settings):
    """Opens a read-only client on the sandbox sign-in (#93's slot), for the fresh read a proposal is checked and
    fingerprinted against, the same read the executor repeats. None when there is no usable sandbox sign-in: the
    assistant still answers, and refuses every proposal."""
    from fleetwatch.epiphan.token_store import TokenStoreError

    try:
        store = _sandbox_store(settings)
        if not store.has_tokens() or store.is_dead():
            return None
    except (TokenStoreError, ValueError, OSError):
        return None
    tools = load_tools(settings.tool_policy_file)
    return lambda: EpiphanClient(
        settings.epiphan_mcp_url, tools, storage=store, callback_port=settings.oauth_callback_port
    )


async def _assistant_answer(
    settings: Settings, key: str, question: str, state: State, policy, reader, fleet, sandbox=None, **extra
) -> str:
    """`extra` is `slot` (what a proposal is bound to) and `client` (a stand-in model, for tests)."""
    from fleetwatch import assistant
    from fleetwatch.fence import Fence

    extra.setdefault("fence", Fence.from_settings(settings))  # replay passes its own: the fixtures' devices
    reply = await assistant.answer(
        question,
        state=state,
        policy=policy,
        tools=load_tools(settings.tool_policy_file),
        reader=reader,
        api_key=key,
        model=settings.ai_model,
        sandbox=sandbox,
        fleet=fleet,
        **extra,
    )
    return reply.text


async def _ask(settings: Settings, question: str, replay: str | None, serve: bool, no_ai: bool = False) -> None:
    """With FLEETWATCH_ANTHROPIC_API_KEY set, the assistant answers (reads through the guarded client, or the replay
    files with --replay, so the model call is the only network use). Without a key, or with --no-ai, the keyword
    answer, exactly as before. The --serve page stays keyword-only for now."""
    from fleetwatch.ask import answer, last_checked
    from fleetwatch.heartbeat import snapshot

    policy = load_policy(settings.policy_file)
    key = "" if no_ai or serve else reveal(settings.anthropic_api_key)
    fleet = None
    if replay:
        policy = dataclasses.replace(policy, quiet_start=None, quiet_end=None)
        client, state, _ = _build(settings, interactive=False, replay=replay)
        async with client:
            await tick(client, state, policy, _Quiet(), first_run=True)
            fleet = await snapshot(client, datetime.now(UTC))
            if key:  # no sandbox here: a replay answers questions, and refuses every proposal
                text = await _assistant_answer(settings, key, question, state, policy, client, fleet)
                print(f"{text}\n\n{last_checked(state)}")
                return
    else:
        state = State(settings.state_db)  # reads what the heartbeat saved
        if key:  # the assistant reads the fleet through the guarded, read-only client; never an interactive sign-in
            print(f"{await _live_assistant(settings, key, question, state, policy)}\n\n{last_checked(state)}")
            return

    def ask(q: str) -> str:
        return answer(q, state, policy, fleet)

    if not serve:
        print(f"{ask(question)}\n\n{last_checked(state)}")
        return

    from fleetwatch.ask_page import serve as serve_page

    def rooms() -> list[str]:
        with_events = [fleet.devices[i].name for i in fleet.events if i in fleet.devices] if fleet else []
        return (with_events or [d.name for d in state.devices() if d.online])[:8]

    serve_page(ask, rooms, settings.ask_port, lambda: last_checked(state))


async def _live_assistant(settings: Settings, key: str, question: str, state: State, policy) -> str:
    from fleetwatch import assistant
    from fleetwatch.redact import redact

    try:
        async with _make_client(settings) as reader:
            sandbox = _sandbox_opener(settings)
            return await _assistant_answer(settings, key, question, state, policy, reader, None, sandbox)
    except Exception as e:  # noqa: BLE001  (no sign-in, or Epiphan unreachable: the keyword answer still works)
        logging.getLogger(__name__).warning("assistant couldn't read the fleet: %s", redact(type(e).__name__))
        return assistant.fallback(question, state, policy, None, None, "no_fleet").text


def _approve_ask(
    settings: Settings,
    state: State,
    policy,
    key: str,
    replay: str | None,
    model_client=None,
    replay_now=None,
    no_ai: bool = False,
):
    """The approval page's chat box. Sync, because the page's server is single-threaded: one question at a time, each
    in its own event loop. Replay reads the fixtures for both the model's reads and the proposal check, and binds
    proposals to the replay slot. With no key (or --no-ai) it's the keyword answer, with the note that says so."""
    from fleetwatch import assistant

    if not key:
        why = "no_ai" if no_ai else "no_key"
        return lambda q: assistant.fallback(q, state, policy, None, None, why).text
    if not replay:
        return lambda q: asyncio.run(_live_assistant(settings, key, q, state, policy))
    tools = load_tools(settings.tool_policy_file)
    replay_policy = dataclasses.replace(policy, autonomy="propose")  # a replay proposes; only a RecordingExecutor runs

    async def ask(q: str) -> str:
        from fleetwatch.fence import Fence
        from fleetwatch.heartbeat import snapshot

        async with ReplayClient(Path(replay), tools, now=replay_now) as reader:
            # A replay's fence is the fixtures' own devices: nothing real exists to reach, and only a
            # RecordingExecutor runs what's approved.
            fence = Fence(device_ids=frozenset((await snapshot(reader, datetime.now(UTC))).devices))
            return await _assistant_answer(
                settings,
                key,
                q,
                state,
                replay_policy,
                reader,
                None,
                lambda: ReplayClient(Path(replay), tools, now=replay_now),
                slot="replay",
                fence=fence,
                client=model_client,
            )

    return lambda q: asyncio.run(ask(q))


def _approve_page(settings: Settings, replay: str | None, no_ai: bool = False, model_client=None):
    """Build the v0.2 approval page. Replay: fake reads from DIR and a RecordingExecutor; the real executor is never
    imported or built. Otherwise it refuses to start unless policy.yaml says autonomy: propose and a sandbox
    sign-in exists."""
    from fleetwatch.approve_page import ApprovePage, RecordingExecutor, seed_replay_sample
    from fleetwatch.epiphan.approval_read import read_for_approval

    tools = load_tools(settings.tool_policy_file)
    key = "" if no_ai else reveal(settings.anthropic_api_key)
    if replay:
        # One clock for every replay client the page builds: relative times in the sample ("{{now+25m}}") resolve
        # against it, so a proposal's fingerprint and the card's fresh read agree, however long the chat takes.
        replay_now = datetime.now(UTC)
        client = ReplayClient(Path(replay), tools, now=replay_now)

        async def read_replay():
            async with client:
                return await read_for_approval(client, datetime.now(UTC))

        state = State(":memory:", check_same_thread=False)  # a replay never touches the real history
        ask_fn = _approve_ask(
            settings, state, load_policy(settings.policy_file), key, replay, model_client, replay_now, no_ai
        )
        page = ApprovePage(
            state,
            read_replay,
            RecordingExecutor(state),
            tools,
            settings.approve_port,
            ask_fn=ask_fn,
            policy=load_policy(settings.policy_file),
        )
        seed_replay_sample(state, tools, asyncio.run(read_replay()))
        return page

    policy = load_policy(settings.policy_file)
    if not policy.proposes:
        print("The approval page didn't start: policy.yaml says autonomy: observe. Set autonomy: propose to use it.")
        raise SystemExit(2)
    from fleetwatch.epiphan.executor import WriteExecutor, sandbox_store

    store = sandbox_store(settings)
    try:
        signed_in = store.has_tokens() and not store.is_dead()
    except Exception:  # noqa: BLE001 - an unreadable slot counts as no sign-in
        signed_in = False
    if not signed_in:
        print(
            "The approval page didn't start: there's no sandbox sign-in, so no change could run. "
            "Sign in to the sandbox team first: fleetwatch login --sandbox"
        )
        raise SystemExit(2)
    from fleetwatch.fence import Fence

    if not Fence.from_settings(settings).is_set:
        print(
            "The approval page didn't start: no sandbox fence is set, so every change would be refused. "
            "List the sandbox devices in FLEETWATCH_WRITE_DEVICE_IDS (or set FLEETWATCH_WRITE_TEAM_ID)."
        )
        raise SystemExit(2)
    state = State(settings.state_db, check_same_thread=False)
    executor = WriteExecutor(settings, state, policy=policy, tools=tools)

    async def read_sandbox():
        client = EpiphanClient(
            settings.epiphan_mcp_url, tools, storage=store, callback_port=settings.oauth_callback_port
        )
        async with client:
            # Strict: a failed recorder or event read must fail the card (Deny only), not read as "Not recording".
            return await read_for_approval(client, datetime.now(UTC), strict=True)

    ask_fn = _approve_ask(settings, state, policy, key, None, no_ai=no_ai)
    return ApprovePage(state, read_sandbox, executor, tools, settings.approve_port, ask_fn=ask_fn, policy=policy)


async def _maybe_sweep(client, state: State, policy, notifier) -> None:
    from fleetwatch.sweep import due, post_pending, run_sweep

    now = datetime.now(UTC)
    last = state.last_sweep()
    if due(now.astimezone(), policy.sweep_at, last.at.astimezone() if last else None):
        await run_sweep(client, state, policy, notifier, now)
    else:
        post_pending(state, policy, notifier, now)


async def _sweep(settings: Settings, replay: str | None) -> None:
    from fleetwatch.sweep import run_sweep

    client, state, notifier = _build(settings, interactive=False, replay=replay)
    policy = load_policy(settings.policy_file)
    if replay:
        policy = dataclasses.replace(policy, quiet_start=None, quiet_end=None)
    async with client:
        if not await run_sweep(client, state, policy, notifier, datetime.now(UTC)):
            print("Sweep saved. It will be posted after quiet hours.")


def _history(settings: Settings, days: int) -> None:
    from fleetwatch.sweep import render_history

    now = datetime.now(UTC)
    print(render_history(State(settings.state_db), since=now - timedelta(days=days), now=now))


def _notes(p: argparse.ArgumentParser, args: argparse.Namespace, settings: Settings) -> None:
    from fleetwatch.notes import add_note, default_author, list_notes

    state = State(settings.state_db)  # local notes only; never signs in or calls Epiphan
    if args.command == "notes":
        print(list_notes(state, room=" ".join(args.question) or None, search=args.search))
        return
    if len(args.question) < 2:
        p.error('note needs a room and the text, e.g. fleetwatch note "Room 204 Pearl Mini" "Bulb replaced"')
    room, text = args.question[0], " ".join(args.question[1:])
    code, msg = add_note(state, room, text, args.author or default_author(), datetime.now(UTC))
    print(msg)
    raise SystemExit(code)


async def _run(settings: Settings) -> None:
    from fleetwatch.epiphan.auth import SIGN_IN_EXPIRED
    from fleetwatch.runner import EXIT_CONFIG, run_loop
    from fleetwatch.slack_command import start_listener

    if not reveal(settings.epiphan_token) and make_token_store(settings.token_store, settings.token_file).is_dead():
        # Epiphan refused the refresh token last time. Don't call it again; a restart loop only adds noise.
        logging.getLogger("fleetwatch").error(SIGN_IN_EXPIRED)
        raise SystemExit(EXIT_CONFIG)
    state, notifier = State(settings.state_db), from_settings(settings)
    policy = load_policy(settings.policy_file)
    slash = await start_listener(settings, policy, state)  # None unless FLEETWATCH_SLACK_APP_TOKEN is set
    try:
        await run_loop(
            lambda: _make_client(settings),  # a fresh session after any failed beat
            state,
            policy,
            notifier,
            first_run=not state.open_findings(),
            slash=slash,
            after_beat=lambda client: _maybe_sweep(client, state, policy, notifier),
        )
    finally:
        if slash:
            slash.close()


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("fleetwatch")
    except PackageNotFoundError:
        return "unknown"


def main() -> None:
    p = argparse.ArgumentParser(
        prog="fleetwatch",
        description="Fleetwatch for Epiphan Edge: an always-on, read-only watcher for your Pearl and EC20 fleet.",
    )
    p.add_argument(
        "command",
        choices=[
            "login",
            "digest",
            "run",
            "status",
            "doctor",
            "logout",
            "ask",
            "sweep",
            "history",
            "note",
            "notes",
            "approve",
        ],
    )
    p.add_argument("question", nargs="*", help='ask: your question, e.g. fleetwatch ask "is Main Stage ready"')
    p.add_argument("--version", action="version", version=f"fleetwatch {_package_version()}")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--check", action="store_true", help="status: exit 1 unless a heartbeat read the fleet recently (health check)"
    )
    p.add_argument(
        "--replay",
        metavar="DIR",
        help="digest, ask, sweep, approve: use saved tool results from DIR instead of Epiphan (no sign-in)",
    )
    p.add_argument(
        "--capture",
        metavar="DIR",
        help="digest: also save every tool result, redacted, to DIR as replay files (keep DIR outside the repo)",
    )
    p.add_argument("--serve", action="store_true", help="ask, approve: open a local page on 127.0.0.1")
    p.add_argument(
        "--no-ai",
        action="store_true",
        help="ask, approve: keyword answers only, even with FLEETWATCH_ANTHROPIC_API_KEY set",
    )
    p.add_argument("--days", type=int, default=7, help="history: how many days back (default 7)")
    p.add_argument("--author", help="note: who left it (default: your login name)")
    p.add_argument("--search", metavar="TEXT", help="notes: only notes containing TEXT")
    p.add_argument(
        "--sandbox",
        action="store_true",
        help="login, logout: the v0.2 sandbox sign-in, kept in its own slot (needs a sandbox team)",
    )
    args = p.parse_intermixed_args()
    if args.sandbox and args.command not in ("login", "logout"):
        p.error("--sandbox works with login and logout only")
    configure_logging(args.verbose)  # redacted, and the MCP/HTTP libraries stay at WARNING even with -v
    settings = Settings()
    if args.command == "login":
        asyncio.run(_login(settings, args.sandbox))
    elif args.command == "logout":
        print(_logout(settings, args.sandbox))
    elif args.command == "digest":
        asyncio.run(_digest(settings, args.replay, args.capture))
    elif args.command == "ask":
        if not args.question and not args.serve:
            p.error('ask needs a question, e.g. fleetwatch ask "what needs attention", or --serve for the page')
        asyncio.run(_ask(settings, " ".join(args.question), args.replay, args.serve, args.no_ai))
    elif args.command == "approve":
        if not args.serve:
            p.error("approve needs --serve, e.g. fleetwatch approve --serve (add --replay tests/fixtures for a demo)")
        from fleetwatch.approve_page import serve as serve_approve

        serve_approve(_approve_page(settings, args.replay, args.no_ai), settings.state_db.parent)
    elif args.command == "sweep":
        asyncio.run(_sweep(settings, args.replay))
    elif args.command == "history":
        _history(settings, args.days)
    elif args.command in ("note", "notes"):
        _notes(p, args, settings)
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
        store = make_token_store(settings.token_store, settings.token_file)
        signed_in = bool(reveal(settings.epiphan_token)) or store.has_tokens()
        print(
            f"Signed in: {'yes' if signed_in else 'no (run fleetwatch login)'}\nOpen items: {len(items)}  ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)"
        )
        for f in items:
            print(f"  {f.priority.value}: {f.what}")


if __name__ == "__main__":
    main()
