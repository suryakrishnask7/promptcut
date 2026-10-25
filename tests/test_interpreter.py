"""
Tests for Interpreter Layer, Resolver Tolerance, Tie-Breakers, Clarification Cycles,
and Phrasing Equivalence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from promptcut.config import TIME_HINT_TOLERANCE_SECS
from promptcut.engine import apply
from promptcut.interpreter import (
    GroqInterpreter,
    IntentTarget,
    InterpretationResult,
    InterpreterContext,
    MockInterpreter,
    RuleInterpreter,
)
from promptcut.models.operation_log import OperationLog
from promptcut.models.transcript import Transcript, Word
from promptcut.resolver import resolve_intent_target, resolve_interpretation_result


@pytest.fixture
def nexus_transcript() -> Transcript:
    """
    Transcript with two distinct occurrences of 'nexus':
    Turn 1: word 6 "nexus" at 2.67s -> 3.10s
    Turn 2: word 7 "nexus" at 3.40s -> 3.90s
    """
    words = [
        Word(id=0, text="all", start=0.18, end=0.40),
        Word(id=1, text="right", start=0.40, end=0.70),
        Word(id=2, text="ladies", start=0.70, end=1.10),
        Word(id=3, text="and", start=1.10, end=1.30),
        Word(id=4, text="gentlemen", start=1.30, end=1.90),
        Word(id=5, text="welcome", start=1.90, end=2.30),
        Word(id=6, text="to", start=2.30, end=2.50),
        Word(id=7, text="nexus", start=2.67, end=3.10, sentence_end=True),
        Word(id=8, text="nexus", start=3.40, end=3.90),
        Word(id=9, text="is", start=3.90, end=4.10),
        Word(id=10, text="the", start=4.10, end=4.30),
        Word(id=11, text="link", start=4.30, end=4.80, sentence_end=True),
    ]
    return Transcript(words=words, title="Nexus Clip", source_duration=5.0)


@pytest.fixture
def tie_transcript() -> Transcript:
    """
    Transcript designed specifically for the tolerance tie-breaker:
    Two occurrences of 'target':
    - Occurrence 1 (word 1) at 1.0s (distance to 2.0s is 1.0s)
    - Occurrence 2 (word 3) at 3.0s (distance to 2.0s is 1.0s)
    """
    words = [
        Word(id=0, text="start", start=0.0, end=0.8),
        Word(id=1, text="target", start=1.0, end=1.6),
        Word(id=2, text="middle", start=1.8, end=2.8),
        Word(id=3, text="target", start=3.0, end=3.6),
        Word(id=4, text="end", start=3.8, end=4.5),
    ]
    return Transcript(words=words, title="Tie Clip", source_duration=5.0)


# ---------------------------------------------------------------------------
# Test 1: "there are two nexus 2.67 remove one of them" cuts word nearest 2.67
# ---------------------------------------------------------------------------


def test_remove_nexus_with_time_hint(nexus_transcript: Transcript):
    log = OperationLog()
    timeline = apply(log, nexus_transcript)
    interpreter = MockInterpreter()

    result = interpreter.interpret(
        "there are two nexus 2.67 remove one of them",
        InterpreterContext(duration=timeline.total_duration),
    )
    assert result.status == "applied"
    assert len(result.targets) == 1
    target = result.targets[0]
    assert target.action == "cut"
    assert target.query == "nexus"
    assert target.time_hint == 2.67

    # Resolve target against timeline
    status, ops, details = resolve_intent_target(target, nexus_transcript, timeline, log)
    assert status == "applied"
    assert ops is not None and len(ops) == 1
    # Word 7 is the first 'nexus' at 2.67s
    assert ops[0].word_ids == [7]

    # Apply operation
    new_log = log.push(ops[0])
    new_timeline = apply(new_log, nexus_transcript)
    # Word 7 is removed, but word 8 ("nexus" at 3.40s) remains
    surviving_words = [
        w.text for seg in new_timeline.segments for w in nexus_transcript.words if w.id in seg.source_word_ids
    ]
    assert surviving_words.count("nexus") == 1
    assert 7 not in [w for seg in new_timeline.segments for w in seg.source_word_ids]
    assert 8 in [w for seg in new_timeline.segments for w in seg.source_word_ids]


# ---------------------------------------------------------------------------
# Test 2: Ambiguous destructive edit without hint -> NeedsClarification
# ---------------------------------------------------------------------------


def test_remove_nexus_ambiguous_without_time_hint(nexus_transcript: Transcript):
    log = OperationLog()
    timeline = apply(log, nexus_transcript)
    interpreter = MockInterpreter()

    result = interpreter.interpret(
        "remove nexus",
        InterpreterContext(duration=timeline.total_duration),
    )
    assert result.status == "applied"
    target = result.targets[0]
    assert target.query == "nexus"
    assert target.time_hint is None

    # Resolver should detect multiple hits without time hint -> NeedsClarification
    status, ops, details = resolve_intent_target(target, nexus_transcript, timeline, log)
    assert status == "needs_clarification"
    assert "Nexus" in details["question"]
    assert len(details["options"]) >= 2
    assert "candidate_hits" in details
    assert len(details["candidate_hits"]) == 2


# ---------------------------------------------------------------------------
# Test 3: "the second one" completes the pending clarification
# ---------------------------------------------------------------------------


def test_clarification_cycle_second_one(nexus_transcript: Transcript):
    log = OperationLog()
    timeline = apply(log, nexus_transcript)
    interpreter = MockInterpreter()

    # Step 1: Initial ambiguous instruction
    res1 = interpreter.interpret("remove nexus", InterpreterContext(duration=timeline.total_duration))
    status1, ops1, det1 = resolve_intent_target(res1.targets[0], nexus_transcript, timeline, log)
    assert status1 == "needs_clarification"

    pending_state = {
        "action": "cut",
        "phrase": "nexus",
        "candidate_hits": det1["candidate_hits"],
        "question": det1["question"],
        "options": det1["options"],
    }

    # Step 2: User answers "the second one"
    res2 = interpreter.interpret(
        "the second one",
        InterpreterContext(duration=timeline.total_duration, pending_clarification=pending_state),
    )
    assert res2.status == "applied"
    assert res2.targets[0].action == "clarification_answer"
    assert res2.targets[0].occurrence == 2

    status2, ops2, det2 = resolve_intent_target(
        res2.targets[0],
        nexus_transcript,
        timeline,
        log,
        pending_clarification=pending_state,
    )
    assert status2 == "applied"
    assert ops2 is not None and len(ops2) == 1
    # Word 8 is the second 'nexus'
    assert ops2[0].word_ids == [8]

    # Apply to timeline
    new_log = log.push(ops2[0])
    new_timeline = apply(new_log, nexus_transcript)
    # Word 7 remains, word 8 is cut
    surviving_ids = [wid for seg in new_timeline.segments for wid in seg.source_word_ids]
    assert 7 in surviving_ids
    assert 8 not in surviving_ids


# ---------------------------------------------------------------------------
# Test 4: Unrelated message arriving while clarification is pending
# ---------------------------------------------------------------------------


def test_unrelated_message_clears_pending_clarification(nexus_transcript: Transcript):
    log = OperationLog()
    timeline = apply(log, nexus_transcript)
    interpreter = MockInterpreter()

    # Ambiguous clarification pending
    pending_state = {
        "action": "cut",
        "phrase": "nexus",
        "candidate_hits": [[7], [8]],
        "question": "Which nexus?",
        "options": ["Occurrence 1", "Occurrence 2"],
    }

    # User sends completely unrelated command: "remove fillers"
    unrelated_prompt = "remove fillers"
    res = interpreter.interpret(
        unrelated_prompt,
        InterpreterContext(duration=timeline.total_duration, pending_clarification=pending_state),
    )
    # The interpreter recognizes "remove fillers" as an action, not an answer to "Which nexus?"
    assert res.targets[0].action == "remove_fillers"

    # In session flow: because it's not a clarification_answer, pending_clarification is cleared
    # and the new op is applied cleanly
    status, ops, det = resolve_intent_target(
        res.targets[0],
        nexus_transcript,
        timeline,
        log,
        pending_clarification=None,  # cleared!
    )
    assert status == "applied"
    assert ops[0].op_type == "remove_fillers"


# ---------------------------------------------------------------------------
# Test 5: Tolerance Constant & Deterministic Tie-Breaker Test
# ---------------------------------------------------------------------------


def test_tolerance_tie_breaker(tie_transcript: Transcript):
    """
    Documented in RULES.md (§IntentResolutionTolerance):
    Occurrence 1 is at 1.0s.
    Occurrence 2 is at 3.0s.
    User specifies time_hint = 2.0s.
    Distance to both is exactly 1.0s (|1.0 - 2.0| == |3.0 - 2.0| == 1.0 <= 2.0s).
    Deterministic tie-breaker: Occurrence 1 (earlier in timeline order) MUST win!
    """
    assert TIME_HINT_TOLERANCE_SECS == 2.0
    log = OperationLog()
    timeline = apply(log, tie_transcript)

    target = IntentTarget(
        action="cut",
        query="target",
        time_hint=2.0,
        time_basis="current",
    )

    status, ops, det = resolve_intent_target(target, tie_transcript, timeline, log)
    assert status == "applied"
    assert ops is not None and len(ops) == 1
    # Word 1 (the earlier occurrence at 1.0s) MUST be selected over Word 3 (at 3.0s)
    assert ops[0].word_ids == [1]


# ---------------------------------------------------------------------------
# Test 6: 10 Phrasings of One Edit Reach the Exact Same State
# ---------------------------------------------------------------------------


def test_ten_phrasings_equivalence(nexus_transcript: Transcript):
    """
    10 different phrasings of removing 'nexus' at 2.67s must all produce
    the identical timeline state (identical timeline_hash).
    """
    phrasings = [
        "there are two nexus 2.67 remove one of them",
        "remove the nexus at 2.67",
        "cut nexus near 2.67s",
        "delete nexus around 2.67",
        "remove nexus at 2.67",
        "cut the first nexus at 2.67",
        "drop nexus at 2.67s",
        "take out nexus 2.67",
        "remove nexus timestamp 2.67",
        "cut occurrence of nexus at 2.67",
    ]

    hashes: set[str] = set()
    interpreter = MockInterpreter()

    for phrase in phrasings:
        log = OperationLog()
        timeline = apply(log, nexus_transcript)
        res = interpreter.interpret(phrase, InterpreterContext(duration=timeline.total_duration))
        assert res.status == "applied"
        status, ops, det = resolve_intent_target(res.targets[0], nexus_transcript, timeline, log)
        assert status == "applied"
        assert ops is not None and len(ops) == 1
        assert ops[0].word_ids == [7]

        new_log = log.push(ops[0])
        new_timeline = apply(new_log, nexus_transcript)
        hashes.add(new_timeline.timeline_hash)

    # All 10 phrasings must produce exactly 1 identical timeline hash!
    assert len(hashes) == 1


# ---------------------------------------------------------------------------
# Test 7: Disk Caching and Cost Tracking
# ---------------------------------------------------------------------------


def test_groq_caching_and_cost_tracking(tmp_path: Path):
    """
    Verifies disk caching on sha256(instruction + canonical_context) and cost metrics.
    """
    groq = GroqInterpreter(api_key=None, model="openai/gpt-oss-120b", cache_enabled=True)
    # When api_key is None, GroqInterpreter falls back gracefully to RuleInterpreter
    ctx = InterpreterContext(duration=10.0, time_policy="current")
    res1 = groq.interpret("undo", ctx)
    assert res1.status == "undo"

    # Direct cache test
    cache_key = groq._compute_cache_key("test instruction", ctx)
    sample_result = InterpretationResult(
        status="applied",
        targets=[IntentTarget(action="cut", query="welcome")],
        prompt_tokens=100,
        completion_tokens=50,
        tokens_used=150,
        cost_usd=0.000045,
        model_name="openai/gpt-oss-120b",
    )
    groq._write_cache(cache_key, sample_result)
    cached = groq._read_cache(cache_key)
    assert cached is not None
    assert cached.cached is True
    assert cached.status == "applied"
    assert cached.targets[0].query == "welcome"
    assert cached.cost_usd == 0.000045


# ---------------------------------------------------------------------------
# Test 8: Groq Llama Model Configuration and .env Loading
# ---------------------------------------------------------------------------


def test_groq_llama_model_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Verifies that GroqInterpreter picks up the Llama model configured via GROQ_MODEL or env.
    """
    from promptcut.config import load_env_file

    fake_env = tmp_path / ".env"
    fake_env.write_text("GROQ_MODEL=llama-3.3-70b-versatile\nGROQ_API_KEY=test_key_dummy\n", encoding="utf-8")

    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    load_env_file(fake_env)

    groq = GroqInterpreter(cache_enabled=False)
    assert groq.model == "llama-3.3-70b-versatile"
    assert groq.api_key == "test_key_dummy"

