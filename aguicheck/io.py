"""Read an AG-UI event stream: an SSE capture, JSON Lines, a JSON array, a
conformance-fixture file, or a live endpoint."""

import json
import uuid
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib import request


class InputError(Exception):
    pass


def parse_sse(lines: Iterable[str]) -> Iterator[Tuple[int, str]]:
    """Yield (line_number, data) for each SSE event that carries data.

    Standard framing: data lines accumulate until a blank line; comment lines
    (":"), event:, id: and retry: are ignored.
    """
    data: List[str] = []
    start = 0
    for n, line in enumerate(lines, 1):
        line = line.rstrip("\r\n")
        if line == "":
            if data:
                yield start, "\n".join(data)
            data = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            if not data:
                start = n
            data.append(value)
    if data:
        yield start, "\n".join(data)


def _decode(items: Iterable[Tuple[int, str]]) -> List[Tuple[int, Any]]:
    out = []
    for line, text in items:
        try:
            out.append((line, json.loads(text)))
        except ValueError as exc:
            raise InputError("line %d: not JSON (%s): %s" % (line, exc, text[:80]))
    return out


def load_events(text: str) -> List[Tuple[int, Any]]:
    """Return [(source_line, event)] from SSE, JSON Lines, a JSON array, or a fixture object."""
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, list):
            return [(0, e) for e in doc]
        if isinstance(doc, dict) and isinstance(doc.get("stream"), list):
            return [(0, e) for e in doc["stream"]]
        if isinstance(doc, dict):
            return [(1, doc)]
        # JSON Lines
        return _decode((n, ln) for n, ln in enumerate(text.splitlines(), 1) if ln.strip())
    return _decode(parse_sse(text.splitlines()))


def run_input(message: str, thread_id: Optional[str] = None, run_id: Optional[str] = None,
              state: Any = None) -> Dict[str, Any]:
    """A minimal 1.0 RunAgentInput carrying one user message."""
    return {
        "threadId": thread_id or "aguicheck-" + uuid.uuid4().hex[:8],
        "runId": run_id or "aguicheck-run-" + uuid.uuid4().hex[:8],
        "state": {} if state is None else state,
        "messages": [{"id": "aguicheck-msg-1", "role": "user", "content": message}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def fetch(url: str, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None,
          timeout: float = 120.0) -> str:
    """POST a RunAgentInput and return the raw SSE response body."""
    req = request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8")
    except OSError as exc:
        raise InputError("request to %s failed: %s" % (url, exc))
    if "text/event-stream" not in ctype:
        raise InputError("%s answered with Content-Type %r, not text/event-stream" % (url, ctype))
    return raw
