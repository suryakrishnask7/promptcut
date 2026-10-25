"""
tests/test_caption_masking.py

Tests for default caption masking when bleeping words, dynamic unmasking and remasking
via operations and natural language prompts, undo/redo determinism, and ASS subtitle generation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from promptcut.api.app import app
from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import (
    AddCaption,
    BleepWords,
    MaskCaptions,
    UnbleepWords,
    UnmaskCaptions,
)
from promptcut.models.timeline import mask_word_text
from promptcut.models.transcript import Transcript, Word
from promptcut.render.ass import generate_ass_subtitles


@pytest.fixture
def sample_transcript() -> Transcript:
    return Transcript(
        words=[
            Word(id=0, text="Welcome", start=0.0, end=0.6),
            Word(id=1, text="wonder", start=0.6, end=1.2),
            Word(id=2, text="world", start=1.2, end=1.8),
            Word(id=3, text="today", start=1.8, end=2.4),
        ],
        source_duration=3.0,
    )


class TestMaskWordHelper:
    def test_replaces_letters_with_asterisks(self):
        assert mask_word_text("wonder") == "******"
        assert mask_word_text("it") == "**"
        assert mask_word_text("a") == "*"

    def test_preserves_punctuation(self):
        assert mask_word_text("wonder,") == "******,"
        assert mask_word_text("hello!") == "*****!"
        assert mask_word_text('"quote"') == '"*****"'
        assert mask_word_text("what-ever") == "****-****"

    def test_empty_or_whitespace(self):
        assert mask_word_text("") == ""
        assert mask_word_text("   ") == "   "


class TestEngineCaptionMasking:
    def test_bleep_by_default_masks_captions(self, sample_transcript: Transcript):
        log = OperationLog()
        # Add caption covering word 1 ("wonder")
        log = log.push(AddCaption(word_ids=[1], text="wonder"))
        # Bleep word 1
        log = log.push(BleepWords(word_ids=[1]))

        timeline = apply(log, sample_transcript)

        # Word 1 must be in masked_word_ids
        assert timeline.masked_word_ids == [1]
        # Caption text must have 6 asterisks corresponding to 6 letters
        assert len(timeline.captions) == 1
        assert timeline.captions[0].text == "******"

    def test_unmask_captions_reveals_original(self, sample_transcript: Transcript):
        log = OperationLog()
        log = log.push(AddCaption(word_ids=[1], text="wonder"))
        log = log.push(BleepWords(word_ids=[1]))
        log = log.push(UnmaskCaptions(word_ids=[1]))

        timeline = apply(log, sample_transcript)

        assert timeline.masked_word_ids == []
        assert timeline.captions[0].text == "wonder"

    def test_unmask_all_and_remask(self, sample_transcript: Transcript):
        log = OperationLog()
        log = log.push(AddCaption(word_ids=[1], text="wonder"))
        log = log.push(AddCaption(word_ids=[2], text="world"))
        log = log.push(BleepWords(word_ids=[1, 2]))

        tl1 = apply(log, sample_transcript)
        assert set(tl1.masked_word_ids) == {1, 2}
        assert tl1.captions[0].text == "******"
        assert tl1.captions[1].text == "*****"

        # Unmask all
        log = log.push(UnmaskCaptions(word_ids=None))
        tl2 = apply(log, sample_transcript)
        assert tl2.masked_word_ids == []
        assert tl2.captions[0].text == "wonder"
        assert tl2.captions[1].text == "world"

        # Remask all
        log = log.push(MaskCaptions(word_ids=None))
        tl3 = apply(log, sample_transcript)
        assert set(tl3.masked_word_ids) == {1, 2}
        assert tl3.captions[0].text == "******"
        assert tl3.captions[1].text == "*****"

    def test_undo_redo_preserves_masking_state(self, sample_transcript: Transcript):
        log = OperationLog()
        log = log.push(AddCaption(word_ids=[1], text="wonder"))
        log = log.push(BleepWords(word_ids=[1]))
        log = log.push(UnmaskCaptions(word_ids=[1]))

        # State before undo: unmasked
        tl = apply(log, sample_transcript)
        assert tl.masked_word_ids == []

        # Undo unmask -> back to masked
        log = log.undo()
        tl_undone = apply(log, sample_transcript)
        assert tl_undone.masked_word_ids == [1]
        assert tl_undone.captions[0].text == "******"

        # Redo unmask -> unmasked again
        log = log.redo()
        tl_redone = apply(log, sample_transcript)
        assert tl_redone.masked_word_ids == []
        assert tl_redone.captions[0].text == "wonder"


class TestASSCaptionMasking:
    def test_ass_renders_asterisks_for_bleeped_words(self, sample_transcript: Transcript, tmp_path):
        log = OperationLog()
        log = log.push(AddCaption(word_ids=[0, 1, 2], text="Welcome wonder world"))
        log = log.push(BleepWords(word_ids=[1]))

        timeline = apply(log, sample_transcript)
        ass_file = tmp_path / "test_masked.ass"
        generate_ass_subtitles(timeline.captions, ass_file, transcript=sample_transcript, timeline=timeline)
        ass_content = ass_file.read_text(encoding="utf-8")

        # Word 1 "wonder" should be rendered as "******" in ASS dialogue
        assert "******" in ass_content
        # Non-bleeped words should remain intact (in uppercase)
        assert "WELCOME" in ass_content
        assert "WORLD" in ass_content

    def test_ass_renders_original_after_unmask(self, sample_transcript: Transcript, tmp_path):
        log = OperationLog()
        log = log.push(AddCaption(word_ids=[0, 1, 2], text="Welcome wonder world"))
        log = log.push(BleepWords(word_ids=[1]))
        log = log.push(UnmaskCaptions())

        timeline = apply(log, sample_transcript)
        ass_file = tmp_path / "test_unmasked.ass"
        generate_ass_subtitles(timeline.captions, ass_file, transcript=sample_transcript, timeline=timeline)
        ass_content = ass_file.read_text(encoding="utf-8")

        # Should render WONDER instead of ******
        assert "WONDER" in ass_content
        assert "******" not in ass_content


class TestApiCaptionMaskingRoutes:
    def test_unmask_and_remask_via_instructions(self, sample_transcript: Transcript):
        client = TestClient(app)
        # Create session with transcript
        res = client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # First add caption to whole video
        res_cap = client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "add caption to the whole video"},
        )
        assert res_cap.status_code == 200

        # Bleep a word (e.g. "welcome")
        res_bleep = client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "bleep the word welcome"},
        )
        assert res_bleep.status_code == 200

        # Timeline must have masked word ids
        res_tl = client.get(f"/api/v1/sessions/{session_id}/timeline")
        tl = res_tl.json()
        assert len(tl["masked_word_ids"]) > 0

        # Unmask captions
        res_unmask = client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "unmask captions"},
        )
        assert res_unmask.status_code == 200
        assert res_unmask.json()["status"] == "applied"

        res_tl2 = client.get(f"/api/v1/sessions/{session_id}/timeline")
        tl2 = res_tl2.json()
        assert len(tl2["masked_word_ids"]) == 0

        # Remask captions
        res_remask = client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "remask captions"},
        )
        assert res_remask.status_code == 200
        assert res_remask.json()["status"] == "applied"

        res_tl3 = client.get(f"/api/v1/sessions/{session_id}/timeline")
        tl3 = res_tl3.json()
        assert len(tl3["masked_word_ids"]) > 0
