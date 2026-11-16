"""Replay the official AG-UI 1.0 conformance streams and compare verdicts.

Each fixture states what the reference TypeScript client must do with the
stream: consume it ("completed") or reject it ("failed", with a substring of
the error), and which warnings it must print. aguicheck should reach the same
verdict, since its errors are meant to be exactly what that client rejects.
"""

import json
from pathlib import Path

import pytest

from aguicheck.check import check_stream

HERE = Path(__file__).parent / "conformance"
FIXTURES = sorted(p for p in HERE.glob("*.json"))


def _listed():
    return [ln.strip() for ln in (HERE / "MANIFEST.txt").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


def test_corpus_is_complete():
    assert [p.name for p in FIXTURES] == _listed()
    assert len(FIXTURES) == 68


def _text(findings):
    return [f.message + " " + f.path for f in findings]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_verdict(path):
    fixture = json.loads(path.read_text())
    if fixture.get("client"):
        pytest.skip("pins an older client peer (%s); tests a client downgrade, not the stream"
                    % fixture["client"].get("maxProtocolVersion"))
    expect = fixture["expect"]
    result = check_stream([(0, e) for e in fixture["stream"]])
    got = "failed" if result.errors else "completed"
    assert got == expect["outcome"], [f.to_dict() for f in result.findings]
    if expect["outcome"] == "failed":
        # zod's messages are JSON-escaped in the fixtures; compare the plain text.
        needle = expect["errorContains"].replace('\\"', '"')
        assert any(needle in t for t in _text(result.errors)), _text(result.errors)
    for w in expect.get("warnings", []):
        assert any(w in t for t in _text(result.warnings)), (w, _text(result.warnings))
    if expect.get("noWarnings"):
        assert result.warnings == []
