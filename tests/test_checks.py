from aguicheck.check import check_stream
from aguicheck.findings import ERROR, WARNING

from helpers import run, stream


def codes(result, level=None):
    return [f.code for f in result.findings if level is None or f.level == level]


def test_clean_run_with_text_tool_and_state():
    r = check_stream(stream(run(
        {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Hi "},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "there"},
        {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "lookup", "parentMessageId": "m1"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": "{\"q\": "},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": "\"x\"}"},
        {"type": "TOOL_CALL_END", "toolCallId": "c1"},
        {"type": "TOOL_CALL_RESULT", "messageId": "m2", "toolCallId": "c1", "content": "42"},
        {"type": "STATE_SNAPSHOT", "snapshot": {"n": 1, "items": []}},
        {"type": "STATE_DELTA", "delta": [{"op": "replace", "path": "/n", "value": 2},
                                          {"op": "add", "path": "/items/-", "value": "a"}]},
    )))
    assert r.findings == []
    t = r.transcript.to_dict()
    assert t["messages"] == [{"id": "m1", "role": "assistant", "content": "Hi there"}]
    assert t["toolCalls"][0]["args"] == '{"q": "x"}' and t["toolCalls"][0]["result"] == "42"
    assert t["state"] == {"n": 2, "items": ["a"]}
    assert t["runs"][0]["end"] == "finished"


def test_snake_case_fields_are_errors_and_stripped_warnings():
    # What pydantic's model_dump_json() gives without by_alias=True.
    r = check_stream(stream(run(
        {"type": "TOOL_CALL_START", "tool_call_id": "c1", "tool_call_name": "lookup"},
    )))
    errors = [f for f in r.findings if f.level == ERROR]
    assert any("'toolCallId' is a required property" in f.message for f in errors)
    warned = {f.path for f in r.findings if f.code == "unknown-property"}
    assert warned == {"/tool_call_id", "/tool_call_name"}


def test_wrong_type_is_an_error_with_a_path():
    r = check_stream(stream(run(
        {"type": "TEXT_MESSAGE_START", "messageId": "m1"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": 7},
        {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
    )))
    [f] = r.errors
    assert (f.index, f.path, f.code) == (2, "/delta", "schema")


def test_unknown_event_type_is_dropped_with_a_warning():
    r = check_stream(stream(run({"type": "FUTURE_EVENT", "x": 1})))
    assert codes(r) == ["unknown-event"]
    assert not r.errors


def test_tool_call_left_open_at_run_finished():
    r = check_stream(stream(run(
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "lookup"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": "{}"},
    )))
    [f] = r.errors
    assert f.event_type == "RUN_FINISHED"
    assert "tool calls are still active: c1" in f.message


def test_truncated_stream_lists_what_is_still_open():
    events = run({"type": "TEXT_MESSAGE_START", "messageId": "m1"},
                 {"type": "STEP_STARTED", "stepName": "plan"})[:-1]
    r = check_stream(stream(events))
    [f] = r.errors
    assert f.code == "truncated" and f.index == 3
    assert "text message 'm1'" in f.message and "step 'plan'" in f.message


def test_empty_stream_is_an_error():
    assert codes(check_stream([])) == ["truncated"]


def test_non_object_and_untyped_events():
    r = check_stream([(0, [1, 2]), (0, {"no": "type"})])
    assert [f.message for f in r.errors][:2] == ["an event must be a JSON object, got list",
                                                 "event has no string 'type'"]


def test_tool_args_that_are_not_json_warn():
    r = check_stream(stream(run(
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "lookup"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": "{'q': 1}"},
        {"type": "TOOL_CALL_END", "toolCallId": "c1"},
    )))
    assert codes(r) == ["tool-args"]
    assert r.warnings[0].index == 3


def test_state_patch_that_cannot_apply_keeps_previous_state():
    r = check_stream(stream(run(
        {"type": "STATE_SNAPSHOT", "snapshot": {"a": 1}},
        {"type": "STATE_DELTA", "delta": [{"op": "replace", "path": "/missing", "value": 2}]},
    )))
    assert codes(r) == ["state-patch"]
    assert r.transcript.state == {"a": 1}


def test_protocol_version_newer_and_garbled():
    newer = check_stream(stream([{"type": "RUN_STARTED", "threadId": "t", "runId": "r", "protocolVersion": "1.1"},
                                 {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"}]))
    assert codes(newer) == ["protocol-version"] and "1.1" in newer.warnings[0].message
    same = check_stream(stream([{"type": "RUN_STARTED", "threadId": "t", "runId": "r", "protocolVersion": "1.0"},
                                {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"}]))
    assert same.findings == []


def test_two_runs_and_run_error_restart():
    events = run(thread="t", run_id="r1") + [
        {"type": "RUN_STARTED", "threadId": "t", "runId": "r2"},
        {"type": "RUN_ERROR", "message": "model timed out"},
        {"type": "RUN_STARTED", "threadId": "t", "runId": "r3"},
        {"type": "RUN_FINISHED", "threadId": "t", "runId": "r3"},
    ]
    r = check_stream(stream(events))
    assert r.findings == []
    assert [x["end"] for x in r.transcript.runs] == ["finished", "error: model timed out", "finished"]


def test_levels_are_only_error_or_warning():
    r = check_stream(stream(run({"type": "FUTURE"}, {"type": "TEXT_MESSAGE_END", "messageId": "x"})))
    assert {f.level for f in r.findings} == {ERROR, WARNING}
