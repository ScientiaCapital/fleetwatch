"""`fleetwatch connect`: the pieces of the guided first run that don't need a sign-in.

The region menu and the `.env` edit live here so they can be tested without a browser. The sign-in itself is
`cli._login`; this module never touches a token.
"""

import os
import re
from collections.abc import Callable
from pathlib import Path

REGIONS = {
    "na": ("North America", "https://go.epiphan.cloud/mcp"),
    "eu": ("Europe", "https://eu.epiphan.cloud/mcp"),
    "au": ("Australia", "https://au.epiphan.cloud/mcp"),
}
_URL_KEY = "FLEETWATCH_EPIPHAN_MCP_URL"
_URL_LINE = re.compile(rf"^{_URL_KEY}\s*=.*$", re.MULTILINE)


def region_of(url: str) -> str | None:
    """The region key (na, eu, au) for one of Epiphan's three servers, or None for any other address."""
    url = url.rstrip("/")
    return next((key for key, (_, known) in REGIONS.items() if known == url), None)


def choose_region(current: str, ask: Callable[[str], str], say: Callable[..., None]) -> str:
    """Ask which Epiphan region the account is on and return that server's URL.

    Enter keeps the current address, including a custom one. With no keyboard (a script, a closed stdin) it also
    keeps the current address."""
    say("Where is your Epiphan Edge account? Check the web address you sign in at.")
    for i, (_, (name, url)) in enumerate(REGIONS.items(), 1):
        say(f"  {i}) {name:<14}({url.split('/')[2]})")
    keep = region_of(current)
    default = list(REGIONS).index(keep) + 1 if keep else None
    prompt = f"Type 1, 2 or 3 and press Enter [{default}]: " if default else "Type 1, 2 or 3, or Enter to keep it: "
    while True:
        try:
            typed = ask(prompt).strip()
        except EOFError:
            return current
        if not typed:
            return current
        if typed in ("1", "2", "3"):
            return list(REGIONS.values())[int(typed) - 1][1]
        say("Please type 1, 2 or 3.")


def set_env_url(path: Path, url: str) -> None:
    """Set FLEETWATCH_EPIPHAN_MCP_URL in the .env file at `path`, changing nothing else.

    A commented-out example is left alone. A new file is created private (mode 600), an existing one keeps its mode."""
    text = path.read_text() if path.exists() else ""
    line = f"{_URL_KEY}={url}"
    if _URL_LINE.search(text):
        text = _URL_LINE.sub(lambda _: line, text)
    else:
        text += ("" if not text or text.endswith("\n") else "\n") + line + "\n"
    if path.exists():
        path.write_text(text)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
