"""
tests/test_caption_and_word_edit.py

Tests for caption movement (middle/center/top/bottom), font size increase/decrease,
font style (italic/bold), custom caption text editing, word pronunciation correction,
and the PUT /transcript/words/{word_id} API endpoint.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from promptcut.api.app import app
from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import AddCaption, SetCaptionStyle
from promptcut.models.transcript import Transcript, Word
from promptcut.session.parser import parse_instruction


@pytest.fixture
def sample_transcript() -> Transcript:
    return Transcript(
        words=[
            Word(id=0, text="Welcome", start=0.0, end=0.6),
            Word(id=1, text="to", start=0.6, end=1.0),
            Word(id=2, text="the", start=1.0, end=1.4),
            Word(id=3, text="channel", start=1.4, end=2.4),
            Word(id=4, text="pricing", start=2.4, end=3.4),
        ],
        source_duration=4.0,
    )


class TestCaptionMovementAndStylingParser:
    """Tests natural language parsing of caption movement, font size, and font style."""

    def test_move_caption_to_middle(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, details = parse_instruction(
            say="move the caption to the middle",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )

        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert isinstance(ops[0], SetCaptionStyle)
        assert ops[0].position == "middle"
        assert "middle" in details["summary"].lower()

    def test_move_caption_to_center(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, details = parse_instruction(
            say="move caption to center",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )

        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert ops[0].position == "middle"

    def test_center_the_caption(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, details = parse_instruction(
            say="center the caption",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )

        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert ops[0].position == "middle"

    def test_move_caption_to_top_and_bottom(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, _ = parse_instruction(
            say="move caption to top",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].position == "top"

        status, ops, _ = parse_instruction(
            say="position caption at bottom",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].position == "bottom"

    def test_font_size_increase_decrease(self, sample_transcript: Transcript):
        log = OperationLog().push(AddCaption(word_ids=[0], text="Welcome", font_size=32))
        timeline = apply(log, sample_transcript)

        # Increase
        status, ops, details = parse_instruction(
            say="increase font size",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].font_size == 40
        assert "increased" in details["summary"].lower()

        # Decrease
        status, ops, details = parse_instruction(
            say="decrease font size",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].font_size == 24
        assert "decreased" in details["summary"].lower()

    def test_set_explicit_font_size(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, _ = parse_instruction(
            say="set caption font size to 48",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].font_size == 48

    def test_font_style_italic_and_bold(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, _ = parse_instruction(
            say="make caption italic",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].font_style == "italic"

        status, ops, _ = parse_instruction(
            say="make captions bold",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].font_style == "bold"

    def test_set_custom_caption_text(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, details = parse_instruction(
            say="change caption to 'Awesome Tutorial'",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].text == "Awesome Tutorial"
        assert "Awesome Tutorial" in details["summary"]

    def test_add_any_custom_caption(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        # Custom caption without anchor anchors to start of video
        status, ops, details = parse_instruction(
            say="add caption 'Welcome Everyone to Project 2!'",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops[0].text == "Welcome Everyone to Project 2!"
        assert len(ops[0].word_ids) >= 1

    def test_correct_word_pronunciation_via_prompt(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, details = parse_instruction(
            say="correct word 'pricing' to 'billing'",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "correct_word"
        assert details["word_id"] == 4
        assert details["new_text"] == "billing"

    def test_caption_whole_video(self, sample_transcript: Transcript):
        log = OperationLog()
        timeline = apply(log, sample_transcript)

        status, ops, details = parse_instruction(
            say="add caption to the whole video",
            transcript=sample_transcript,
            log=log,
            timeline=timeline,
        )
        assert status == "applied"
        assert ops is not None
        assert len(ops) >= 1
        assert "whole video" in details["summary"].lower()

        # Apply ops to log and verify timeline has timed captions covering all words
        for op in ops:
            log = log.push(op)
        timeline2 = apply(log, sample_transcript)
        assert len(timeline2.captions) == len(ops)

        # Verify moving all captions to the middle works across all captions
        status_move, ops_move, _ = parse_instruction(
            say="move the caption to the middle",
            transcript=sample_transcript,
            log=log,
            timeline=timeline2,
        )
        assert status_move == "applied"
        for op in ops_move:
            log = log.push(op)
        timeline3 = apply(log, sample_transcript)
        for cap in timeline3.captions:
            assert cap.position == "middle"


class TestTranscriptWordEditAPI:
    """Tests the PUT /sessions/{id}/transcript/words/{id} API endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(app)

    def test_sample_session_word_edit(self, client: TestClient):
        # 1. Create sample session
        res = client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # 2. Check initial transcript word 0
        t_res = client.get(f"/api/v1/sessions/{session_id}/transcript")
        assert t_res.status_code == 200
        words = t_res.json()["words"]
        assert words[0]["text"] == "Welcome"

        # 3. Edit word 0 (e.g. fix pronunciation error to "Greetings")
        put_res = client.put(
            f"/api/v1/sessions/{session_id}/transcript/words/0",
            json={"text": "Greetings"},
        )
        assert put_res.status_code == 200
        data = put_res.json()
        assert data["status"] == "success"
        assert data["word_id"] == 0
        assert data["old_text"] == "Welcome"
        assert data["new_text"] == "Greetings"
        assert "timeline_hash" in data

        # 4. Verify transcript has updated word
        t_res2 = client.get(f"/api/v1/sessions/{session_id}/transcript")
        words2 = t_res2.json()["words"]
        assert words2[0]["text"] == "Greetings"


class TestSpeakerConditionedBleep:
    """Tests speaker-conditioned bleep commands such as 'bleep every time vanessa says and'."""

    def test_bleep_every_time_speaker_says_word(self):
        transcript = Transcript(
            words=[
                Word(id=0, text="Hi", start=0.0, end=0.4, speaker="Dan"),
                Word(id=1, text="and", start=0.4, end=0.7, speaker="Dan"),
                Word(id=2, text="welcome", start=0.7, end=1.2, speaker="Dan"),
                Word(id=3, text="Hello", start=1.5, end=1.9, speaker="Vanessa"),
                Word(id=4, text="and", start=1.9, end=2.2, speaker="Vanessa"),
                Word(id=5, text="thanks", start=2.2, end=2.7, speaker="Vanessa"),
            ],
            source_duration=3.0,
        )
        log = OperationLog()
        timeline = apply(log, transcript)

        status, ops, details = parse_instruction(
            say="bleep every time vanessa says and",
            transcript=transcript,
            log=log,
            timeline=timeline,
        )

        assert status == "applied"
        assert ops is not None
        from promptcut.models.operations import BleepWords
        bleep_op = next(op for op in ops if isinstance(op, BleepWords))
        # Word 4 is Vanessa's 'and', word 1 is Dan's 'and'
        assert bleep_op.word_ids == [4]
        assert "Vanessa" in details["summary"]
        assert "and" in details["summary"]

