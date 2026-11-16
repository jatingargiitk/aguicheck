"""Validate one event against the AG-UI 1.0 JSON Schema and sort the failures.

The schema closes every object, but the reference TypeScript client is a
forgiving receiver: it strips a property it does not know and drops a union
member it does not recognise, both with a warning, while a known field holding
a wrong value is fatal. The classification below follows that split, so an
"error" here is something the client rejects and a "warning" is something it
quietly removes.
"""

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, List, Optional

from jsonschema import Draft202012Validator

from .findings import ERROR, WARNING, Finding, pointer

@lru_cache(maxsize=1)
def load_schema() -> Dict[str, Any]:
    text = resources.files("aguicheck").joinpath("data/schema-1.0.json").read_text("utf-8")
    return json.loads(text)


@lru_cache(maxsize=1)
def event_definitions() -> Dict[str, str]:
    """Map each 1.0 event type (e.g. "TEXT_MESSAGE_START") to its $defs name."""
    schema = load_schema()
    out = {}
    for ref in schema["$defs"]["Event"]["oneOf"]:
        name = ref["$ref"].rsplit("/", 1)[-1]
        const = schema["$defs"][name]["properties"]["type"]["const"]
        out[const] = name
    return out


@lru_cache(maxsize=None)
def _validator(definition: str) -> Draft202012Validator:
    schema = dict(load_schema())
    schema["$ref"] = "#/$defs/" + definition
    return Draft202012Validator(schema)


def validator_for(definition: str) -> Draft202012Validator:
    return _validator(definition)


def _resolve(branch: Dict[str, Any]) -> Dict[str, Any]:
    ref = branch.get("$ref", "")
    if ref.startswith("#/$defs/"):
        return load_schema()["$defs"][ref[len("#/$defs/"):]]
    return branch


def _discriminator(branches: List[Dict[str, Any]]) -> Optional[str]:
    keys = None
    for b in branches:
        props = b.get("properties", {})
        consts = {k for k, v in props.items() if isinstance(v, dict) and "const" in v}
        keys = consts if keys is None else keys & consts
    if keys:
        for preferred in ("type", "op", "role"):
            if preferred in keys:
                return preferred
        return sorted(keys)[0]
    return None


def _short(value: Any, limit: int = 60) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _classify(err, out: List[Finding]) -> None:
    path = list(err.absolute_path)
    kind = err.validator
    if kind in ("unevaluatedProperties", "additionalProperties"):
        names = []
        if isinstance(err.instance, dict):
            m = re.search(r"\((.*)\)", err.message)
            if m:
                names = re.findall(r"'([^']*)'", m.group(1))
        for name in names or ["?"]:
            out.append(Finding(
                WARNING, "unknown-property",
                "property %r is not in the AG-UI 1.0 schema; the reference client strips it with a warning"
                % name,
                path=pointer(path + [name]),
            ))
        return
    if kind in ("oneOf", "anyOf") and isinstance(err.validator_value, list):
        branches = [_resolve(b) for b in err.validator_value]
        inst = err.instance
        disc = _discriminator(branches)
        context = list(err.context or [])
        if disc is not None:
            if not isinstance(inst, dict):
                out.append(Finding(
                    ERROR, "schema",
                    "expected object, received %s: %s" % ("array" if isinstance(inst, list) else type(inst).__name__,
                                                           _short(inst)),
                    path=pointer(path),
                ))
                return
            if disc not in inst:
                out.append(Finding(ERROR, "schema", "missing discriminator %r" % disc, path=pointer(path)))
                return
            value = inst[disc]
            matches = [i for i, b in enumerate(branches)
                       if b.get("properties", {}).get(disc, {}).get("const") == value]
            if not matches:
                out.append(Finding(
                    WARNING, "unknown-member",
                    "%s %s is not an AG-UI 1.0 member here; the reference client drops it with a warning"
                    % (disc, _short(value)),
                    path=pointer(path),
                    extra={"dropped": True},
                ))
                return
            chosen = matches[0]
        else:
            # No discriminator (e.g. content: string | array). Keep the branches
            # whose own top-level "type" accepted the instance.
            ok = []
            for i in range(len(branches)):
                type_fail = any(
                    c.relative_schema_path and c.relative_schema_path[0] == i
                    and list(c.relative_schema_path)[1:] == ["type"] and list(c.absolute_path) == path
                    for c in context
                )
                if not type_fail:
                    ok.append(i)
            if len(ok) != 1:
                out.append(Finding(ERROR, "schema", err.message if len(err.message) < 200 else
                                   "%s is not valid under any of the allowed shapes" % _short(inst),
                                   path=pointer(path)))
                return
            chosen = ok[0]
        inner = [c for c in context if c.relative_schema_path and c.relative_schema_path[0] == chosen]
        if not inner:
            out.append(Finding(ERROR, "schema", err.message, path=pointer(path)))
            return
        for c in inner:
            _classify(c, out)
        return
    if kind == "const":
        msg = "expected %s, got %s" % (_short(err.validator_value), _short(err.instance))
    elif kind == "enum":
        msg = "%s is not one of %s (the reference client rejects an unknown member of a closed set)" % (
            _short(err.instance), _short(err.validator_value, 120))
    elif kind == "type":
        msg = "%s is not of type %r" % (_short(err.instance), err.validator_value)
    elif kind == "required":
        out.append(Finding(ERROR, "schema", err.message, path=pointer(path), extra={"missing": True}))
        return
    else:
        msg = err.message if len(err.message) < 200 else "%s fails %r" % (_short(err.instance), kind)
    out.append(Finding(ERROR, "schema", msg, path=pointer(path)))


def check_event(event: Dict[str, Any], definition: str) -> List[Finding]:
    found: List[Finding] = []
    for err in sorted(validator_for(definition).iter_errors(event), key=lambda e: list(map(str, e.absolute_path))):
        _classify(err, found)
    # One finding per (level, path, message) is enough.
    seen = set()
    unique = []
    for f in found:
        key = (f.level, f.path, f.message)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def check_definition(document: Any, definition: str) -> List[Finding]:
    """Validate any document (e.g. a RunAgentInput) against a named definition."""
    return check_event(document, definition)
