"""
tests/test_otio_export.py

Unit tests for OpenTimelineIO (OTIO) export serialization.
Verifies structure, tracks, markers, clips, and schema conformity.
"""

import json
from pathlib import Path
import pytest

from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import AddCaption, CutRange, SpeedRange, MuteWords
from promptcut.otio_export import export_otio, to_otio_dict
from tests.fixtures.single_speaker import make_single_speaker, WELCOME_CHANNEL_IDS


def test_to_otio_dict_structure():
    t = make_single_speaker()
    log = OperationLog()
    log = log.push(AddCaption(word_ids=WELCOME_CHANNEL_IDS, text="Welcome!", color="red"))
    log = log.push(CutRange(word_ids=list(range(10))))
    log = log.push(SpeedRange(word_ids=[20, 21, 22], factor=1.5))
    log = log.push(MuteWords(word_ids=[25, 26]))

    tl = apply(log, t)
    doc = to_otio_dict(tl, t, fps=24.0, project_name="Test Assessment Export")

    assert doc["OTIO_SCHEMA"] == "Timeline.1"
    assert doc["name"] == "Test Assessment Export"
    assert "tracks" in doc
    assert doc["tracks"]["OTIO_SCHEMA"] == "Stack.1"

    children = doc["tracks"]["children"]
    assert len(children) == 2  # Video Track and Audio Track

    video_track = children[0]
    assert video_track["kind"] == "Video"
    assert len(video_track["children"]) > 0
    assert len(video_track["markers"]) == 1

    marker = video_track["markers"][0]
    assert marker["name"] == "Welcome!"
    assert marker["color"] == "RED"
    assert marker["marked_range"]["OTIO_SCHEMA"] == "TimeRange.1"

    audio_track = children[1]
    assert audio_track["kind"] == "Audio"
    assert len(audio_track["children"]) > 0
    # One of the audio clips should be a Gap (muted)
    assert any(child["OTIO_SCHEMA"] == "Gap.1" for child in audio_track["children"])


def test_export_otio_file_writing(tmp_path: Path):
    t = make_single_speaker()
    log = OperationLog()
    log = log.push(AddCaption(word_ids=WELCOME_CHANNEL_IDS, text="Welcome!"))
    tl = apply(log, t)

    out_file = tmp_path / "exported_timeline.otio"
    res_path = export_otio(tl, t, out_file)

    assert res_path.exists()
    content = json.loads(res_path.read_text(encoding="utf-8"))
    assert content["OTIO_SCHEMA"] == "Timeline.1"
    assert content["metadata"]["promptcut"]["timeline_hash"] == tl.timeline_hash
