"""
tests/test_cli.py

Unit tests for promptcut command line interface.
"""

import json
from pathlib import Path
import pytest

from promptcut.cli.main import main
from promptcut.models.transcript import Transcript, Word


@pytest.fixture
def sample_media(tmp_path: Path) -> Path:
    f = tmp_path / "test_video.mp4"
    f.write_bytes(b"sample video bytes for cli test")
    return f


@pytest.fixture
def sample_sidecar(tmp_path: Path) -> Path:
    f = tmp_path / "speakers.json"
    f.write_text(json.dumps([{"start": 0.0, "end": 10.0, "speaker": "Host"}]), encoding="utf-8")
    return f


def test_cli_transcribe_and_inspect(sample_media: Path, sample_sidecar: Path, tmp_path: Path, capsys: pytest.CaptureFixture):
    out_transcript = tmp_path / "out_transcript.json"

    # 1. Run transcribe
    ret = main([
        "transcribe",
        str(sample_media),
        "-o", str(out_transcript),
        "--adapter", "mock",
        "--sidecar", str(sample_sidecar),
    ])
    assert ret == 0
    assert out_transcript.exists()

    # Verify transcript contents
    data = json.loads(out_transcript.read_text(encoding="utf-8"))
    assert len(data["words"]) > 0
    assert data["words"][0]["speaker"] == "Host"

    # 2. Run inspect
    capsys.readouterr()  # clear buffer
    ret_inspect = main(["inspect", str(out_transcript)])
    assert ret_inspect == 0
    captured = capsys.readouterr()
    assert "Transcript: test_video.mp4" in captured.out
    assert "Welcome" in captured.out
    assert "Host" in captured.out


def test_cli_missing_file():
    ret = main(["transcribe", "non_existent_file.mp4"])
    assert ret == 1

    ret2 = main(["inspect", "non_existent_transcript.json"])
    assert ret2 == 1
