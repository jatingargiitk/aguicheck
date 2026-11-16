# aguicheck

Check the event stream your [AG-UI](https://github.com/ag-ui-protocol/ag-ui) agent sends, before a frontend finds the problem for you.

AG-UI 1.0 shipped on 2026-09-17 with a JSON Schema for every event and a strict
TypeScript client (`@ag-ui/client`) that rejects a stream the moment it breaks a
rule: a tool call still open at `RUN_FINISHED`, a `delta` that isn't a string,
an event after the run ended. The official conformance corpus tests
*clients* and leaves producer correctness to each integration's own suite, and
the Python SDK has no client to replay a stream through. aguicheck is that
check for the producer side, meaning your FastAPI or LangGraph server. Point it at a live endpoint or a
recorded stream and it tells you which event the reference client would reject
and why, which fields it would quietly strip, and which pre-1.0 shapes only
keep working until the compatibility shims expire on **2027-09-17**.

- **Errors** are what the reference client rejects: schema violations on known
  fields, broken START/CONTENT/END pairing, events after `RUN_FINISHED`,
  subagent attribution mismatches, chunk streams that contradict their opener,
  and (the spec's truncation rule) runs that end without `RUN_FINISHED` or
  `RUN_ERROR`. Sequence errors use
  the client's own wording, so you can search for them in its logs and source.
- **Warnings** are what the client strips or translates: unknown properties,
  unknown event types and union members, `THINKING_*` events, the optional
  `null`s on the AG-UI deprecation list, and newer `protocolVersion`s. It also
  warns about state patches that don't apply and tool-call arguments that
  aren't valid JSON.
- It also prints a **transcript** of what a client ends up holding: messages,
  tool calls with their arguments and results, and the final state.

## Install

```bash
pip install git+https://github.com/jatingargiitk/aguicheck
```

Python 3.9+, one dependency (`jsonschema`). The AG-UI 1.0 schema is bundled.

## Check a live endpoint

`aguicheck run URL` POSTs a minimal 1.0 `RunAgentInput` (one user message) with
`Accept: text/event-stream` and checks the response. `examples/demo_server.py`
is a tiny standard-library agent you can try it on:

```bash
python examples/demo_server.py 8765 &
```

A well-formed run:

```console
$ aguicheck run http://127.0.0.1:8765/agent -m Pune --run-id r1
http://127.0.0.1:8765/agent: 12 events, 1 run
  transcript:
    run r1: finished
    assistant m1: Checking the weather in Pune.
    tool call1 get_weather({"city": "Pune"}) -> 18C, clear
    state: {"lookups": 1}
OK: 0 errors, 0 warnings
[exit 0]
```

The same run with four mistakes real servers make: tool arguments sent as an
object, a text message never closed, `outcome: null`, and a misspelt
`toolCallID`:

```console
$ aguicheck run http://127.0.0.1:8765/agent-buggy -m Pune --run-id r1
http://127.0.0.1:8765/agent-buggy: 11 events, 1 run
  error #6   TOOL_CALL_ARGS /delta: {"city": "Pune"} is not of type 'string'
  warn  #8   TOOL_CALL_RESULT /toolCallID: property 'toolCallID' is not in the AG-UI 1.0 schema; the reference client strips it with a warning
  warn  #10  RUN_FINISHED /outcome: RUN_FINISHED.outcome: null is a pre-1.0 shape, omit the field; the reference client upgrades it with a warning only until 2027-09-17
  error #10  RUN_FINISHED: Cannot send 'RUN_FINISHED' while text messages are still active: m1
  transcript:
    run r1: RUN_FINISHED rejected
    assistant m1: Checking the weather in Pune.
    tool call1 get_weather() -> 18C, clear
    state: {"lookups": 1}
FAIL: 2 errors, 2 warnings (first at event #6)
[exit 1]
```

A stream that stops half way:

```console
$ aguicheck run http://127.0.0.1:8765/agent-cut -m Pune --run-id r1 --no-transcript
http://127.0.0.1:8765/agent-cut: 8 events, 1 run
  error end  (end of stream): the stream ended without RUN_FINISHED or RUN_ERROR: a truncated run has no outcome and a client must not report it as succeeded; still open: tool call 'call1'
FAIL: 1 error, 0 warnings
[exit 1]
```

Options: `-m/--message`, `-H "Authorization: Bearer ..."` (repeatable),
`--state '{"k": 1}'`, `--thread-id`, `--run-id`, `--timeout`, and
`--save run.sse` to keep the raw response for later.

## Check a recorded stream

`aguicheck check` reads SSE captures (`data:` frames), JSON Lines, a JSON
array of events, an official conformance fixture, or `-` for stdin.
`examples/sdk-0.1.22-thinking.sse` is the real output of the official Python
SDK, `ag-ui-protocol` 0.1.22, for a run that streams its reasoning
(regenerate it with `examples/record_sdk_thinking.py`):

```console
$ aguicheck check examples/sdk-0.1.22-thinking.sse
examples/sdk-0.1.22-thinking.sse: 10 events, 1 run
  warn  #1   THINKING_START: THINKING_START is a pre-1.0 shape, use REASONING_START; the reference client upgrades it with a warning only until 2027-09-17; its title "Planning" is dropped, REASONING_START has none
  warn  #2   THINKING_TEXT_MESSAGE_START: THINKING_TEXT_MESSAGE_START is a pre-1.0 shape, use REASONING_MESSAGE_START; the reference client upgrades it with a warning only until 2027-09-17
  warn  #3   THINKING_TEXT_MESSAGE_CONTENT: THINKING_TEXT_MESSAGE_CONTENT is a pre-1.0 shape, use REASONING_MESSAGE_CONTENT; the reference client upgrades it with a warning only until 2027-09-17
  warn  #4   THINKING_TEXT_MESSAGE_END: THINKING_TEXT_MESSAGE_END is a pre-1.0 shape, use REASONING_MESSAGE_END; the reference client upgrades it with a warning only until 2027-09-17
  warn  #5   THINKING_END: THINKING_END is a pre-1.0 shape, use REASONING_END; the reference client upgrades it with a warning only until 2027-09-17
  transcript:
    run r1: finished
    reasoning minted-2: The user wants a short answer.
    assistant m1: Here it is.
OK: 0 errors, 5 warnings
[exit 0]
```

Those events still work today. `--strict` makes warnings fail the run, which
is how to catch them in CI before the shims are removed:

```console
$ aguicheck check examples/sdk-0.1.22-thinking.sse --strict --no-transcript
examples/sdk-0.1.22-thinking.sse: 10 events, 1 run
  warn  #1   THINKING_START: THINKING_START is a pre-1.0 shape, use REASONING_START; the reference client upgrades it with a warning only until 2027-09-17; its title "Planning" is dropped, REASONING_START has none
  warn  #2   THINKING_TEXT_MESSAGE_START: THINKING_TEXT_MESSAGE_START is a pre-1.0 shape, use REASONING_MESSAGE_START; the reference client upgrades it with a warning only until 2027-09-17
  warn  #3   THINKING_TEXT_MESSAGE_CONTENT: THINKING_TEXT_MESSAGE_CONTENT is a pre-1.0 shape, use REASONING_MESSAGE_CONTENT; the reference client upgrades it with a warning only until 2027-09-17
  warn  #4   THINKING_TEXT_MESSAGE_END: THINKING_TEXT_MESSAGE_END is a pre-1.0 shape, use REASONING_MESSAGE_END; the reference client upgrades it with a warning only until 2027-09-17
  warn  #5   THINKING_END: THINKING_END is a pre-1.0 shape, use REASONING_END; the reference client upgrades it with a warning only until 2027-09-17
OK: 0 errors, 5 warnings
[exit 1]
```

`--format json` prints every finding (level, code, event index, JSON pointer,
message) plus the transcript, for scripts.

Exit codes: `0` clean (warnings allowed unless `--strict`), `1` errors found,
`2` the input could not be read or the endpoint could not be reached.

## In CI

```yaml
- run: pip install git+https://github.com/jatingargiitk/aguicheck
- run: |
    python -m myagent.server &          # your AG-UI server
    sleep 2
    aguicheck run http://127.0.0.1:8000/agent -m "What is the weather in Paris?" --strict
```

## How it decides

1. **Shape.** Each event is validated against its own definition in the bundled
   AG-UI 1.0 schema (`spec/1.0/schema.json`). The schema closes every object,
   but the client is a forgiving receiver, so failures are sorted the same
   way: an undeclared property or an unrecognised union member (an unknown
   `outcome.type`, content part or patch `op`) is a warning; a wrong type, a
   missing required field or an unknown member of a closed string set (a
   message `role`) is an error.
2. **Pre-1.0 shapes.** The `THINKING_*` events, the optional `null`s and the
   `binary` content part from `DEPRECATIONS.md` are upgraded the way the
   client's compatibility boundary does it, with a warning.
3. **Sequence.** `*_CHUNK` events are expanded into START/CONTENT/END, then
   every event goes through a Python port of the client's `verifyEvents`: run
   lifecycle, text messages, tool calls, reasoning spans and messages, steps
   (per owner), subagents and attribution.
4. **Apply.** State snapshots and RFC 6902 deltas are applied, and messages and
   tool calls are assembled into the transcript.

`tests/test_conformance.py` replays all 68 streams from the official
`spec/1.0/conformance` corpus. For the 63 that don't pin an older client peer,
aguicheck's verdict matches the outcome the fixture requires of the reference
client, its error contains the fixture's expected error text, and the expected
warnings are reported. The 5 `era-*` fixtures test a client downgrading for an
old peer, which says nothing about the stream, so they are skipped.

## Limits

- SSE and JSON only; the optional protobuf binding isn't read.
- One schema version (1.0). A producer declaring a newer `protocolVersion` gets
  a warning, and its newer fields show up as unknown.
- It checks what a client receives. It doesn't run your frontend's tools,
  apply `ACTIVITY_DELTA` patches, or judge what the agent said.
- The tool-argument JSON check is aguicheck's own; the protocol only requires
  arguments to be a string.

## License

MIT. The bundled `aguicheck/data/schema-1.0.json` and the conformance fixtures
in `tests/conformance/` are copied unchanged from
[ag-ui-protocol/ag-ui](https://github.com/ag-ui-protocol/ag-ui) (MIT); see
`aguicheck/data/AG-UI-LICENSE`.
