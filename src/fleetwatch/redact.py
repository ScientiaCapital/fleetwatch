"""Remove stream keys, passwords and credentialed URLs from tool output.

Kept in step with the Epiphan Edge Claude Kit's `.claude/hooks/epiphan-redact.sh`; shared cases in
`tests/redaction-cases.json`. Runs on every tool result before it is parsed, stored, logged or shown to a model,
so nothing downstream ever holds a secret.
"""

import json
import re
from typing import Any

MASK = "[redacted]"

_SECRET_NAME = re.compile(
    r"^(?:.*[_-])?(?:streaming_?key|stream_?key|stream_?name|key|api_?key|private_?key|password|passphrase"
    r"|passwd|pwd|secret|token|authorization|auth|credentials?)$",
    re.IGNORECASE,
)
_PAGING = re.compile(r"page|cursor", re.IGNORECASE)
_STREAM_ID = re.compile(r"^(?:.*[_-])?stream_?id$", re.IGNORECASE)
_UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)

# A mask already in the text is read as part of the value around it, so the whole value is masked again: scrubbing
# twice equals scrubbing once (room notes are redacted when saved and again inside an `ask` answer), and typing
# "[redacted]" in front of a secret can't split the match and let the rest through.
_M = re.escape(MASK)
_MASKS = rf"(?:{_M})+(?![\w/.~%+=&:@?#\[-])"  # masks with nothing secret after them: already done
_USERINFO = rf"(?:(?:{_M}|[^/?#\s\"<>()\[\]])*@)?"  # user:pw@ is dropped, even with a typed mask inside it
_STREAM_URL = re.compile(
    rf"(?P<p>\b(?:rtmp[a-z]*|srt|rtsp|rist)://){_USERINFO}(?P<h>[^/?#\s\"'<>()\[\],@]+)"
    rf"(?P<r>[/?#](?:{_MASKS}|(?:{_M}|[^\s\"'<>()\[\],])*))?",
    re.IGNORECASE,
)
_HTTP_URL = re.compile(
    rf"(?P<p>\bhttps?://){_USERINFO}(?P<h>[^/?#\s\"'<>()\[\],@]+)"
    rf"(?P<path>/(?:{_MASKS}|(?:{_M}|[^?#\s\"'<>()\[\],])*))?(?P<q>\?(?:{_MASKS}|(?:{_M}|[^\s\"'<>()\[\],])*))?",
    re.IGNORECASE,
)
# File-transfer and websocket URLs keep their host and path; only the credentials go: ftp://[redacted]@host/x.
_USERINFO_URL = re.compile(rf"(?P<p>\b(?:s?ftps?|wss?)://)(?:{_M}|[^/?#\s\"<>()\[\]])*@", re.IGNORECASE)
_INGEST_PATH = re.compile(r"whip|whep|ingest|publish|upload|live|stream|rtmp|srt|push|broadcast", re.IGNORECASE)
_SEP = r"(?:[ _-]|%2[0d]|%5f)?"  # stream key, stream_key, stream-key, streamkey, stream%20key
_KEY_NAMES = "|".join(
    re.sub(r"[ _-]", lambda _: _SEP, name)
    for name in (
        "streaming key", "stream key", "stream name", "x-api-key", "api key", "private key", "client secret",
        "access token", "password", "passphrase", "passwd", "pwd", "secret", "token", "authorization", "key",
    )
)  # fmt: skip
# The name stands alone (\b on both sides), so "monkey: George" and "keyboard: US" are left alone. A quoted value is
# masked up to its closing quote, spaces and all; an unquoted one up to the first space or delimiter.
_KEY_VALUE = re.compile(
    rf"(?P<k>[\"']?\b(?:{_KEY_NAMES})\b[\"']?\s*[:=]\s*[\"']?(?:bearer\s+|basic\s+)?)"
    rf"(?P<v>(?<=\")[^\"\n]+|(?<=')[^'\n]+|{_MASKS}|(?:{_M}|[^\s\"',;}}\]|])+)",
    re.IGNORECASE,
)
# A bare "Bearer <token>" or "Basic <base64>" anywhere. The token has to look like one (a digit or + / =, or 20+
# characters), so prose such as "a Bearer token" or "Basic settings" is left alone.
_TOKEN = r"(?:(?=[\w.~+/-]*[\d+/=])[\w.~+/-]{6,}|[\w.~+/-]{20,})"
_AUTH_SCHEME = re.compile(
    rf"\b(?P<s>bearer|basic)\s+(?P<v>{_MASKS}|(?:{_M})*{_TOKEN}(?:{_M}|[\w.~+/=-])*)",
    re.IGNORECASE,
)
# A markdown pipe table whose header names a secret column (Stream key, Password, API key...) has that column masked.
_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+$")
_LOOKS_SECRET = re.compile(r"://|key|pass|pwd|secret|token|auth|cred|bearer|basic|stream|sk-ant-", re.IGNORECASE)
# An Anthropic API key (sk-ant-api03-..., sk-ant-admin01-...), masked even bare, with no "key:" in front of it.
_ANTHROPIC_KEY = re.compile(rf"\bsk-ant-(?:{_M}|[\w-])+", re.IGNORECASE)
MAX_TEXT = 200_000


