"""Command line entry point."""

import argparse
import json
import sys
from typing import List, Optional

from . import SCHEMA_VERSION, __version__
from .check import check_stream
from .io import InputError, fetch, load_events, run_input
from .report import render_json, render_text


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _emit(outputs, fmt: str) -> None:
    if fmt == "json":
        docs = [doc for _, doc in outputs]
        print(json.dumps(docs[0] if len(docs) == 1 else docs, indent=2, ensure_ascii=False))
    else:
        print("\n\n".join(text for text, _ in outputs))


def _exit_code(results, strict: bool) -> int:
    if any(r.errors for r in results):
        return 1
    if strict and any(r.warnings for r in results):
        return 1
    return 0


def cmd_check(args) -> int:
    outputs, results = [], []
    for path in args.files:
        try:
            events = load_events(_read(path))
        except (OSError, InputError) as exc:
            print("aguicheck: %s: %s" % (path, exc), file=sys.stderr)
            return 2
        result = check_stream(events)
        results.append(result)
        name = "stdin" if path == "-" else path
        outputs.append((render_text(name, result, not args.no_transcript) if args.format == "text" else None,
                        render_json(name, result) if args.format == "json" else None))
    _emit(outputs, args.format)
    return _exit_code(results, args.strict)


def cmd_run(args) -> int:
    headers = {}
    for h in args.header or []:
        k, sep, v = h.partition(":")
        if not sep:
            print("aguicheck: --header must look like 'Name: value'", file=sys.stderr)
            return 2
        headers[k.strip()] = v.strip()
    state = json.loads(args.state) if args.state else None
    body = run_input(args.message, args.thread_id, args.run_id, state)
    try:
        raw = fetch(args.url, body, headers, args.timeout)
        events = load_events(raw)
    except InputError as exc:
        print("aguicheck: %s" % exc, file=sys.stderr)
        return 2
    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            fh.write(raw)
    result = check_stream(events)
    if args.format == "json":
        print(json.dumps(render_json(args.url, result), indent=2, ensure_ascii=False))
    else:
        print(render_text(args.url, result, not args.no_transcript))
    return _exit_code([result], args.strict)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aguicheck",
        description="Check an AG-UI event stream against the %s schema and the sequence rules the reference "
                    "client enforces." % SCHEMA_VERSION)
    p.add_argument("--version", action="version", version="aguicheck %s (AG-UI %s)" % (__version__, SCHEMA_VERSION))
    sub = p.add_subparsers(dest="command")

    def common(sp):
        sp.add_argument("--format", choices=["text", "json"], default="text")
        sp.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
        sp.add_argument("--no-transcript", action="store_true", help="only print findings")

    c = sub.add_parser("check", help="check recorded streams (SSE, JSON Lines, JSON array or '-' for stdin)")
    c.add_argument("files", nargs="+")
    common(c)
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("run", help="POST a RunAgentInput to a live AG-UI endpoint and check what comes back")
    r.add_argument("url")
    r.add_argument("-m", "--message", default="Hello", help="the user message to send (default: Hello)")
    r.add_argument("-H", "--header", action="append", help="extra request header, 'Name: value' (repeatable)")
    r.add_argument("--state", help="initial state as JSON")
    r.add_argument("--thread-id")
    r.add_argument("--run-id")
    r.add_argument("--timeout", type=float, default=120.0)
    r.add_argument("--save", metavar="FILE", help="also write the raw SSE response to FILE")
    common(r)
    r.set_defaults(func=cmd_run)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
