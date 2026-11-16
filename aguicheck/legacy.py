"""Pre-1.0 shapes the reference client still upgrades, with a warning, until
the compatibility shims expire (AG-UI DEPRECATIONS.md: every 1.0 shim expires
2027-09-17). After that date these shapes stop working.

normalize() returns the upgraded event plus one finding per retired shape, so
the rest of the checks see what the client sees after its boundary.
"""

import copy
from typing import Any, Dict, List, Optional, Tuple

from .findings import WARNING, Finding

SHIM_EXPIRY = "2027-09-17"

EVENT_NULLS = ["rawEvent", "subagentRunId"]
TYPE_NULLS = {
    "RUN_STARTED": ["parentRunId", "input"],
    "RUN_FINISHED": ["result", "outcome"],
    "RUN_ERROR": ["usage"],
    "SUBAGENT_FINISHED": ["result"],
    "TOOL_CALL_START": ["parentMessageId"],
    "TOOL_CALL_CHUNK": ["parentMessageId"],
    "TOOL_CALL_RESULT": ["role"],
}
OUTCOME_NULLS = ["pendingToolCallIds"]
USAGE_NULLS = ["provider", "reasoningTokens", "cachedInputTokens", "cacheWriteInputTokens"]
MEDIA_PARTS = ("image", "audio", "video", "document")

THINKING = {
    "THINKING_START": "REASONING_START",
    "THINKING_END": "REASONING_END",
    "THINKING_TEXT_MESSAGE_START": "REASONING_MESSAGE_START",
    "THINKING_TEXT_MESSAGE_CONTENT": "REASONING_MESSAGE_CONTENT",
    "THINKING_TEXT_MESSAGE_END": "REASONING_MESSAGE_END",
}


def _note(out: List[Finding], what: str, path: str, replacement: str = "omit the field") -> None:
    out.append(Finding(
        WARNING, "deprecated",
        "%s is a pre-1.0 shape (%s); the reference client upgrades it with a warning only until %s"
        % (what, replacement, SHIM_EXPIRY),
        path=path,
    ))


def _drop_nulls(obj: Any, fields: List[str], label: str, base: str, out: List[Finding]) -> None:
    if not isinstance(obj, dict):
        return
    for f in fields:
        if f in obj and obj[f] is None:
            del obj[f]
            _note(out, "%s: null" % (label + f), base + "/" + f)


def _messages(messages: Any, base: str, out: List[Finding]) -> None:
    if not isinstance(messages, list):
        return
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
            continue
        for j, part in enumerate(msg["content"]):
            here = "%s/%d/content/%d" % (base, i, j)
            if not isinstance(part, dict):
                continue
            if part.get("type") in MEDIA_PARTS:
                _drop_nulls(part, ["metadata"], "%s content part " % part["type"], here, out)
            elif part.get("type") == "binary":
                _note(out, '{"type": "binary"} content part', here,
                      "use an image, audio, video or document part with a source")


class Normalizer:
    """Stateful, because THINKING_* continuations carry no id of their own."""

    def __init__(self) -> None:
        self.reasoning_id: Optional[str] = None
        self.message_id: Optional[str] = None
        self.minted = 0

    def _mint(self) -> str:
        self.minted += 1
        return "aguicheck-minted-%d" % self.minted

    def normalize(self, raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Finding]]:
        out: List[Finding] = []
        ev = copy.deepcopy(raw)
        etype = ev.get("type")
        _drop_nulls(ev, EVENT_NULLS, "", "", out)
        if etype in TYPE_NULLS:
            _drop_nulls(ev, TYPE_NULLS[etype], etype + ".", "", out)
        if etype == "RUN_FINISHED":
            _drop_nulls(ev.get("outcome"), OUTCOME_NULLS, "RUN_FINISHED.outcome.", "/outcome", out)
            if isinstance(ev.get("usage"), list):
                for i, u in enumerate(ev["usage"]):
                    _drop_nulls(u, USAGE_NULLS, "RUN_FINISHED.usage[].", "/usage/%d" % i, out)
        if etype == "RUN_STARTED" and isinstance(ev.get("input"), dict):
            inp = ev["input"]
            _drop_nulls(inp, ["forwardedProps"], "RunAgentInput.", "/input", out)
            if isinstance(inp.get("tools"), list):
                for i, t in enumerate(inp["tools"]):
                    _drop_nulls(t, ["parameters"], "Tool.", "/input/tools/%d" % i, out)
            if isinstance(inp.get("resume"), list):
                for i, r in enumerate(inp["resume"]):
                    _drop_nulls(r, ["payload"], "resume entry ", "/input/resume/%d" % i, out)
            _messages(inp.get("messages"), "/input/messages", out)
        if etype == "MESSAGES_SNAPSHOT":
            _messages(ev.get("messages"), "/messages", out)
        if etype in THINKING:
            new = THINKING[etype]
            _note(out, "event type %s" % etype, "/type", "use " + new)
            ev["type"] = new
            if etype == "THINKING_START":
                ev.pop("title", None)
                self.reasoning_id = self._mint()
                ev["messageId"] = self.reasoning_id
            elif etype == "THINKING_TEXT_MESSAGE_START":
                self.message_id = self._mint()
                ev["messageId"] = self.message_id
                ev["role"] = "reasoning"
            elif etype == "THINKING_TEXT_MESSAGE_CONTENT":
                ev["messageId"] = self.message_id or self._mint()
            elif etype == "THINKING_TEXT_MESSAGE_END":
                ev["messageId"] = self.message_id or self._mint()
                self.message_id = None
            elif etype == "THINKING_END":
                ev["messageId"] = self.reasoning_id or self._mint()
                self.reasoning_id = None
        for f in out:
            f.event_type = etype
        return ev, out
