import pytest

from aguicheck.check import check_stream
from aguicheck.chunks import ChunkError, ChunkExpander
from aguicheck.state import PatchError, apply_patch
from aguicheck.verify import Verifier, VerifyError

from helpers import run, stream


def test_text_chunks_expand_and_close_on_the_next_event():
    x = ChunkExpander()
    out = x.feed({"type": "TEXT_MESSAGE_CHUNK", "messageId": "m", "delta": "a"})
    assert [e["type"] for e in out] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert [e["type"] for e in x.feed({"type": "TEXT_MESSAGE_CHUNK", "delta": "b"})] == ["TEXT_MESSAGE_CONTENT"]
    out = x.feed({"type": "STEP_STARTED", "stepName": "s"})
    assert [e["type"] for e in out] == ["TEXT_MESSAGE_END", "STEP_STARTED"]


def test_tool_chunk_needs_a_name_and_must_agree():
    x = ChunkExpander()
    with pytest.raises(ChunkError, match="must have a toolCallName"):
        x.feed({"type": "TOOL_CALL_CHUNK", "toolCallId": "c"})
    x.feed({"type": "TOOL_CALL_CHUNK", "toolCallId": "c", "toolCallName": "f", "delta": "{"})
    with pytest.raises(ChunkError, match="toolCallName 'g'"):
        x.feed({"type": "TOOL_CALL_CHUNK", "toolCallName": "g", "delta": "}"})


def test_chunks_left_open_close_at_end_of_stream():
    events = [{"type": "RUN_STARTED", "threadId": "t", "runId": "r"},
              {"type": "TEXT_MESSAGE_CHUNK", "messageId": "m", "delta": "hi"}]
    r = check_stream(stream(events))
    assert [f.code for f in r.errors] == ["truncated"]
    assert r.transcript.messages["m"]["content"] == "hi"


def test_subagent_lifecycle_and_attribution():
    ok = check_stream(stream(run(
        {"type": "SUBAGENT_STARTED", "subagentRunId": "s1", "name": "researcher"},
        {"type": "TEXT_MESSAGE_START", "messageId": "m", "subagentRunId": "s1"},
        {"type": "TEXT_MESSAGE_END", "messageId": "m", "subagentRunId": "s1"},
        {"type": "SUBAGENT_FINISHED", "subagentRunId": "s1"},
    )))
    assert ok.findings == []
    bad = check_stream(stream(run(
        {"type": "TEXT_MESSAGE_START", "messageId": "m", "subagentRunId": "s1"},
        {"type": "TEXT_MESSAGE_END", "messageId": "m", "subagentRunId": "s2"},
    )))
    assert any("does not match the message 'm' opener's subagent 's1'" in f.message for f in bad.errors)


def test_steps_are_keyed_by_owner():
    v = Verifier()
    v.feed({"type": "RUN_STARTED"})
    v.feed({"type": "STEP_STARTED", "stepName": "tools"})
    v.feed({"type": "STEP_STARTED", "stepName": "tools", "subagentRunId": "s"})
    with pytest.raises(VerifyError, match="already active"):
        v.feed({"type": "STEP_STARTED", "stepName": "tools"})
    v.feed({"type": "STEP_FINISHED", "stepName": "tools", "subagentRunId": "s"})
    with pytest.raises(VerifyError, match="open under the parent agent"):
        v.feed({"type": "STEP_FINISHED", "stepName": "tools", "subagentRunId": "s"})


def test_messages_snapshot_seeds_owners():
    v = Verifier()
    v.feed({"type": "RUN_STARTED"})
    v.feed({"type": "MESSAGES_SNAPSHOT", "messages": [{"id": "m", "role": "assistant", "subagentRunId": "s"}]})
    with pytest.raises(VerifyError, match="does not match"):
        v.feed({"type": "TEXT_MESSAGE_START", "messageId": "m", "subagentRunId": "other"})


def test_json_patch_ops():
    doc = {"a": {"b": [1, 2]}, "c": 3}
    out = apply_patch(doc, [
        {"op": "add", "path": "/a/b/1", "value": 9},
        {"op": "remove", "path": "/c"},
        {"op": "copy", "from": "/a/b", "path": "/d"},
        {"op": "move", "from": "/d", "path": "/e"},
        {"op": "test", "path": "/e/1", "value": 9},
        {"op": "replace", "path": "/a/x~1y", "value": 0} if False else {"op": "add", "path": "/a/x~1y", "value": 0},
    ])
    assert out == {"a": {"b": [1, 9, 2], "x/y": 0}, "e": [1, 9, 2]}
    assert doc == {"a": {"b": [1, 2]}, "c": 3}
    with pytest.raises(PatchError):
        apply_patch(doc, [{"op": "test", "path": "/c", "value": 4}])
    with pytest.raises(PatchError):
        apply_patch(doc, [{"op": "move", "from": "/a", "path": "/a/z"}])
    assert apply_patch(doc, [{"op": "replace", "path": "", "value": [1]}]) == [1]
