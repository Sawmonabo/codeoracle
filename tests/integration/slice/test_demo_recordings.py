import json
from pathlib import Path


def test_demo_cast_files_are_asciinema_v2_json_lines() -> None:
    expected_markers = {
        "proven.cast": "PROVEN",
        "unknown.cast": "UNKNOWN",
        "live-edit.cast": "APPLIED",
    }

    for filename, marker in expected_markers.items():
        path = Path("tests/fixtures/acceptance-recordings") / filename
        text = path.read_text()
        lines = text.splitlines()
        header = json.loads(lines[0])
        assert header["version"] == 2
        assert marker in text
        for line in lines[1:]:
            event = json.loads(line)
            assert isinstance(event, list)
            assert len(event) == 3
