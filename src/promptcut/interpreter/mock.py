"""
MockInterpreter: Fixture and heuristic mock adapter for test suites and benchmark validation.

Provides deterministic, reproducible interpretation without external network calls or API keys.
"""

from __future__ import annotations

import re
import time
from typing import Any

from promptcut.interpreter.base import (
    IntentTarget,
    InterpretationResult,
    InterpreterAdapter,
    InterpreterContext,
)
from promptcut.interpreter.rule import RuleInterpreter


class MockInterpreter(InterpreterAdapter):
    """
    Deterministic mock interpreter for test environments.
    """

    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        self.fixtures = fixtures or {}
        self.rule_fallback = RuleInterpreter()

    def register_fixture(self, prompt: str, result: InterpretationResult | dict[str, Any]) -> None:
        if isinstance(result, InterpretationResult):
            self.fixtures[prompt.strip().lower()] = result.model_dump()
        else:
            self.fixtures[prompt.strip().lower()] = result

    def interpret(self, text: str, context: InterpreterContext) -> InterpretationResult:
        start_t = time.perf_counter()
        raw = text.strip()
        lower = raw.lower()

        # 1. Check explicit registered fixtures
        if lower in self.fixtures:
            data = self.fixtures[lower]
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            data_copy = dict(data)
            data_copy["latency_ms"] = dur_ms
            data_copy["model_name"] = "mock"
            return InterpretationResult.model_validate(data_copy)

        # 2. Check pending clarification answers
        if context.pending_clarification is not None:
            if "second" in lower or lower == "2":
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[IntentTarget(action="clarification_answer", occurrence=2)],
                    latency_ms=dur_ms,
                    model_name="mock",
                )
            if "first" in lower or lower == "1":
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[IntentTarget(action="clarification_answer", occurrence=1)],
                    latency_ms=dur_ms,
                    model_name="mock",
                )
            if "all" in lower:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[IntentTarget(action="clarification_answer", occurrence="all")],
                    latency_ms=dur_ms,
                    model_name="mock",
                )

        # 3. Rule fallback (undo, redo, render, explicit ranges)
        rule_res = self.rule_fallback.interpret(raw, context)
        if rule_res.status != "rejected":
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            rule_res.latency_ms = dur_ms
            rule_res.model_name = "mock"
            return rule_res

        # 3b. Fillers & Silences
        if "filler" in lower:
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="applied",
                targets=[IntentTarget(action="remove_fillers")],
                latency_ms=dur_ms,
                model_name="mock",
                interpreted_by="rule",
            )

        if "silence" in lower:
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="applied",
                targets=[IntentTarget(action="remove_silences")],
                latency_ms=dur_ms,
                model_name="mock",
                interpreted_by="rule",
            )

        # 4. Should-reject cases (completely out of scope)
        if any(w in lower for w in ("order pizza", "download subtitles", "hack", "send email", "play music")):
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="rejected",
                rejection_reason="Command out of scope for video editor.",
                latency_ms=dur_ms,
                model_name="mock",
            )

        # 5. Vague instructions
        if lower in ("make it better", "edit video", "fix everything", "improve video", "do something"):
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="needs_clarification",
                clarification_question="Your instruction is vague. What specific edit would you like to make?",
                clarification_options=["Cut intro", "Remove fillers", "Add captions"],
                latency_ms=dur_ms,
                model_name="mock",
            )

        # 6. Impossible cases: e.g. "cut at 99:99", "speed up 100:00 to 110:00"
        m_imp = re.search(r"(?:cut|speed|remove)\s+(?:at|from)?\s*(\d{2,}:\d{2,})", lower)
        if m_imp:
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="applied",
                targets=[IntentTarget(action="cut", start_time=5999.0, end_time=6000.0)],
                latency_ms=dur_ms,
                model_name="mock",
            )

        # 7. Pattern matching for phrase cut with time hints
        # e.g. "there are two nexus 2.67 remove one of them", "remove nexus near 2.67"
        m_hint = re.search(r"(?:nexus|pricing|word|phrase).*?(\d+(?:\.\d+)?)", lower)
        if not m_hint:
            m_hint = re.search(r"(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?.*?(?:nexus|pricing|word|phrase)", lower)

        time_val = float(m_hint.group(1)) if m_hint else None

        # Check for phrase cuts
        if any(w in lower for w in ("remove", "cut", "delete", "drop", "take out")):
            query = None
            if "nexus" in lower:
                query = "nexus"
            elif "pricing" in lower:
                query = "pricing"
            elif "welcome" in lower:
                query = "welcome"
            else:
                m_phrase = re.search(r"(?:remove|cut|delete|drop|take out)\s+(?:the\s+)?([a-zA-Z0-9_\-]+)", lower)
                if m_phrase:
                    query = m_phrase.group(1).strip()

            if query:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[
                        IntentTarget(
                            action="cut",
                            query=query,
                            time_hint=time_val,
                            time_basis="current",
                        )
                    ],
                    latency_ms=dur_ms,
                    model_name="mock",
                )

        # 8. Add caption
        m_cap = re.search(r"(?:add\s+)?(?:a\s+)?caption\s+['\"]([^'\"]+)['\"](?:\s+(?:when\s+i\s+say|to|at)\s+(.+))?", raw, re.IGNORECASE)
        if m_cap:
            text_cap = m_cap.group(1)
            target_q = m_cap.group(2)
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="applied",
                targets=[
                    IntentTarget(
                        action="caption",
                        text=text_cap,
                        query=target_q.strip() if target_q else None,
                        time_hint=time_val,
                    )
                ],
                latency_ms=dur_ms,
                model_name="mock",
            )

        # Fallback rejection
        dur_ms = (time.perf_counter() - start_t) * 1000.0
        return InterpretationResult(
            status="rejected",
            rejection_reason=f"MockInterpreter could not interpret instruction: '{raw}'",
            latency_ms=dur_ms,
            model_name="mock",
        )
