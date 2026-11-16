"""Run every check over a stream and collect findings."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from . import SCHEMA_VERSION
from .chunks import ChunkError, ChunkExpander
from .findings import ERROR, WARNING, Finding
from .legacy import THINKING, Normalizer
from .schema import check_event, event_definitions
from .state import Transcript
from .verify import Verifier, VerifyError


@dataclass
class Result:
    events: int
    findings: List[Finding] = field(default_factory=list)
    transcript: Transcript = field(default_factory=Transcript)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.level == ERROR]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == WARNING]

    @property
    def first_rejection(self):
        errs = self.errors
        return errs[0] if errs else None


def _protocol_version(ev: Dict[str, Any]) -> List[Finding]:
    declared = ev.get("protocolVersion")
    if declared is None or declared == SCHEMA_VERSION:
        return []
    if not isinstance(declared, str) or not declared.replace(".", "", 1).isdigit() or declared.count(".") != 1:
        return [Finding(WARNING, "protocol-version",
                        "the producer declared protocol version %r, which the reference client cannot interpret"
                        % (declared,), path="/protocolVersion")]
    major, minor = (int(x) for x in declared.split("."))
    smaj, smin = (int(x) for x in SCHEMA_VERSION.split("."))
    if (major, minor) > (smaj, smin):
        return [Finding(WARNING, "protocol-version",
                        "the producer speaks protocol %s; aguicheck and the client it mirrors speak %s, so material "
                        "newer than %s is reported as unknown" % (declared, SCHEMA_VERSION, SCHEMA_VERSION),
                        path="/protocolVersion")]
    return []


def check_stream(events: List[Tuple[int, Any]]) -> Result:
    result = Result(events=len(events))
    defs = event_definitions()
    normalizer = Normalizer()
    chunks = ChunkExpander()
    verifier = Verifier()
    add = result.findings.append
    rejected_terminal = []

    def located(f: Finding, idx: int, etype: Any, line: int) -> Finding:
        f.index = idx
        f.event_type = etype if isinstance(etype, str) else None
        if line:
            f.extra.setdefault("line", line)
        return f

    def run_verifier(expanded: List[Dict[str, Any]], idx: int, etype: str, line: int, silent: bool = False) -> None:
        for e in expanded:
            try:
                verifier.feed(e)
            except VerifyError as exc:
                if silent:
                    continue
                add(located(Finding(ERROR, "sequence", str(exc)), idx, etype, line))
                if e.get("type") in ("RUN_FINISHED", "RUN_ERROR"):
                    rejected_terminal.append(idx)
                continue
            note = result.transcript.apply(e)
            if note and not silent:
                code = "state-patch" if note.startswith("Failed to apply state patch") else "tool-args"
                add(located(Finding(WARNING, code, note), idx, etype, line))

    for idx, (line, raw) in enumerate(events):
        if not isinstance(raw, dict):
            add(located(Finding(ERROR, "schema", "an event must be a JSON object, got %s" % type(raw).__name__),
                        idx, None, line))
            continue
        etype = raw.get("type")
        if not isinstance(etype, str):
            add(located(Finding(ERROR, "schema", "event has no string 'type'"), idx, None, line))
            continue
        ev, notes = normalizer.normalize(raw)
        for f in notes:
            add(located(f, idx, etype, line))
        definition = defs.get(ev["type"])
        if definition is None:
            if etype not in THINKING:
                add(located(Finding(WARNING, "unknown-event",
                                    "event type %r is not in AG-UI %s; the reference client drops it with a warning"
                                    % (etype, SCHEMA_VERSION)), idx, etype, line))
            continue
        shape = check_event(ev, definition)
        for f in shape:
            add(located(f, idx, etype, line))
        # The reference client rejects a malformed event outright. Still replay it
        # (silently) so the events after it are judged against the stream the
        # producer meant to send, not against errors this one already caused.
        silent = any(f.level == ERROR for f in shape)
        if any(f.extra.get("missing") for f in shape):
            continue  # without its ids the event cannot be replayed at all
        if ev["type"] == "RUN_STARTED":
            for f in _protocol_version(ev):
                add(located(f, idx, etype, line))
        try:
            expanded = chunks.feed(ev)
        except ChunkError as exc:
            if not silent:
                add(located(Finding(ERROR, "sequence", str(exc)), idx, etype, line))
            continue
        run_verifier(expanded, idx, etype, line, silent)

    tail = chunks.close_all()
    if tail:
        run_verifier(tail, len(events), "(end of stream)", 0)
    if not events:
        add(Finding(ERROR, "truncated", "the stream is empty: no RUN_STARTED, no terminal event"))
    elif verifier.run_active and not rejected_terminal:
        still_open = verifier.open_entities()
        add(Finding(
            ERROR, "truncated",
            "the stream ended without RUN_FINISHED or RUN_ERROR: a truncated run has no outcome and a client must "
            "not report it as succeeded" + ("; still open: " + ", ".join(still_open) if still_open else ""),
            index=len(events)))
    return result
