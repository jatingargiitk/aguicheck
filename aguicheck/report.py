"""Text and JSON output."""

import json
from typing import Any, Dict, List

from .check import Result
from .findings import ERROR


def _clip(value: Any, limit: int = 70) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_text(name: str, result: Result, transcript: bool = True) -> str:
    lines: List[str] = []
    runs = result.transcript.runs
    lines.append("%s: %d event%s, %d run%s" % (name, result.events, "" if result.events == 1 else "s",
                                               len(runs), "" if len(runs) == 1 else "s"))
    for f in sorted(result.findings, key=lambda f: (f.index if f.index is not None else -1)):
        if f.index is None:
            where = ""
        elif f.index >= result.events:
            where = "end"
        else:
            where = "#%d" % f.index
        what = f.event_type or ""
        if f.path:
            what = (what + " " + f.path).strip()
        label = "error" if f.level == ERROR else "warn "
        lines.append("  %s %-4s %s: %s" % (label, where, what or "-", f.message))
    if transcript:
        t = result.transcript
        lines.append("  transcript:")
        for r in t.runs:
            lines.append("    run %s: %s" % (r.get("runId"), r.get("end") or "never ended"))
        for mid in t.order:
            m = t.messages[mid]
            lines.append("    %s %s: %s" % (m.get("role"), mid, _clip(m.get("content", ""))))
        for tid in t.tool_order:
            tc = t.tool_calls[tid]
            res = "" if tc["result"] is None else " -> " + _clip(tc["result"], 40)
            lines.append("    tool %s %s(%s)%s" % (tid, tc["name"], _clip(tc["args"], 50), res))
        if t.has_state:
            lines.append("    state: %s" % _clip(t.state, 100))
    errs, warns = len(result.errors), len(result.warnings)
    if errs:
        first = result.first_rejection
        at = " (first at event #%d)" % first.index if first.index is not None and first.index < result.events else ""
        lines.append("FAIL: %d error%s, %d warning%s%s" % (errs, "" if errs == 1 else "s", warns,
                                                           "" if warns == 1 else "s", at))
    else:
        lines.append("OK: 0 errors, %d warning%s" % (warns, "" if warns == 1 else "s"))
    return "\n".join(lines)


def render_json(name: str, result: Result) -> Dict[str, Any]:
    return {
        "source": name,
        "events": result.events,
        "ok": not result.errors,
        "errors": len(result.errors),
        "warnings": len(result.warnings),
        "findings": [f.to_dict() for f in sorted(result.findings,
                                                 key=lambda f: (f.index if f.index is not None else -1))],
        "transcript": result.transcript.to_dict(),
    }
