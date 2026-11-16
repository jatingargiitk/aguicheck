from aguicheck.check import check_stream
from aguicheck.legacy import Normalizer

from helpers import run, stream


def deprecated(result):
    return [(f.index, f.path) for f in result.findings if f.code == "deprecated"]


def test_optional_nulls_are_upgraded_with_a_warning():
    events = [
        {"type": "RUN_STARTED", "threadId": "t", "runId": "r", "parentRunId": None, "rawEvent": None},
        {"type": "TOOL_CALL_START", "toolCallId": "c", "toolCallName": "f", "parentMessageId": None},
        {"type": "TOOL_CALL_END", "toolCallId": "c"},
        {"type": "RUN_FINISHED", "threadId": "t", "runId": "r", "result": None, "outcome": None},
    ]
    r = check_stream(stream(events))
    assert not r.errors
    assert deprecated(r) == [(0, "/rawEvent"), (0, "/parentRunId"), (1, "/parentMessageId"),
                             (3, "/result"), (3, "/outcome")]
    assert "2027-09-17" in r.warnings[0].message


def test_null_that_is_not_on_the_list_is_still_an_error():
    r = check_stream(stream(run({"type": "TEXT_MESSAGE_START", "messageId": "m", "name": None},
                                {"type": "TEXT_MESSAGE_END", "messageId": "m"})))
    assert [f.path for f in r.errors] == ["/name"]


def test_nested_nulls_in_run_input_and_snapshot():
    events = [
        {"type": "RUN_STARTED", "threadId": "t", "runId": "r", "input": {
            "threadId": "t", "runId": "r", "messages": [], "context": [], "state": {},
            "tools": [{"name": "f", "description": "d", "parameters": None}], "forwardedProps": None}},
        {"type": "MESSAGES_SNAPSHOT", "messages": [{"id": "u", "role": "user", "content": [
            {"type": "image", "source": {"type": "url", "value": "https://example.com/a.png"}, "metadata": None}]}]},
        {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"},
    ]
    r = check_stream(stream(events))
    assert not r.errors, [f.to_dict() for f in r.errors]
    assert deprecated(r) == [(0, "/input/forwardedProps"), (0, "/input/tools/0/parameters"),
                             (1, "/messages/0/content/0/metadata")]


def test_thinking_events_translate_and_keep_their_ids():
    n = Normalizer()
    start, _ = n.normalize({"type": "THINKING_START", "title": "plan"})
    msg, _ = n.normalize({"type": "THINKING_TEXT_MESSAGE_START"})
    content, _ = n.normalize({"type": "THINKING_TEXT_MESSAGE_CONTENT", "delta": "hm"})
    end, notes = n.normalize({"type": "THINKING_END"})
    assert start["type"] == "REASONING_START" and "title" not in start
    assert msg["role"] == "reasoning" and content["messageId"] == msg["messageId"]
    assert end["messageId"] == start["messageId"]
    assert notes[0].code == "deprecated" and "REASONING_END" in notes[0].message


def test_thinking_content_without_an_opener_is_rejected_downstream():
    r = check_stream(stream(run({"type": "THINKING_TEXT_MESSAGE_CONTENT", "delta": "x"})))
    assert [f.code for f in r.errors] == ["sequence"]
    assert "No active reasoning message" in r.errors[0].message


def test_legacy_binary_content_part():
    r = check_stream(stream(run({"type": "MESSAGES_SNAPSHOT", "messages": [{"id": "u", "role": "user", "content": [
        {"type": "binary", "mimeType": "image/png", "data": "AAAA"}]}]})))
    assert ("deprecated", "/messages/0/content/0") in [(f.code, f.path) for f in r.warnings]
