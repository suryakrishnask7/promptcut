"""
RuleInterpreter: Restricted deterministic rule-based instruction interpreter.

Adheres strictly to the explicit allowlist:
- undo
- redo
- render
- fully explicit time forms (e.g. "cut 0:30-0:40", "cut from 0:10 to 0:20", "cut first 2s", "speed up from 1:00 to 1:30 by 2x")

Everything else is rejected by RuleInterpreter and routes to the LLM.
"""

from __future__ import annotations

import re
import time

from promptcut.interpreter.base import (
    IntentTarget,
    InterpretationResult,
    InterpreterAdapter,
    InterpreterContext,
)


class RuleInterpreter(InterpreterAdapter):
    """
    Local rule-based interpreter restricted to an explicit allowlist.
    """

    def interpret(self, text: str, context: InterpreterContext) -> InterpretationResult:
        from promptcut.resolver import parse_time_expression

        start_t = time.perf_counter()
        raw = text.strip()
        lower = raw.lower()

        # 1. Undo
        if lower in ("undo", "revert", "undo last"):
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="undo",
                targets=[IntentTarget(action="undo")],
                latency_ms=dur_ms,
                model_name="rule",
                interpreted_by="rule",
            )

        # 2. Redo
        if lower in ("redo", "redo last"):
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="redo",
                targets=[IntentTarget(action="redo")],
                latency_ms=dur_ms,
                model_name="rule",
                interpreted_by="rule",
            )

        # 3. Render
        if lower in ("render", "export", "render video"):
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="render",
                targets=[IntentTarget(action="render")],
                latency_ms=dur_ms,
                model_name="rule",
                interpreted_by="rule",
            )

        # 4. Fully explicit time forms:
        # 4a. Hyphenated range: "cut 0:30-0:40", "cut 0:30 - 0:40", "cut 10-20", "remove 10s-20s"
        m_hyphen = re.search(
            r"^(?:cut|remove)\s+([0-9:]+(?:\.\d+)?s?)\s*[-–—]\s*([0-9:]+(?:\.\d+)?s?)$",
            lower,
        )
        if m_hyphen:
            try:
                t1 = parse_time_expression(m_hyphen.group(1))
                t2 = parse_time_expression(m_hyphen.group(2))
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[IntentTarget(action="cut", start_time=t1, end_time=t2)],
                    latency_ms=dur_ms,
                    model_name="rule",
                    interpreted_by="rule",
                )
            except Exception:
                pass

        # 4b. Range with from/to or between/and: "cut from 0:10 to 0:20", "cut between 10s and 20s"
        m_range = re.search(
            r"^(?:cut|remove)\s+(?:from|between)\s+([0-9:]+(?:\.\d+)?s?)\s+(?:to|and)\s+([0-9:]+(?:\.\d+)?s?)$",
            lower,
        )
        if m_range:
            try:
                t1 = parse_time_expression(m_range.group(1))
                t2 = parse_time_expression(m_range.group(2))
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[IntentTarget(action="cut", start_time=t1, end_time=t2)],
                    latency_ms=dur_ms,
                    model_name="rule",
                    interpreted_by="rule",
                )
            except Exception:
                pass

        # 4c. Cut first N seconds: "cut first 2s", "cut the first 2 seconds", "remove first 10s"
        m_first = re.search(
            r"^(?:cut|remove)\s+(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?$",
            lower,
        )
        if m_first:
            secs = float(m_first.group(1))
            dur_ms = (time.perf_counter() - start_t) * 1000.0
            return InterpretationResult(
                status="applied",
                targets=[IntentTarget(action="cut", start_time=0.0, end_time=secs)],
                latency_ms=dur_ms,
                model_name="rule",
                interpreted_by="rule",
            )

        # 4d. Explicit speed range: "speed up from 1:00 to 1:30 by 2x", "speed up 1:00-1:30 by 2x"
        m_speed = re.search(
            r"^speed\s+up\s+(?:from\s+([0-9:]+(?:\.\d+)?s?)\s+to\s+([0-9:]+(?:\.\d+)?s?)|([0-9:]+(?:\.\d+)?s?)\s*[-–—]\s*([0-9:]+(?:\.\d+)?s?))\s+by\s+(\d+(?:\.\d+)?)x?$",
            lower,
        )
        if m_speed:
            try:
                raw_t1 = m_speed.group(1) or m_speed.group(3)
                raw_t2 = m_speed.group(2) or m_speed.group(4)
                factor = float(m_speed.group(5))
                t1 = parse_time_expression(raw_t1)
                t2 = parse_time_expression(raw_t2)
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[IntentTarget(action="speed", start_time=t1, end_time=t2, factor=factor)],
                    latency_ms=dur_ms,
                    model_name="rule",
                    interpreted_by="rule",
                )
            except Exception:
                pass

        # 4e. Explicit zoom / punch-in range: "zoom in from 1:00 to 1:30 by 1.2x", "punch in 1.2x from 10s to 20s"
        m_zoom = re.search(
            r"^(?:zoom\s+in|punch\s+in|zoom)\s+(?:from\s+([0-9:]+(?:\.\d+)?s?)\s+to\s+([0-9:]+(?:\.\d+)?s?)|([0-9:]+(?:\.\d+)?s?)\s*[-–—]\s*([0-9:]+(?:\.\d+)?s?))\s*(?:by\s+(\d+(?:\.\d+)?)x?)?$",
            lower,
        )
        if m_zoom:
            try:
                raw_t1 = m_zoom.group(1) or m_zoom.group(3)
                raw_t2 = m_zoom.group(2) or m_zoom.group(4)
                factor = float(m_zoom.group(5)) if m_zoom.group(5) else 1.2
                t1 = parse_time_expression(raw_t1)
                t2 = parse_time_expression(raw_t2)
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="applied",
                    targets=[IntentTarget(action="zoom", start_time=t1, end_time=t2, factor=factor)],
                    latency_ms=dur_ms,
                    model_name="rule",
                    interpreted_by="rule",
                )
            except Exception:
                pass

        # Everything else is outside the explicit allowlist and requires the LLM
        dur_ms = (time.perf_counter() - start_t) * 1000.0
        return InterpretationResult(
            status="rejected",
            rejection_reason=f"Instruction '{raw}' not in RuleInterpreter allowlist; requires LLM.",
            latency_ms=dur_ms,
            model_name="rule",
            interpreted_by="rule",
        )
