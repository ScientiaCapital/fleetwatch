"""Remove stream keys, passwords and credentialed URLs from tool output.

Python port of the Epiphan Edge Claude Kit's `.claude/hooks/epiphan-redact.sh`. Runs on every tool result
before it is parsed, stored, logged or shown to a model, so nothing downstream ever holds a secret.
"""

import json
import re
from typing import Any

MASK = "[redacted]"

_SECRET_NAME = re.compile(
    r"^(?:.*[_-])?(?:streaming_?key|stream_?key|stream_?name|key|password|passphrase|passwd|pwd|secret|token"
    r"|authorization|auth|credentials?)$",
    re.IGNORECASE,
)
_PAGING = re.compile(r"page|cursor", re.IGNORECASE)
_STREAM_ID = re.compile(r"^(?:.*[_-])?stream_?id$", re.IGNORECASE)
_UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)

# Text that was already redacted stays as it is, so scrubbing twice equals scrubbing once (room notes are redacted
# when saved and again inside an `ask` answer). A mask followed by more URL or token characters is scrubbed again.
_DONE = rf"(?!{re.escape(MASK)}(?![\w/.~%+=&:@?#-]))"
_STREAM_URL = re.compile(
    r"(?P<p>\b(?:rtmp[a-z]*|srt|rtsp|rist)://)(?:[^/?#\s\"<>()\[\]]*@)?(?P<h>[^/?#\s\"'<>()\[\],@]+)"
    rf"(?P<r>[/?#]{_DONE}[^\s\"'<>()\[\],]*)?",
    re.IGNORECASE,
)
_HTTP_URL = re.compile(
    r"(?P<p>\bhttps?://)(?:[^/?#\s\"<>()\[\]]*@)?(?P<h>[^/?#\s\"'<>()\[\],@]+)"
    rf"(?P<path>/{_DONE}[^?#\s\"'<>()\[\],]*)?(?P<q>\?{_DONE}[^\s\"'<>()\[\],]*)?",
    re.IGNORECASE,
)
_INGEST_PATH = re.compile(r"whip|whep|ingest|publish|upload|live|stream|rtmp|srt|push|broadcast", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"(?P<k>[\"']?\b(?:streaming[ _-]?key|stream[ _-]?key|stream[ _-]?name|password|passphrase|passwd|secret"
    r"|client[_-]?secret|access[_-]?token|token|authorization)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+|basic\s+)?)"
    rf"{_DONE}(?P<v>[^\s\"',;}}\]|]+)",
    re.IGNORECASE,
)
_LOOKS_SECRET = re.compile(r"://|key|pass|pwd|secret|token|auth|cred|bearer|stream", re.IGNORECASE)
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


def scrub_text(text: str) -> str:
    if not _LOOKS_SECRET.search(text):
        return text
    if len(text) > MAX_TEXT:
        return "[withheld: a long text value was too long to check for stream keys]"
    text = _STREAM_URL.sub(lambda m: f"{m['p']}{m['h']}{'/' + MASK if m['r'] else ''}", text)
    text = _HTTP_URL.sub(_http, text)
    return _KEY_VALUE.sub(lambda m: f"{m['k']}{MASK}", text)


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
