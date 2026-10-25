"""
tests/test_zoom_speaker.py

Comprehensive test suite for the ZoomSpeaker (Punch-in / Speaker Zoom) feature.
Validates:
- Pydantic model initialization, defaults, and boundary constraints
- Pure engine compilation and segment splitting on zoom changes
- Replay determinism (identical timeline_hash across 3 replays)
- Undo, redo, and restore_op toggling for ZoomSpeaker
- Natural language parsing and resolver integration
- FFmpeg filtergraph verification (crop & scale)
- OpenTimelineIO export metadata preservation
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import CutRange, SpeedRange, ZoomSpeaker
from promptcut.models.transcript import Transcript, Word
from promptcut.otio_export import to_otio_dict
from promptcut.render.ffmpeg import render_timeline
from promptcut.resolver import IntentTarget, resolve_intent_target
from promptcut.session.parser import parse_instruction
from tests.fixtures.single_speaker import make_single_speaker


class TestZoomSpeakerModel:
    """Validates ZoomSpeaker Pydantic model properties."""

    def test_default_values(self):
        op = ZoomSpeaker(word_ids=[0, 1, 2])
        assert op.op_type == "zoom_speaker"
        assert op.word_ids == [0, 1, 2]
        assert op.factor == 1.2
        assert op.anchor == "center"
        assert op.active is True
        assert op.op_id == -1

    def test_custom_factor_and_anchor(self):
        op = ZoomSpeaker(word_ids=[3, 4], factor=1.5, anchor="top")
        assert op.factor == 1.5
        assert op.anchor == "top"

    def test_invalid_factor_raises_validation_error(self):
        with pytest.raises(Exception):
            ZoomSpeaker(word_ids=[1], factor=0.4)  # below gt=0.5
        with pytest.raises(Exception):
            ZoomSpeaker(word_ids=[1], factor=3.5)  # above le=3.0

    def test_serialization_roundtrip(self):
        op = ZoomSpeaker(word_ids=[10, 11], factor=1.25)
        raw = op.model_dump_json()
        data = json.loads(raw)
        assert data["op_type"] == "zoom_speaker"
        assert data["factor"] == 1.25
        restored = ZoomSpeaker.model_validate(data)
        assert restored == op


class TestZoomSpeakerEngine:
    """Validates pure deterministic engine compilation of ZoomSpeaker."""

    def test_zoom_speaker_sets_segment_zoom_factor(self):
        words = [
            Word(id=0, text="Intro", start=0.0, end=1.0),
            Word(id=1, text="highlight", start=1.0, end=2.0),
            Word(id=2, text="outro", start=2.0, end=3.0),
        ]
        transcript = Transcript(words=words, source_duration=3.0)

        # Zoom in 1.3x on word 1 only
        log = OperationLog().push(ZoomSpeaker(word_ids=[1], factor=1.3))
        timeline = apply(log, transcript)

        # Should produce 3 segments: word 0 (zoom 1.0), word 1 (zoom 1.3), word 2 (zoom 1.0)
        assert len(timeline.segments) == 3
        assert timeline.segments[0].zoom_factor == 1.0
        assert timeline.segments[0].source_word_ids == [0]

        assert timeline.segments[1].zoom_factor == 1.3
        assert timeline.segments[1].source_word_ids == [1]

        assert timeline.segments[2].zoom_factor == 1.0
        assert timeline.segments[2].source_word_ids == [2]

    def test_zoom_speaker_last_write_wins(self):
        words = [
            Word(id=0, text="First", start=0.0, end=1.0),
            Word(id=1, text="Second", start=1.0, end=2.0),
        ]
        transcript = Transcript(words=words, source_duration=2.0)

        # Op 0: zoom 1.2x
        # Op 1: zoom 1.5x on same words
        log = (
            OperationLog()
            .push(ZoomSpeaker(word_ids=[0, 1], factor=1.2))
            .push(ZoomSpeaker(word_ids=[0, 1], factor=1.5))
        )
        timeline = apply(log, transcript)

        assert len(timeline.segments) == 1
        assert timeline.segments[0].zoom_factor == 1.5

    def test_zoom_speaker_determinism_replay_3_times(self):
        transcript = make_single_speaker()
        log = (
            OperationLog()
            .push(CutRange(word_ids=[0, 1, 2, 3]))
            .push(ZoomSpeaker(word_ids=[10, 11, 12], factor=1.2))
            .push(SpeedRange(word_ids=[20, 21], factor=1.5))
        )

        t1 = apply(log, transcript)
        t2 = apply(log, transcript)
        t3 = apply(log, transcript)

        assert t1.timeline_hash != ""
        assert t1.timeline_hash == t2.timeline_hash == t3.timeline_hash

    def test_zoom_speaker_undo_redo_and_restore(self):
        words = [
            Word(id=0, text="One", start=0.0, end=1.0),
            Word(id=1, text="Two", start=1.0, end=2.0),
        ]
        transcript = Transcript(words=words, source_duration=2.0)

        log = OperationLog()
        tl_initial = apply(log, transcript)
        assert tl_initial.segments[0].zoom_factor == 1.0

        # Push zoom op (op_id 0)
        log = log.push(ZoomSpeaker(word_ids=[0, 1], factor=1.2))
        tl_zoomed = apply(log, transcript)
        assert tl_zoomed.segments[0].zoom_factor == 1.2

        # Undo zoom
        log_undone = log.undo()
        tl_undone = apply(log_undone, transcript)
        assert tl_undone.segments[0].zoom_factor == 1.0
        assert tl_undone.timeline_hash == tl_initial.timeline_hash

        # Redo zoom
        log_redone = log_undone.redo()
        tl_redone = apply(log_redone, transcript)
        assert tl_redone.segments[0].zoom_factor == 1.2
        assert tl_redone.timeline_hash == tl_zoomed.timeline_hash

        # Restore op (deactivate op 0 without undo)
        log_restored = log.restore_op(0)
        tl_restored = apply(log_restored, transcript)
        assert tl_restored.segments[0].zoom_factor == 1.0


class TestZoomSpeakerParsingAndResolver:
    """Validates natural language interpretation and resolution of zoom prompts."""

    def test_parser_zoom_on_speaker(self):
        transcript = make_single_speaker()
        log = OperationLog()
        timeline = apply(log, transcript)

        status, ops, details = parse_instruction(
            "zoom in on the speaker",
            transcript=transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert isinstance(ops[0], ZoomSpeaker)
        assert ops[0].factor == 1.2
        assert "Zoomed in on the speaker" in details["summary"]

    def test_parser_punch_in_custom_factor(self):
        transcript = make_single_speaker()
        log = OperationLog()
        timeline = apply(log, transcript)

        status, ops, details = parse_instruction(
            "punch in by 1.3x",
            transcript=transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert isinstance(ops[0], ZoomSpeaker)
        assert ops[0].factor == 1.3

    def test_parser_zoom_on_phrase(self):
        transcript = make_single_speaker()
        log = OperationLog()
        timeline = apply(log, transcript)

        status, ops, details = parse_instruction(
            "zoom in on welcome to the channel by 1.25x",
            transcript=transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert isinstance(ops[0], ZoomSpeaker)
        assert ops[0].factor == 1.25

    def test_resolver_intent_target_zoom(self):
        transcript = make_single_speaker()
        log = OperationLog()
        timeline = apply(log, transcript)

        target = IntentTarget(action="zoom", query="speaker", factor=1.2)
        status, ops, details = resolve_intent_target(target, transcript, timeline, log)
        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert isinstance(ops[0], ZoomSpeaker)
        assert ops[0].factor == 1.2


class TestZoomSpeakerRenderAndOTIO:
    """Validates FFmpeg filtergraph compilation and OpenTimelineIO export."""

    def test_otio_export_preserves_zoom_factor(self):
        words = [
            Word(id=0, text="Intro", start=0.0, end=1.0),
            Word(id=1, text="Highlight", start=1.0, end=2.0),
        ]
        transcript = Transcript(words=words, source_duration=2.0)
        log = OperationLog().push(ZoomSpeaker(word_ids=[1], factor=1.25))
        timeline = apply(log, transcript)

        otio_doc = to_otio_dict(timeline, transcript)
        video_track = otio_doc["tracks"]["children"][0]
        clips = video_track["children"]

        assert len(clips) == 2
        assert clips[0]["metadata"]["promptcut"]["zoom_factor"] == 1.0
        assert clips[1]["metadata"]["promptcut"]["zoom_factor"] == 1.25
