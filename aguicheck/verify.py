"""Sequence rules: a Python port of the reference client's verifyEvents.

Each rule raises VerifyError with the same wording the TypeScript client
(@ag-ui/client, src/verify/verify.ts) uses, so a message found here can be
searched for in the client's logs and source.
"""

from typing import Any, Dict, List, Optional, Set

PARENT = "(the parent agent)"


class VerifyError(Exception):
    pass


class Verifier:
    def __init__(self) -> None:
        self.first = True
        self._reset()
        self.run_started = False

    def _reset(self) -> None:
        self.messages: Set[str] = set()
        self.tool_calls: Set[str] = set()
        self.reasoning_spans: Set[str] = set()
        self.reasoning_messages: Set[str] = set()
        self.owners: Dict[str, Dict[str, Optional[str]]] = {
            "message": {}, "toolCall": {}, "activity": {}, "reasoning": {},
        }
        self.steps: Dict[Optional[str], Set[str]] = {}
        self.subagents: Set[str] = set()
        self.closed_subagents: Set[str] = set()
        self.run_finished = False
        self.run_error = False
        self.run_started = True

    @property
    def run_active(self) -> bool:
        return self.run_started and not self.run_finished and not self.run_error

    def open_entities(self) -> List[str]:
        out = []
        out += ["text message '%s'" % m for m in sorted(self.messages)]
        out += ["tool call '%s'" % t for t in sorted(self.tool_calls)]
        out += ["reasoning message '%s'" % m for m in sorted(self.reasoning_messages)]
        out += ["reasoning span '%s'" % m for m in sorted(self.reasoning_spans)]
        for owner, names in self.steps.items():
            out += ["step '%s'%s" % (n, "" if owner is None else " (subagent '%s')" % owner) for n in sorted(names)]
        out += ["subagent '%s'" % s for s in sorted(self.subagents)]
        return out

    # helpers -----------------------------------------------------------
    @staticmethod
    def _tag_error(etype: str, tag: Optional[str], owners: Dict[str, Optional[str]], key: str,
                   kind: str, entity: str) -> None:
        if tag is None or key not in owners:
            return
        owner = owners[key]
        if owner != tag:
            raise VerifyError(
                "Cannot send '%s': subagentRunId '%s' does not match the %s '%s' opener's subagent '%s'."
                % (etype, tag, kind, entity, owner if owner is not None else PARENT))

    def _seed(self, messages: Any, authoritative: bool) -> None:
        if not isinstance(messages, list):
            return
        for msg in messages:
            if not isinstance(msg, dict) or not isinstance(msg.get("id"), str):
                continue
            if "subagentRunId" in msg and msg["subagentRunId"] is None:
                raise VerifyError("Cannot send a message (id '%s') with 'subagentRunId: null'. The field is "
                                  "optional — omit it entirely." % msg["id"])
            role = msg.get("role")
            bucket = self.owners["reasoning" if role == "reasoning" else "activity" if role == "activity" else "message"]
            if authoritative or msg["id"] not in bucket:
                bucket[msg["id"]] = msg.get("subagentRunId")
            for tc in msg.get("toolCalls") or []:
                if isinstance(tc, dict) and isinstance(tc.get("id"), str):
                    if authoritative or tc["id"] not in self.owners["toolCall"]:
                        self.owners["toolCall"][tc["id"]] = msg.get("subagentRunId")

    # main --------------------------------------------------------------
    def feed(self, ev: Dict[str, Any]) -> None:
        t = ev.get("type")
        if self.run_error and t != "RUN_STARTED":
            raise VerifyError("Cannot send event type '%s': The run has already errored with 'RUN_ERROR'. No "
                              "further events can be sent." % t)
        if self.run_finished and t not in ("RUN_ERROR", "RUN_STARTED"):
            raise VerifyError("Cannot send event type '%s': The run has already finished with 'RUN_FINISHED'. "
                              "Start a new run with 'RUN_STARTED'." % t)
        if self.first:
            self.first = False
            if t not in ("RUN_STARTED", "RUN_ERROR"):
                raise VerifyError("First event must be 'RUN_STARTED'")
        elif t == "RUN_STARTED":
            if self.run_started and not self.run_finished and not self.run_error:
                raise VerifyError("Cannot send 'RUN_STARTED' while a run is still active. The previous run must "
                                  "be finished with 'RUN_FINISHED' before starting a new run.")
            if self.run_finished or self.run_error:
                self._reset()
        tag = ev.get("subagentRunId")

        if t in ("SUBAGENT_STARTED", "SUBAGENT_FINISHED", "SUBAGENT_ERROR"):
            optional = {"SUBAGENT_STARTED": ["description", "parentSubagentRunId", "parentToolCallId",
                                             "parentMessageId"],
                        "SUBAGENT_FINISHED": ["outcome"], "SUBAGENT_ERROR": ["code"]}[t]
            for f in optional:
                if f in ev and ev[f] is None:
                    raise VerifyError("Cannot send '%s' with '%s: null'. The field is optional — omit it entirely."
                                      % (t, f))
            outcome = ev.get("outcome")
            if isinstance(outcome, dict):
                if outcome.get("type") not in ("success", "suspended"):
                    raise VerifyError("Cannot send '%s' with outcome type '%s'. The outcome is either "
                                      '{ type: "success" } or { type: "suspended" }.' % (t, outcome.get("type")))
                ids = outcome.get("interruptIds", [])
                if ids is None:
                    raise VerifyError("Cannot send '%s' with 'outcome.interruptIds: null'. The field is optional "
                                      "— omit it entirely." % t)
                if isinstance(ids, list) and any(not isinstance(i, str) for i in ids):
                    raise VerifyError("Cannot send '%s' with a non-string entry in 'outcome.interruptIds'. "
                                      "Interrupt ids are strings." % t)
        if t == "RUN_FINISHED":
            outcome = ev.get("outcome")
            if isinstance(outcome, dict) and outcome.get("type") == "interrupt":
                for it in outcome.get("interrupts") or []:
                    if isinstance(it, dict) and "subagentRunId" in it and it["subagentRunId"] is None:
                        raise VerifyError("Cannot send 'RUN_FINISHED' with an interrupt (id '%s') carrying "
                                          "'subagentRunId: null'. The field is optional — omit it entirely."
                                          % it.get("id"))

        handler = getattr(self, "_" + str(t).lower(), None)
        if handler is not None:
            handler(ev, t, tag)

    # text messages -----------------------------------------------------
    def _text_message_start(self, ev, t, tag):
        mid = ev.get("messageId")
        if mid in self.messages:
            raise VerifyError("Cannot send 'TEXT_MESSAGE_START' event: A text message with ID '%s' is already in "
                              "progress. Complete it with 'TEXT_MESSAGE_END' first." % mid)
        self._tag_error(t, tag, self.owners["message"], mid, "message", mid)
        self.messages.add(mid)
        self.owners["message"].setdefault(mid, tag)

    def _text_message_content(self, ev, t, tag):
        mid = ev.get("messageId")
        if mid not in self.messages:
            raise VerifyError("Cannot send 'TEXT_MESSAGE_CONTENT' event: No active text message found with ID "
                              "'%s'. Start a text message with 'TEXT_MESSAGE_START' first." % mid)
        self._tag_error(t, tag, self.owners["message"], mid, "message", mid)

    def _text_message_end(self, ev, t, tag):
        mid = ev.get("messageId")
        if mid not in self.messages:
            raise VerifyError("Cannot send 'TEXT_MESSAGE_END' event: No active text message found with ID '%s'. "
                              "A 'TEXT_MESSAGE_START' event must be sent first." % mid)
        self._tag_error(t, tag, self.owners["message"], mid, "message", mid)
        self.messages.discard(mid)

    # tool calls --------------------------------------------------------
    def _tool_call_start(self, ev, t, tag):
        tid = ev.get("toolCallId")
        if tid in self.tool_calls:
            raise VerifyError("Cannot send 'TOOL_CALL_START' event: A tool call with ID '%s' is already in "
                              "progress. Complete it with 'TOOL_CALL_END' first." % tid)
        parent = ev.get("parentMessageId")
        inherited = None
        has_inherited = False
        if parent is not None and parent in self.owners["message"]:
            powner = self.owners["message"][parent]
            if tag is not None and tag != powner:
                raise VerifyError("Cannot send 'TOOL_CALL_START': subagentRunId '%s' does not match its parent "
                                  "message '%s' owner '%s'. A tool call belongs to the message that carries it."
                                  % (tag, parent, powner if powner is not None else PARENT))
            inherited, has_inherited = powner, True
        if tid in self.owners["toolCall"]:
            existing = self.owners["toolCall"][tid]
            self._tag_error(t, tag, self.owners["toolCall"], tid, "tool call", tid)
            if tag is None and has_inherited and inherited != existing:
                raise VerifyError("Cannot send 'TOOL_CALL_START': tool call '%s' is owned by '%s' but its parent "
                                  "message '%s' is owned by '%s'. A tool call belongs to the message that "
                                  "carries it." % (tid, existing if existing is not None else PARENT, parent,
                                                   inherited if inherited is not None else PARENT))
        else:
            self.owners["toolCall"][tid] = tag if tag is not None else inherited
        self.tool_calls.add(tid)

    def _tool_call_args(self, ev, t, tag):
        tid = ev.get("toolCallId")
        if tid not in self.tool_calls:
            raise VerifyError("Cannot send 'TOOL_CALL_ARGS' event: No active tool call found with ID '%s'. Start a "
                              "tool call with 'TOOL_CALL_START' first." % tid)
        self._tag_error(t, tag, self.owners["toolCall"], tid, "tool call", tid)

    def _tool_call_end(self, ev, t, tag):
        tid = ev.get("toolCallId")
        if tid not in self.tool_calls:
            raise VerifyError("Cannot send 'TOOL_CALL_END' event: No active tool call found with ID '%s'. A "
                              "'TOOL_CALL_START' event must be sent first." % tid)
        self._tag_error(t, tag, self.owners["toolCall"], tid, "tool call", tid)
        self.tool_calls.discard(tid)

    def _tool_call_result(self, ev, t, tag):
        if isinstance(ev.get("messageId"), str):
            self.owners["message"][ev["messageId"]] = tag

    # steps -------------------------------------------------------------
    def _step_started(self, ev, t, tag):
        name = ev.get("stepName")
        steps = self.steps.setdefault(tag, set())
        if name in steps:
            raise VerifyError('Step "%s" is already active for \'STEP_STARTED\'' % name)
        steps.add(name)

    def _step_finished(self, ev, t, tag):
        name = ev.get("stepName")
        steps = self.steps.setdefault(tag, set())
        if name not in steps:
            for owner, names in self.steps.items():
                if owner != tag and name in names:
                    who = "subagent '%s'" % tag if tag is not None else "the parent agent"
                    other = "subagent '%s'" % owner if owner is not None else "the parent agent"
                    raise VerifyError("Cannot send 'STEP_FINISHED' for step \"%s\" attributed to %s: that step is "
                                      "open under %s. A step must be finished by whoever started it."
                                      % (name, who, other))
            raise VerifyError("Cannot send 'STEP_FINISHED' for step \"%s\" that was not started" % name)
        steps.discard(name)

    # activity ----------------------------------------------------------
    def _activity_snapshot(self, ev, t, tag):
        mid = ev.get("messageId")
        if mid not in self.owners["activity"] or ev.get("replace") is not False:
            self.owners["activity"][mid] = tag

    def _activity_delta(self, ev, t, tag):
        mid = ev.get("messageId")
        self._tag_error(t, tag, self.owners["activity"], mid, "activity", mid)

    # reasoning ---------------------------------------------------------
    def _reasoning_open(self, ev, t, tag, span):
        mid = ev.get("messageId")
        open_ = self.reasoning_spans if span else self.reasoning_messages
        if mid in open_:
            if span:
                raise VerifyError("Cannot send 'REASONING_START' event: A reasoning span with ID '%s' is already "
                                  "in progress. Complete it with 'REASONING_END' first." % mid)
            raise VerifyError("Cannot send 'REASONING_MESSAGE_START' event: A reasoning message with ID '%s' is "
                              "already in progress. Complete it with 'REASONING_MESSAGE_END' first." % mid)
        self._tag_error(t, tag, self.owners["reasoning"], mid, "reasoning message", mid)
        open_.add(mid)
        self.owners["reasoning"].setdefault(mid, tag)

    def _reasoning_continue(self, ev, t, tag, span, close):
        mid = ev.get("messageId")
        open_ = self.reasoning_spans if span else self.reasoning_messages
        if mid not in open_:
            if span:
                raise VerifyError("Cannot send 'REASONING_END' event: No active reasoning span found with ID '%s'. "
                                  "A 'REASONING_START' event must be sent first." % mid)
            raise VerifyError("Cannot send '%s' event: No active reasoning message found with ID '%s'. Start a "
                              "reasoning message with 'REASONING_MESSAGE_START' first." % (t, mid))
        self._tag_error(t, tag, self.owners["reasoning"], mid, "reasoning message", mid)
        if close:
            open_.discard(mid)

    def _reasoning_start(self, ev, t, tag):
        self._reasoning_open(ev, t, tag, True)

    def _reasoning_message_start(self, ev, t, tag):
        self._reasoning_open(ev, t, tag, False)

    def _reasoning_message_content(self, ev, t, tag):
        self._reasoning_continue(ev, t, tag, False, False)

    def _reasoning_message_end(self, ev, t, tag):
        self._reasoning_continue(ev, t, tag, False, True)

    def _reasoning_end(self, ev, t, tag):
        self._reasoning_continue(ev, t, tag, True, True)

    def _reasoning_encrypted_value(self, ev, t, tag):
        eid, sub = ev.get("entityId"), ev.get("subtype")
        if sub == "tool-call":
            owners, kind = self.owners["toolCall"], "tool call"
        elif sub == "message":
            owners = self.owners["message"] if eid in self.owners["message"] else self.owners["reasoning"]
            kind = "message"
        else:
            owners, kind = self.owners["reasoning"], "reasoning message"
        self._tag_error(t, tag, owners, eid, kind, eid)

    # subagents ---------------------------------------------------------
    def _subagent_started(self, ev, t, tag):
        if not isinstance(tag, str):
            raise VerifyError("Cannot send 'SUBAGENT_STARTED' without a 'subagentRunId'.")
        if not isinstance(ev.get("name"), str):
            raise VerifyError("Cannot send 'SUBAGENT_STARTED' without a 'name'.")
        if tag in self.subagents:
            raise VerifyError("Cannot send 'SUBAGENT_STARTED': subagent '%s' is already active. Finish it with "
                              "'SUBAGENT_FINISHED' first." % tag)
        if tag in self.closed_subagents:
            raise VerifyError("Cannot send 'SUBAGENT_STARTED': subagent '%s' has already finished in this run. "
                              "Subagent IDs are per-invocation and cannot be reused." % tag)
        parent = ev.get("parentSubagentRunId")
        if parent is not None and parent not in self.subagents and parent not in self.closed_subagents:
            raise VerifyError("Cannot send 'SUBAGENT_STARTED': parentSubagentRunId '%s' has not been started in "
                              "this run." % parent)
        self.subagents.add(tag)

    def _subagent_finished(self, ev, t, tag):
        if not isinstance(tag, str):
            raise VerifyError("Cannot send '%s' without a 'subagentRunId'." % t)
        if t == "SUBAGENT_ERROR" and not isinstance(ev.get("message"), str):
            raise VerifyError("Cannot send 'SUBAGENT_ERROR' without a 'message'.")
        if tag not in self.subagents:
            raise VerifyError("Cannot send '%s': no active subagent found with ID '%s'. A 'SUBAGENT_STARTED' event "
                              "must be sent first." % (t, tag))
        self.subagents.discard(tag)
        self.closed_subagents.add(tag)

    _subagent_error = _subagent_finished

    # snapshots and run lifecycle ---------------------------------------
    def _messages_snapshot(self, ev, t, tag):
        self._seed(ev.get("messages"), True)

    def _run_started(self, ev, t, tag):
        self.run_started = True
        inp = ev.get("input")
        self._seed(inp.get("messages") if isinstance(inp, dict) else None, False)

    def _run_finished(self, ev, t, tag):
        active_steps = [("%s (subagent '%s')" % (n, o)) if o is not None else n
                        for o, names in self.steps.items() for n in sorted(names)]
        if active_steps:
            raise VerifyError("Cannot send 'RUN_FINISHED' while steps are still active: %s" % ", ".join(active_steps))
        for label, open_ in (("text messages", self.messages), ("reasoning messages", self.reasoning_messages),
                             ("reasoning spans", self.reasoning_spans), ("tool calls", self.tool_calls),
                             ("subagents", self.subagents)):
            if open_:
                raise VerifyError("Cannot send 'RUN_FINISHED' while %s are still active: %s"
                                  % (label, ", ".join(sorted(map(str, open_)))))
        self.run_finished = True

    def _run_error(self, ev, t, tag):
        self.run_error = True
