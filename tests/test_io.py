import json

import pytest

from aguicheck.io import InputError, load_events, parse_sse, run_input
from aguicheck.schema import check_definition


def test_sse_framing_comments_and_multiline_data():
    text = (": keep-alive\n"
            "event: message\n"
            "data: {\"type\":\"RUN_STARTED\",\n"
            "data: \"threadId\":\"t\",\"runId\":\"r\"}\n"
            "\n"
            "id: 2\n"
            "data:{\"type\":\"RUN_FINISHED\",\"threadId\":\"t\",\"runId\":\"r\"}\r\n"
            "\r\n")
    events = load_events(text)
    assert [e["type"] for _, e in events] == ["RUN_STARTED", "RUN_FINISHED"]
    assert [line for line, _ in events] == [3, 7]


def test_sse_last_event_without_blank_line():
    assert list(parse_sse(["data: 1"])) == [(1, "1")]


def test_jsonl_array_and_fixture_forms():
    a = {"type": "RUN_STARTED", "threadId": "t", "runId": "r"}
    b = {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"}
    jsonl = json.dumps(a) + "\n\n" + json.dumps(b) + "\n"
    assert [e for _, e in load_events(jsonl)] == [a, b]
    assert [e for _, e in load_events(json.dumps([a, b]))] == [a, b]
    assert [e for _, e in load_events(json.dumps({"name": "x", "stream": [a, b]}))] == [a, b]
    assert load_events("   ") == []


def test_bad_json_names_the_line():
    with pytest.raises(InputError, match="line 3"):
        load_events("data: {\"type\": \"RUN_STARTED\"}\n\ndata: {nope\n\n")


def test_run_input_is_a_valid_1_0_request():
    body = run_input("hi", "t1", "r1", {"n": 1})
    assert body["messages"][0]["content"] == "hi"
    assert check_definition(body, "RunAgentInput") == []
