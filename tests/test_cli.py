import json
import sys
import threading
from pathlib import Path

import pytest

from aguicheck.cli import main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import demo_server  # noqa: E402


@pytest.fixture(scope="module")
def server():
    srv = demo_server.serve(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % srv.server_port
    srv.shutdown()


def test_run_good_endpoint(server, capsys, tmp_path):
    saved = tmp_path / "run.sse"
    code = main(["run", server + "/agent", "-m", "Pune", "--run-id", "r1", "--save", str(saved)])
    out = capsys.readouterr().out
    assert code == 0
    assert "OK: 0 errors, 0 warnings" in out
    assert 'get_weather({"city": "Pune"}) -> 18C, clear' in out
    # The saved capture checks the same way offline.
    assert main(["check", str(saved), "--no-transcript"]) == 0
    assert saved.read_text().startswith('data: {"type": "RUN_STARTED"')


def test_run_buggy_endpoint_json(server, capsys):
    code = main(["run", server + "/agent-buggy", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert code == 1
    assert (doc["errors"], doc["warnings"]) == (2, 2)
    assert [(f["index"], f["code"]) for f in doc["findings"]] == [
        (6, "schema"), (8, "unknown-property"), (10, "deprecated"), (10, "sequence")]
    assert doc["transcript"]["runs"][0]["end"] == "RUN_FINISHED rejected"


def test_run_cut_stream(server, capsys):
    assert main(["run", server + "/agent-cut"]) == 1
    assert "the stream ended without RUN_FINISHED" in capsys.readouterr().out


def test_strict_turns_warnings_into_failure(capsys):
    sse = str(ROOT / "examples" / "sdk-0.1.22-thinking.sse")
    assert main(["check", sse]) == 0
    assert main(["check", sse, "--strict"]) == 1
    assert "OK: 0 errors, 5 warnings" in capsys.readouterr().out


def test_check_reads_stdin_and_several_files(monkeypatch, capsys):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        '{"type":"RUN_STARTED","threadId":"t","runId":"r"}\n{"type":"RUN_FINISHED","threadId":"t","runId":"r"}\n'))
    fixture = str(ROOT / "tests" / "conformance" / "first-event-not-run-started-fatal.json")
    assert main(["check", "-", fixture, "--format", "json"]) == 1
    docs = json.loads(capsys.readouterr().out)
    assert [d["ok"] for d in docs] == [True, False]


def test_usage_errors(capsys, tmp_path):
    assert main([]) == 2
    assert main(["check", str(tmp_path / "missing.sse")]) == 2
    assert main(["run", "http://127.0.0.1:9/agent", "-H", "no-colon"]) == 2
    bad = tmp_path / "bad.sse"
    bad.write_text("data: {oops\n\n")
    assert main(["check", str(bad)]) == 2
    assert "not JSON" in capsys.readouterr().err


def test_unreachable_endpoint_is_exit_2(capsys):
    assert main(["run", "http://127.0.0.1:9/agent", "--timeout", "2"]) == 2
    assert "request to" in capsys.readouterr().err


def test_non_sse_response_is_exit_2(server, capsys):
    assert main(["run", server + "/nope"]) == 2