def _secret_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_SECRET_NAME.match(name)) and not _PAGING.search(name)


def _stream_id_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_STREAM_ID.match(name))


def _mask(value: Any) -> Any:
    return value if value is None or isinstance(value, bool) or value == "" else MASK


def _http(m: re.Match[str]) -> str:
    path, query = m["path"], m["q"]
    if path is not None and _INGEST_PATH.search(m["h"] + path):
        path = "/" + MASK
    return f"{m['p']}{m['h']}{path or ''}{'?' + MASK if query else ''}"


def _cells(row: str) -> list[str]:
    return re.sub(r"\|\s*$", "", re.sub(r"^\s*\|", "", row)).split("|")


def _scrub_tables(text: str) -> str:
    sep = "\n" if "\n" in text else "\\n"  # a table inside JSON-escaped text has a literal \n between rows
    lines = text.split(sep)
    if sum(1 for line in lines if _TABLE_ROW.match(line)) < 2:
        return text
    out: list[str] = []
    cols: dict[int, bool] | None = None  # secret column -> is it a stream-ID column; None outside a table
    for line in lines:
        if not _TABLE_ROW.match(line):
            cols = None
        elif _TABLE_RULE.match(line):
            pass
        elif cols is None:  # the header row
            names = [re.sub(r"[ -]+", "_", c.strip().lower()) for c in _cells(line)]
            cols = {i: _stream_id_name(n) for i, n in enumerate(names) if _secret_name(n) or _stream_id_name(n)}
        elif cols:
            cells = _cells(line)
            for i, is_stream_id in cols.items():
                value = cells[i].strip() if i < len(cells) else ""
                if value and not (is_stream_id and _UUID.match(value)):  # Epiphan's StreamID is a UUID: kept
                    cells[i] = f" {MASK} "
            line = "|" + "|".join(cells) + "|"
        out.append(line)
    return sep.join(out)


def scrub_text(text: str) -> str:
    if not _LOOKS_SECRET.search(text):
        return text
    if len(text) > MAX_TEXT:
        return "[withheld: a long text value was too long to check for stream keys]"
    if "|" in text:
        text = _scrub_tables(text)
    text = _STREAM_URL.sub(lambda m: f"{m['p']}{m['h']}{'/' + MASK if m['r'] else ''}", text)
    text = _HTTP_URL.sub(_http, text)
    text = _USERINFO_URL.sub(lambda m: f"{m['p']}{MASK}@", text)
    text = _KEY_VALUE.sub(lambda m: f"{m['k']}{MASK}", text)
    text = _AUTH_SCHEME.sub(lambda m: f"{m['s']} {MASK}", text)
    return _ANTHROPIC_KEY.sub(MASK, text)


def encodable(text: str) -> str:
    """`text` with any lone surrogate (invalid in UTF-8; JSON from an API can carry "\\ud800") written out as a
    visible \\udXXX escape, so nothing downstream raises UnicodeEncodeError on untrusted text."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        pair = "value" in value and any(
            _secret_name(value.get(k)) or _stream_id_name(value.get(k)) for k in ("id", "name", "key")
        )
        out = {}
        for key, item in value.items():
            if (
                key == "value"
                and pair
                or _secret_name(key)
                and not ("value" in value and isinstance(item, str) and _secret_name(item))
            ):
                out[key] = _mask(item)
            elif _stream_id_name(key) and not (isinstance(item, str) and _UUID.match(item)):
                out[key] = _mask(item)  # Epiphan's StreamID is a UUID and is kept
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if value.lstrip()[:1] in ("{", "["):
            try:
                parsed = json.loads(value)
            except ValueError:
                return scrub_text(value)
            if isinstance(parsed, (dict, list)):
                cleaned = redact(parsed)
                return value if cleaned == parsed else json.dumps(cleaned)
        return scrub_text(value)
    return value
