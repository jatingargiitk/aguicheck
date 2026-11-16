"""Rebuild what a client ends up holding: messages, tool calls, state.

Kept deliberately small: enough to show a producer what its stream amounts to
and to catch the problems that only show up once events are applied (a state
patch that does not apply, tool-call arguments that are not JSON).
"""

import copy
import json
from typing import Any, Dict, List, Optional


class PatchError(Exception):
    pass


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _split(path: str) -> List[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise PatchError("invalid JSON pointer %r" % path)
    return [_unescape(t) for t in path[1:].split("/")]


def _get(doc: Any, tokens: List[str]) -> Any:
    cur = doc
    for t in tokens:
        if isinstance(cur, dict):
            if t not in cur:
                raise PatchError("path not found: /%s" % "/".join(tokens))
            cur = cur[t]
        elif isinstance(cur, list):
            if not t.isdigit() or int(t) >= len(cur):
                raise PatchError("index out of range: /%s" % "/".join(tokens))
            cur = cur[int(t)]
        else:
            raise PatchError("cannot descend into a scalar at /%s" % "/".join(tokens))
    return cur


def _add(doc: Any, tokens: List[str], value: Any) -> Any:
    if not tokens:
        return value
    parent = _get(doc, tokens[:-1])
    last = tokens[-1]
    if isinstance(parent, dict):
        parent[last] = value
    elif isinstance(parent, list):
        if last == "-":
            parent.append(value)
        elif last.isdigit() and int(last) <= len(parent):
            parent.insert(int(last), value)
        else:
            raise PatchError("index out of range: %s" % last)
    else:
        raise PatchError("cannot add into a scalar")
    return doc


def _remove(doc: Any, tokens: List[str]) -> Any:
    if not tokens:
        raise PatchError("cannot remove the whole document")
    parent = _get(doc, tokens[:-1])
    last = tokens[-1]
    if isinstance(parent, dict):
        if last not in parent:
            raise PatchError("path not found: %s" % last)
        del parent[last]
    elif isinstance(parent, list):
        if not last.isdigit() or int(last) >= len(parent):
            raise PatchError("index out of range: %s" % last)
        del parent[int(last)]
    else:
        raise PatchError("cannot remove from a scalar")
    return doc


KNOWN_OPS = {"add", "remove", "replace", "move", "copy", "test"}


def apply_patch(doc: Any, ops: List[Dict[str, Any]]) -> Any:
    """Apply an RFC 6902 patch atomically; raises PatchError and leaves `doc` untouched."""
    doc = copy.deepcopy(doc)
    for op in ops:
        kind = op.get("op")
        path = _split(op.get("path", ""))
        if kind == "add":
            doc = _add(doc, path, copy.deepcopy(op.get("value")))
        elif kind == "remove":
            doc = _remove(doc, path)
        elif kind == "replace":
            _get(doc, path)
            doc = copy.deepcopy(op.get("value")) if not path else _add(_remove(doc, path), path,
                                                                        copy.deepcopy(op.get("value")))
        elif kind in ("move", "copy"):
            src = _split(op.get("from", ""))
            value = copy.deepcopy(_get(doc, src))
            if kind == "move":
                if path[: len(src)] == src and len(path) > len(src):
                    raise PatchError("cannot move a value into its own child")
                doc = _remove(doc, src)
            doc = _add(doc, path, value)
        elif kind == "test":
            if _get(doc, path) != op.get("value"):
                raise PatchError("test failed at %s" % op.get("path"))
        else:
            raise PatchError("unknown op %r" % kind)
    return doc


class Transcript:
    def __init__(self) -> None:
        self.messages: Dict[str, Dict[str, Any]] = {}
        self.order: List[str] = []
        self.tool_calls: Dict[str, Dict[str, Any]] = {}
        self.tool_order: List[str] = []
        self.state: Any = None
        self.has_state = False
        self.runs: List[Dict[str, Any]] = []

    def _msg(self, mid: str, role: str = "assistant") -> Dict[str, Any]:
        if mid not in self.messages:
            self.messages[mid] = {"id": mid, "role": role, "content": ""}
            self.order.append(mid)
        return self.messages[mid]

    def apply(self, ev: Dict[str, Any]) -> Optional[str]:
        """Apply one verified event. Returns a warning string, or None."""
        t = ev.get("type")
        if t == "RUN_STARTED":
            self.runs.append({"runId": ev.get("runId"), "threadId": ev.get("threadId"), "end": None})
        elif t == "RUN_FINISHED":
            if self.runs:
                outcome = ev.get("outcome")
                self.runs[-1]["end"] = "finished" + (" (%s)" % outcome["type"] if isinstance(outcome, dict)
                                                     and isinstance(outcome.get("type"), str) else "")
        elif t == "RUN_ERROR":
            if not self.runs:
                self.runs.append({"runId": None, "threadId": None, "end": None})
            self.runs[-1]["end"] = "error: %s" % ev.get("message")
        elif t == "TEXT_MESSAGE_START":
            self._msg(ev.get("messageId"), ev.get("role", "assistant"))
        elif t == "TEXT_MESSAGE_CONTENT":
            m = self._msg(ev.get("messageId"))
            if isinstance(ev.get("delta"), str):
                m["content"] += ev["delta"]
        elif t == "REASONING_MESSAGE_START":
            self._msg(ev.get("messageId"), "reasoning")
        elif t == "REASONING_MESSAGE_CONTENT":
            m = self._msg(ev.get("messageId"), "reasoning")
            if isinstance(ev.get("delta"), str):
                m["content"] += ev["delta"]
        elif t == "TOOL_CALL_START":
            tid = ev.get("toolCallId")
            if tid not in self.tool_calls:
                self.tool_order.append(tid)
            self.tool_calls[tid] = {"id": tid, "name": ev.get("toolCallName"), "args": "", "result": None,
                                    "closed": False}
        elif t == "TOOL_CALL_ARGS":
            tc = self.tool_calls.get(ev.get("toolCallId"))
            if tc is not None and isinstance(ev.get("delta"), str):
                tc["args"] += ev["delta"]
        elif t == "TOOL_CALL_END":
            tc = self.tool_calls.get(ev.get("toolCallId"))
            if tc is not None:
                tc["closed"] = True
                if tc["args"].strip():
                    try:
                        json.loads(tc["args"])
                    except ValueError as exc:
                        return ("tool call '%s' (%s) closed with arguments that are not valid JSON (%s): %s"
                                % (tc["id"], tc["name"], exc.msg, _clip(tc["args"])))
        elif t == "TOOL_CALL_RESULT":
            tc = self.tool_calls.get(ev.get("toolCallId"))
            if tc is not None:
                tc["result"] = ev.get("content")
        elif t == "STATE_SNAPSHOT":
            self.state = copy.deepcopy(ev.get("snapshot"))
            self.has_state = True
        elif t == "STATE_DELTA":
            ops = [o for o in ev.get("delta") or [] if isinstance(o, dict) and o.get("op") in KNOWN_OPS]
            try:
                self.state = apply_patch(self.state if self.has_state else {}, ops)
                self.has_state = True
            except PatchError as exc:
                return "Failed to apply state patch (%s); the client keeps the previous state" % exc
        elif t == "MESSAGES_SNAPSHOT":
            self.messages, self.order = {}, []
            for m in ev.get("messages") or []:
                if isinstance(m, dict) and isinstance(m.get("id"), str):
                    content = m.get("content")
                    if isinstance(content, list):
                        content = " ".join(p.get("text", "[%s]" % p.get("type")) for p in content
                                           if isinstance(p, dict))
                    self.messages[m["id"]] = {"id": m["id"], "role": m.get("role"),
                                              "content": content if isinstance(content, str) else ""}
                    self.order.append(m["id"])
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runs": self.runs,
            "messages": [self.messages[m] for m in self.order],
            "toolCalls": [self.tool_calls[t] for t in self.tool_order],
            "state": self.state if self.has_state else None,
        }


def _clip(text: str, limit: int = 60) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."
