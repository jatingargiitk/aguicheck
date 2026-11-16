"""Expand *_CHUNK shorthand events into START/CONTENT/END triads.

Mirrors the lane model of the reference client's chunk transform: one open
chunk stream per owner (the parent agent or a subagentRunId); most other
events close the owner's lane first; a chunk without an id continues the open
lane; the first chunk of a new stream must name its id (and a tool call its
name); a continuation must not contradict the opener's role, name, tool name or
parent message.
"""

from typing import Any, Dict, List, Optional


class ChunkError(Exception):
    pass


CLOSES_OWN_LANE = {
    "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",
    "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT",
    "STATE_SNAPSHOT", "STATE_DELTA", "CUSTOM", "STEP_STARTED", "STEP_FINISHED",
    "REASONING_START", "REASONING_MESSAGE_START", "REASONING_MESSAGE_CONTENT",
    "REASONING_MESSAGE_END", "REASONING_END",
}
CLOSES_ALL_LANES = {"RUN_STARTED", "RUN_FINISHED", "RUN_ERROR", "MESSAGES_SNAPSHOT"}

_KIND = {"TEXT_MESSAGE_CHUNK": "text", "TOOL_CALL_CHUNK": "tool", "REASONING_MESSAGE_CHUNK": "reasoning"}
_ENTITY = {"text": "text message", "tool": "tool call", "reasoning": "reasoning message"}
_ID = {"text": "messageId", "tool": "toolCallId", "reasoning": "messageId"}


def _tag(fields: Dict[str, Any]) -> Dict[str, Any]:
    return {"subagentRunId": fields["subagentRunId"]} if fields.get("subagentRunId") is not None else {}


class ChunkExpander:
    def __init__(self) -> None:
        self.lanes: Dict[Optional[str], Dict[str, Any]] = {}

    def _close(self, owner: Optional[str]) -> List[Dict[str, Any]]:
        pending = self.lanes.pop(owner, None)
        if pending is None:
            return []
        f = pending["fields"]
        if pending["kind"] == "text":
            return [dict(type="TEXT_MESSAGE_END", messageId=f["messageId"], **_tag(f))]
        if pending["kind"] == "tool":
            return [dict(type="TOOL_CALL_END", toolCallId=f["toolCallId"], **_tag(f))]
        return [dict(type="REASONING_MESSAGE_END", messageId=f["messageId"], **_tag(f))]

    def close_all(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for owner in list(self.lanes):
            out.extend(self._close(owner))
        return out

    def _resolve_lane(self, kind: str, entity_id: Optional[str], tag: Optional[str], chunk_type: str) -> Optional[str]:
        if entity_id is not None:
            for owner, pending in self.lanes.items():
                if pending["kind"] == kind and pending["fields"][_ID[kind]] == entity_id:
                    if tag is not None and tag != owner:
                        raise ChunkError(
                            "Cannot continue %s '%s': chunk subagentRunId '%s' does not match the open stream's "
                            "subagent '%s'." % (_ENTITY[kind], entity_id, tag, owner or "(the parent agent)"))
                    return owner
            return tag
        if tag is not None:
            return tag
        parent = self.lanes.get(None)
        if parent is not None and parent["kind"] == kind:
            return None
        candidates = [o for o, p in self.lanes.items() if p["kind"] == kind]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ChunkError("Ambiguous %s: it carries neither a %s nor a subagentRunId, but %d lanes have an open %s."
                             % (chunk_type, _ID[kind], len(candidates), _ENTITY[kind]))
        return None

    @staticmethod
    def _agree(kind: str, entity_id: str, field: str, incoming: Any, established: Any) -> None:
        if incoming is not None and incoming != established:
            raise ChunkError(
                "Cannot continue %s '%s': chunk %s '%s' does not match the open stream's %s %s"
                % (_ENTITY[kind], entity_id, field, incoming, field,
                   "(absent)" if established is None else "'%s'" % established))

    def feed(self, ev: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return the events the verifier should see in place of `ev`. Raises ChunkError."""
        etype = ev.get("type")
        if etype in CLOSES_OWN_LANE:
            return self._close(ev.get("subagentRunId")) + [ev]
        if etype in CLOSES_ALL_LANES:
            return self.close_all() + [ev]
        if etype in ("SUBAGENT_FINISHED", "SUBAGENT_ERROR"):
            owner = ev.get("subagentRunId")
            return ([] if owner is None else self._close(owner)) + [ev]
        kind = _KIND.get(etype)
        if kind is None:
            return [ev]
        idf = _ID[kind]
        tag = ev.get("subagentRunId")
        lane = self._resolve_lane(kind, ev.get(idf), tag, etype)
        open_ = self.lanes.get(lane)
        out: List[Dict[str, Any]] = []
        if open_ is not None and open_["kind"] == kind and ev.get(idf) in (None, open_["fields"][idf]):
            f = open_["fields"]
            if kind == "text":
                self._agree(kind, f[idf], "role", ev.get("role"), f.get("role"))
                self._agree(kind, f[idf], "name", ev.get("name"), f.get("name"))
            elif kind == "tool":
                self._agree(kind, f[idf], "toolCallName", ev.get("toolCallName"), f.get("toolCallName"))
                self._agree(kind, f[idf], "parentMessageId", ev.get("parentMessageId"), f.get("parentMessageId"))
        else:
            out.extend(self._close(lane))
            if ev.get(idf) is None:
                raise ChunkError("First %s must have a %s" % (etype, idf))
            if kind == "tool" and ev.get("toolCallName") is None:
                raise ChunkError("First TOOL_CALL_CHUNK must have a toolCallName")
            f = {idf: ev[idf], "subagentRunId": tag}
            if kind == "text":
                f["role"] = ev.get("role", "assistant")
                f["name"] = ev.get("name")
                start = dict(type="TEXT_MESSAGE_START", messageId=f[idf], role=f["role"], **_tag(f))
                if f["name"] is not None:
                    start["name"] = f["name"]
            elif kind == "tool":
                f["toolCallName"] = ev["toolCallName"]
                f["parentMessageId"] = ev.get("parentMessageId")
                start = dict(type="TOOL_CALL_START", toolCallId=f[idf], toolCallName=f["toolCallName"], **_tag(f))
                if f["parentMessageId"] is not None:
                    start["parentMessageId"] = f["parentMessageId"]
            else:
                start = dict(type="REASONING_MESSAGE_START", messageId=f[idf], role="reasoning", **_tag(f))
            self.lanes[lane] = {"kind": kind, "fields": f}
            out.append(start)
        f = self.lanes[lane]["fields"]
        if ev.get("delta") is not None:
            content_type = {"text": "TEXT_MESSAGE_CONTENT", "tool": "TOOL_CALL_ARGS",
                            "reasoning": "REASONING_MESSAGE_CONTENT"}[kind]
            out.append(dict(type=content_type, **{idf: f[idf]}, delta=ev["delta"], **_tag(f)))
        return out
