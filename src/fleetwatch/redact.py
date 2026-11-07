"""Remove stream keys, passwords and credentialed URLs from tool output.

Kept in step with the Epiphan Edge Claude Kit's `.claude/hooks/epiphan-redact.sh`; shared cases in
`tests/redaction-cases.json`. Runs on every tool result before it is parsed, stored, logged or shown to a model,
so nothing downstream ever holds a secret.
"""

import json
import re
from typing import Any

MASK = "[redacted]"

# A secret name may sit behind a dotted, spaced or slashed prefix (srt.passphrase, "Stream key", publisher.rtmp.key).
_SECRET_NAME = re.compile(
    r"^(?:.*[_\-. /:])?(?:streaming[ _-]?key|stream[ _-]?key|stream[ _-]?name|key|api[ _-]?key|private[ _-]?key"
    r"|password|passphrase|passwd|pwd|pw|pin|psk|pass|secret|token|authorization|auth|credentials?)$",
    re.IGNORECASE,
)
_PAGING = re.compile(r"page|cursor", re.IGNORECASE)
_STREAM_ID = re.compile(r"^(?:.*[_\-. /:])?stream[ _-]?id$", re.IGNORECASE)
# A camelCase or PascalCase name has no separator before its secret word (SrtPassphrase, accessToken, RTMPKey), so a
# name is also tested split into words: a break between a lower-case letter or digit and a capital, and before the
# last capital of an acronym. Both forms are tested, so a name the split breaks apart (PassPhrase) still matches.
_WORD_BREAK = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_COMPOUND = re.compile(r"[_.\-]|[a-z0-9][A-Z]")  # srt_passphrase, srt.passphrase, srtPassphrase
_PAIR_KEYS = ("id", "name", "key", "label", "field")  # {"name": "Stream key", "value": X} pairs
_UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)

# A mask already in the text is read as part of the value around it, so the whole value is masked again: scrubbing
# twice equals scrubbing once (room notes are redacted when saved and again inside an `ask` answer), and typing
# "[redacted]" in front of a secret can't split the match and let the rest through.
_M = re.escape(MASK)
_MASKS = rf"(?:{_M})+(?![\w/.~%+=&:@?#\[-])"  # masks with nothing secret after them: already done
_USERINFO = rf"(?:(?:{_M}|[^/?#\s\"<>()\[\]])*@)?"  # user:pw@ is dropped, even with a typed mask inside it
# rtmp:\/\/ is the same URL with JSON's escaped slashes, in text that was never parsed: the host keeps its trailing
# backslash, and the path after it is masked as usual.
_STREAM_URL = re.compile(
    rf"(?P<p>\b(?:rtmp[a-z]*|srt|rtsp|rist):(?:\\?/){{2}}){_USERINFO}(?P<h>[^/?#\s\"'<>()\[\],@]+)"
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
_INGEST_PATH = re.compile(
    r"whip|whep|ingest|publish|upload|live|stream|rtmp|srt|push|broadcast|webhook|hooks\.slack|discord(?:app)?\.com/api/webhooks",
    re.IGNORECASE,
)
_SEP = r"(?:[ _-]|%2[0d]|%5f)?"  # stream key, stream_key, stream-key, streamkey, stream%20key
_KEY_NAMES = "|".join(
    re.sub(r"[ _-]", lambda _: _SEP, name)
    for name in (
        "streaming key", "stream key", "stream name", "x-api-key", "api key", "private key", "client secret",
        "access token", "password", "passphrase", "passwd", "pwd", "secret", "token", "authorization",
        "pass", "pin", "psk", "pw", "key",
    )
)  # fmt: skip
_ASSIGN = "[:=\uff1a\uff1d]"  # also the fullwidth colon and equals sign that CJK input methods type
# A value after a name: a quoted one up to its closing quote, spaces and all (an escaped quote, password=\"x\" inside a
# JSON string, closes it too); an unquoted one up to the first space or delimiter.
_OPEN_QUOTE = r"\\?[\"']?"
_VALUE = rf"(?P<v>(?<=\")(?:[^\"\\\n]|\\(?!\"))+|(?<=')[^'\n]+|{_MASKS}|(?:{_M}|[^\s\"',;}}\]|])+)"
# The name stands alone (\b on both sides), so "monkey: George" and "keyboard: US" are left alone.
_KEY_VALUE = re.compile(
    rf"(?P<k>[\"']?\b(?:{_KEY_NAMES})\b[\"']?\s*{_ASSIGN}\s*{_OPEN_QUOTE}(?:bearer\s+|basic\s+)?)" + _VALUE,
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
_LOOKS_SECRET = re.compile(
    r":(?:\\?/){2}|key|pass|pwd|pin|psk|\bpw\b|secret|token|auth|cred|bearer|basic|stream|webhook|sk-ant-",
    re.IGNORECASE,
)
# The same two JSON shapes as text, for output that was never parsed: an id/name/label whose value names a secret
# followed by its "value", and a publisher's "stream" field (Pearl's name for the RTMP stream key) when a url,
# username or password field is in the same text. A bare "stream": true flag has no quoted value and is left alone.
_PAIR_TEXT = re.compile(
    rf"(?P<k>[\"'](?:{'|'.join(_PAIR_KEYS)})[\"']\s*:\s*[\"'][^\"'\n]*"
    rf"(?:key|password|passphrase|passwd|pwd|pin|psk|pass|secret|token)[\"']\s*,\s*[\"']value[\"']\s*:\s*[\"'])"
    rf"(?P<v>(?:{_M}|[^\"'\n])+)",
    re.IGNORECASE,
)
# A quoted key in text that was never parsed, such as JSON cut off mid-value: its value is masked when the key passes
# the same test as a parsed key, so compound names that _KEY_VALUE's \b misses (SrtPassphrase, srt_passphrase) are
# caught too. The value is read as in _KEY_VALUE.
_QUOTED_KEY = re.compile(r"(?P<k>(?P<q>[\"'])(?P<name>[A-Za-z][\w.\- /:]{0,63})(?P=q)\s*:\s*[\"']?)" + _VALUE)
# The same test for an unquoted compound name in free text: camelCase (authToken: x, secretKey=x) or an env-style line
# (RTMP_STREAM_KEY=x). The name starts a word, so it isn't the tail of a longer one. A one-word name is left to
# _KEY_VALUE, so prose such as "Auth: required" is kept.
_BARE_KEY = re.compile(rf"(?P<k>(?<![\w.\-])(?P<name>[A-Za-z][\w.\-]{{0,63}})\s*{_ASSIGN}\s*{_OPEN_QUOTE})" + _VALUE)
_STREAM_FIELD_TEXT = re.compile(rf"(?P<k>[\"']stream[\"']\s*:\s*[\"'])(?P<v>(?:{_M}|[^\"'\n])+)", re.IGNORECASE)
_PUBLISHER_SIBLING = re.compile(r"[\"'](?:url|username|password)[\"']\s*:", re.IGNORECASE)
# An Anthropic API key (sk-ant-api03-..., sk-ant-admin01-...), masked even bare, with no "key:" in front of it.
_ANTHROPIC_KEY = re.compile(rf"\bsk-ant-(?:{_M}|[\w-])+", re.IGNORECASE)
MAX_TEXT = 200_000


def _secret_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(_SECRET_NAME.match(name) or _SECRET_NAME.match(_WORD_BREAK.sub("_", name)))
        and not _PAGING.search(name)
    )


def _stream_id_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_STREAM_ID.match(name) or _STREAM_ID.match(_WORD_BREAK.sub("_", name)))


