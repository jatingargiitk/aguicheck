"""Every output pasted in README.md must be the real output in examples/expected/.

examples/readme_examples.sh regenerates those files; CI runs it and fails on a diff.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_shows_the_recorded_outputs():
    readme = (ROOT / "README.md").read_text()
    expected = sorted((ROOT / "examples" / "expected").glob("*.txt"))
    assert len(expected) == 5
    for path in expected:
        assert path.read_text().rstrip("\n") in readme, path.name