def _quoted_key(m: re.Match[str]) -> str:
    keep = not _secret_name(m["name"]) or m["v"] in ("true", "false", "null")
    return m[0] if keep else f"{m['k']}{MASK}"


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
    if sum(1 for line in lines if "|" in line) < 2:
        return text
    out: list[str] = []
    cols: dict[int, bool] | None = None  # secret column -> is it a stream-ID column; None outside a table
    for i, line in enumerate(lines):
        # A row starts with a pipe. Without the outer pipes (Name | Stream key), a line with one is a row when a rule
        # line (--- | ---) sits under it or it continues a table.
        rule_next = (
            i + 1 < len(lines) and "|" in lines[i + 1] and "-" in lines[i + 1] and _TABLE_RULE.match(lines[i + 1])
        )
        if not (_TABLE_ROW.match(line) or "|" in line and (cols is not None or rule_next)):
            cols = None
        elif _TABLE_RULE.match(line):
            pass
        elif cols is None:  # the header row
            names = [re.sub(r"[ -]+", "_", c.strip()) for c in _cells(line)]  # case kept for the word split
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
    text = _QUOTED_KEY.sub(_quoted_key, text)
    text = _BARE_KEY.sub(lambda m: _quoted_key(m) if _COMPOUND.search(m["name"]) else m[0], text)
    text = _PAIR_TEXT.sub(lambda m: f"{m['k']}{MASK}", text)
    if _PUBLISHER_SIBLING.search(text):
        text = _STREAM_FIELD_TEXT.sub(lambda m: f"{m['k']}{MASK}", text)
    text = _AUTH_SCHEME.sub(lambda m: f"{m['s']} {MASK}", text)
    return _ANTHROPIC_KEY.sub(MASK, text)


def encodable(text: str) -> str:
    """`text` with any lone surrogate (invalid in UTF-8; JSON from an API can carry "\\ud800") written out as a
    visible \\udXXX escape, so nothing downstream raises UnicodeEncodeError on untrusted text."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        pair = "value" in value and any(_secret_name(value.get(k)) or _stream_id_name(value.get(k)) for k in _PAIR_KEYS)
        # A publisher's "stream" field is the RTMP stream key when it sits next to url/username/password.
        lower_keys = {k.lower() for k in value if isinstance(k, str)}
        publisher = "stream" in lower_keys and bool(lower_keys & {"url", "username", "password"})
        out = {}
        for key, item in value.items():
            if (
                key == "value"
                and pair
                or _secret_name(key)
                and not ("value" in value and isinstance(item, str) and _secret_name(item))
                or publisher
                and isinstance(key, str)
                and key.lower() == "stream"
                and isinstance(item, str)
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
