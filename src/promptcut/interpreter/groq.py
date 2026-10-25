"""
GroqInterpreter: Production OpenAI-compatible interpreter targeting the Groq API.

Features:
- Reads GROQ_API_KEY and GROQ_MODEL from environment (default: 'llama-3.3-70b-versatile').
- Strictly adheres to security rules: never logs, prints, or exposes API keys.
- Temperature 0.
- Structured JSON output + Pydantic validation.
- One corrective retry on invalid JSON, then status="rejected".
- Exponential backoff on HTTP 429 (Rate Limit).
- Context minimization: sends localized transcript windows around mentioned phrases/times,
  current duration, last 6 log items, and last 4 chat turns (NEVER full transcript, NEVER media).
- Disk caching keyed by sha256(instruction + canonical_context).
- Measures latency, tokens used, and calculates cost per request.
- Honors INTERPRETER env flag: 'auto' | 'llm_only' | 'rule_only'.
- Adds interpreted_by ('rule' | 'llm' | 'cache') to every result.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

import httpx

from promptcut.config import DEFAULT_GROQ_MODEL, get_interpreter_mode
from promptcut.interpreter.base import (
    IntentTarget,
    InterpretationResult,
    InterpreterAdapter,
    InterpreterContext,
)
from promptcut.interpreter.rule import RuleInterpreter

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CACHE_DIR = Path(".cache/interpreter")

# System prompt with JSON schema definition and few-shot examples
SYSTEM_PROMPT = """You are the promptcut natural language video editing intent interpreter.
Your task is to analyze user instructions in context and extract structured intent targets.

CRITICAL RULES:
1. NEVER output word IDs. Output intent targets with semantic properties: {action, query, occurrence, time_hint, time_basis, speaker, factor, text, font_size, font_style, color, position, start_time, end_time}.
2. If the user instruction is ambiguous (e.g. mentions a phrase appearing multiple times without a time hint or occurrence), output status="needs_clarification" with a question and options.
3. If the user instruction is completely out of scope or nonsensical, output status="rejected" with a reason.
4. When the user asks to increase or decrease caption font size (e.g. 'increase the font size', 'make captions bigger', 'decrease font size'), NEVER ask for clarification. Output action="set_caption_style" with font_size_delta=4 (or -4 for decrease) or the user's specified number.
5. When the user says "no bleep the first <word> and dont bleep others" or "only bleep the first <word>", they mean "No, only bleep the first occurrence". Output action="bleep_only" with occurrence=1 (or the specified occurrence).
6. When the user simply says "bleep <word>" or "unbleep <word>" without specifying an occurrence, output action="bleep" or "unbleep" with occurrence="all".
7. When the user asks to unmask or show bleeped words in captions (e.g. 'unmask captions', 'unmask the bleeped words', 'dont mask captions', 'show bleeped words in captions', 'unmask wonder'), output action="unmask_captions" with query of the word (or null if all).
8. When the user asks to mask or remask bleeped words in captions (e.g. 'remask captions', 'mask captions', 'mask bleeped words in captions', 'remask wonder'), output action="mask_captions" with query of the word (or null if all).
9. When the user asks to caption, cut, or edit for a specific speaker or person (e.g. 'add captions for Dave', 'caption speaker 1', 'add yellow captions for speaker 2', 'cut speaker 2'), output the target with speaker set to the requested speaker/character (e.g. speaker="Dave" or speaker="Speaker 1"), action="caption" (or "cut"), and any requested color/style.
10. Output MUST be valid JSON adhering strictly to the schema below.

Output JSON Schema:
{
  "status": "applied" | "needs_clarification" | "rejected" | "undo" | "redo" | "restore" | "render",
  "targets": [
    {
      "action": "cut" | "keep" | "speed" | "caption" | "set_caption_style" | "bleep" | "bleep_only" | "unbleep" | "mask_captions" | "unmask_captions" | "set_default_font_step" | "remove_fillers" | "remove_silences" | "undo" | "redo" | "restore" | "render" | "clarification_answer",
      "query": "string or null",
      "occurrence": 1 | 2 | "first" | "second" | "all" | null,
      "time_hint": number or null,
      "time_basis": "current",
      "speaker": "string or null",
      "factor": number or null,
      "text": "string or null",
      "font_size": integer or null,
      "font_size_delta": integer or null,
      "font_style": "normal" | "bold" | "italic" | null,
      "color": "string or null",
      "position": "bottom" | "middle" | "top" | null,
      "start_time": number or null,
      "end_time": number or null
    }
  ],
  "clarification_question": "string or null",
  "clarification_options": ["option 1", "option 2"] or null,
  "rejection_reason": "string or null"
}

Few-shot examples:
Example 1:
User: "there are two nexus 2.67 remove one of them"
Output:
{"status": "applied", "targets": [{"action": "cut", "query": "nexus", "time_hint": 2.67, "time_basis": "current"}]}

Example 2:
User: "remove nexus" (Context snippet shows two occurrences at 2.67s and 5.1s)
Output:
{"status": "needs_clarification", "targets": [], "clarification_question": "'Nexus' occurs twice. Which one would you like to remove?", "clarification_options": ["Occurrence 1 (~2.7s)", "Occurrence 2 (~5.1s)"]}

Example 3:
User: "the second one" (Pending clarification: "Which occurrence of nexus?")
Output:
{"status": "applied", "targets": [{"action": "clarification_answer", "occurrence": 2}]}

Example 4:
User: "caption the whole video"
Output:
{"status": "applied", "targets": [{"action": "caption", "query": "__all__"}]}

Example 5:
User: "move caption to the middle and make font size 36"
Output:
{"status": "applied", "targets": [{"action": "set_caption_style", "position": "middle", "font_size": 36}]}

Example 6:
User: "speed up from 1:00 to 1:30 by 2x"
Output:
{"status": "applied", "targets": [{"action": "speed", "start_time": 60.0, "end_time": 90.0, "factor": 2.0}]}

Example 7:
User: "undo"
Output:
{"status": "undo", "targets": [{"action": "undo"}]}

Example 8:
User: "order a large pepperoni pizza"
Output:
{"status": "rejected", "targets": [], "rejection_reason": "Command out of scope for video editing engine."}

Example 9:
User: "bleep 'wonder'"
Output:
{"status": "applied", "targets": [{"action": "bleep", "query": "wonder", "occurrence": "all"}]}

Example 10:
User: "no bleep the first 'wonder' and dont bleep others"
Output:
{"status": "applied", "targets": [{"action": "bleep_only", "query": "wonder", "occurrence": 1}]}

Example 11:
User: "dont bleep the second 'wonder'"
Output:
{"status": "applied", "targets": [{"action": "unbleep", "query": "wonder", "occurrence": 2}]}

Example 12:
User: "unbleep 'wonder'"
Output:
{"status": "applied", "targets": [{"action": "unbleep", "query": "wonder", "occurrence": "all"}]}

Example 13:
User: "increase the font size for the captions"
Output:
{"status": "applied", "targets": [{"action": "set_caption_style", "font_size_delta": 4}]}

Example 14:
User: "change the default increase/decrease to 5"
Output:
{"status": "applied", "targets": [{"action": "set_default_font_step", "factor": 5.0}]}

Example 15:
User: "cut 0:30-0:40"
Output:
{"status": "applied", "targets": [{"action": "cut", "start_time": 30.0, "end_time": 40.0}]}

Example 16:
User: "unmask captions"
Output:
{"status": "applied", "targets": [{"action": "unmask_captions", "query": null}]}

Example 17:
User: "remask captions"
Output:
{"status": "applied", "targets": [{"action": "mask_captions", "query": null}]}

Example 18:
User: "unmask 'wonder' in captions"
Output:
{"status": "applied", "targets": [{"action": "unmask_captions", "query": "wonder"}]}
"""


class GroqInterpreter(InterpreterAdapter):
    """
    Groq OpenAI-compatible interpreter with structured JSON mode and disk caching.
    Supports INTERPRETER env modes: 'auto' | 'llm_only' | 'rule_only'.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = GROQ_BASE_URL,
        cache_enabled: bool = True,
        max_retries_429: int = 2,
    ) -> None:
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        # Load model strictly from env or default
        self.model = model or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL
        self.base_url = base_url.rstrip("/")
        self.cache_enabled = cache_enabled
        self.max_retries_429 = max_retries_429
        self.rule_fallback = RuleInterpreter()

        if self.cache_enabled:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _compute_cache_key(self, text: str, context: InterpreterContext) -> str:
        payload = {
            "text": text.strip().lower(),
            "model": self.model,
            "duration": context.duration,
            "time_policy": context.time_policy,
            "log": context.log_summary[-6:],
            "chat": context.chat_turns[-4:],
            "snippets": context.transcript_snippets,
            "pending": context.pending_clarification,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _read_cache(self, key: str) -> InterpretationResult | None:
        if not self.cache_enabled:
            return None
        cache_file = CACHE_DIR / f"{key}.json"
        if cache_file.is_file():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                res = InterpretationResult.model_validate(data)
                res.cached = True
                res.interpreted_by = "cache"
                res.latency_ms = 0.0
                return res
            except Exception:
                return None
        return None

    def _write_cache(self, key: str, result: InterpretationResult) -> None:
        if not self.cache_enabled:
            return
        cache_file = CACHE_DIR / f"{key}.json"
        try:
            cache_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            pass

    def interpret(
        self,
        text: str,
        context: InterpreterContext,
        mode: str | None = None,
    ) -> InterpretationResult:
        start_t = time.perf_counter()
        raw_text = text.strip()
        effective_mode = (mode or get_interpreter_mode()).strip().lower()

        # 1. rule_only mode: LLM bypassed entirely; only RuleInterpreter is used
        if effective_mode == "rule_only":
            res = self.rule_fallback.interpret(raw_text, context)
            res.interpreted_by = "rule"
            res.latency_ms = (time.perf_counter() - start_t) * 1000.0
            return res

        # 2. auto mode: check cache, then check RuleInterpreter allowlist
        if effective_mode == "auto":
            # Check disk cache first
            cache_key = self._compute_cache_key(raw_text, context)
            cached_result = self._read_cache(cache_key)
            if cached_result is not None:
                cached_result.interpreted_by = "cache"
                return cached_result

            # Fast-path RuleInterpreter allowlist (undo, redo, render, cut 0:30-0:40)
            rule_res = self.rule_fallback.interpret(raw_text, context)
            if rule_res.status != "rejected":
                rule_res.interpreted_by = "rule"
                return rule_res

            # Everything else goes to the LLM
            if not self.api_key:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="rejected",
                    rejection_reason="Instruction not in RuleInterpreter allowlist and GROQ_API_KEY not set.",
                    latency_ms=dur_ms,
                    model_name=self.model,
                    interpreted_by="llm",
                )

        # 3. llm_only mode: RuleInterpreter is bypassed entirely
        if effective_mode == "llm_only":
            cache_key = self._compute_cache_key(raw_text, context)
            cached_result = self._read_cache(cache_key)
            if cached_result is not None:
                cached_result.interpreted_by = "cache"
                return cached_result

            if not self.api_key:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="rejected",
                    rejection_reason="GROQ_API_KEY not set and RuleInterpreter is bypassed in llm_only mode.",
                    latency_ms=dur_ms,
                    model_name=self.model,
                    interpreted_by="llm",
                )

        # Prepare messages for LLM
        user_content = {
            "instruction": raw_text,
            "current_timeline_duration": context.duration,
            "time_policy": context.time_policy,
            "recent_actions": context.log_summary[-6:],
            "recent_dialog": context.chat_turns[-4:],
            "localized_transcript_context": context.transcript_snippets,
            "pending_clarification": context.pending_clarification,
        }

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_content, indent=2)},
        ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        client = httpx.Client(timeout=30.0)
        try:
            return self._call_with_retry(client, messages, headers, start_t, cache_key, raw_text, context)
        finally:
            client.close()

    def _call_with_retry(
        self,
        client: httpx.Client,
        messages: list[dict[str, str]],
        headers: dict[str, str],
        start_t: float,
        cache_key: str,
        raw_text: str,
        context: InterpreterContext,
    ) -> InterpretationResult:
        mode = get_interpreter_mode()
        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        retries_429 = 0
        backoff_sec = 1.0

        while True:
            try:
                resp = client.post(url, headers=headers, json=body)
                if resp.status_code == 429:
                    retries_429 += 1
                    if retries_429 > self.max_retries_429:
                        dur_ms = (time.perf_counter() - start_t) * 1000.0
                        return InterpretationResult(
                            status="rejected",
                            rejection_reason="Rate limit exceeded on Groq API.",
                            latency_ms=dur_ms,
                            model_name=self.model,
                            interpreted_by="llm",
                        )
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0
                    continue

                resp.raise_for_status()
                data = resp.json()
                break
            except (httpx.HTTPError, Exception) as e:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="rejected",
                    rejection_reason=f"Groq API error ({type(e).__name__}): {e}".strip(),
                    latency_ms=dur_ms,
                    model_name=self.model,
                    interpreted_by="llm",
                )

        # Extract completion content and usage
        choice = data.get("choices", [{}])[0]
        content_str = choice.get("message", {}).get("content", "{}")
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        comp_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + comp_tokens)

        # Parse and validate JSON
        try:
            parsed = json.loads(content_str)
            interp = self._validate_and_build(parsed, prompt_tokens, comp_tokens, total_tokens, start_t, content_str)
            interp.interpreted_by = "llm"
            self._write_cache(cache_key, interp)
            return interp
        except Exception as json_err:
            # One corrective retry on invalid JSON
            messages.append({"role": "assistant", "content": content_str})
            messages.append({
                "role": "user",
                "content": f"The response was invalid JSON: {json_err}. Output the corrected valid JSON adhering strictly to the schema.",
            })
            try:
                resp2 = client.post(url, headers=headers, json=body)
                resp2.raise_for_status()
                data2 = resp2.json()
                content_str2 = data2.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                parsed2 = json.loads(content_str2)
                usage2 = data2.get("usage", {})
                p2 = prompt_tokens + usage2.get("prompt_tokens", 0)
                c2 = comp_tokens + usage2.get("completion_tokens", 0)
                interp2 = self._validate_and_build(parsed2, p2, c2, p2 + c2, start_t, content_str2)
                interp2.interpreted_by = "llm"
                self._write_cache(cache_key, interp2)
                return interp2
            except Exception:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return InterpretationResult(
                    status="rejected",
                    rejection_reason="Failed to parse structured intent output from model.",
                    raw_response=content_str,
                    tokens_used=total_tokens,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=comp_tokens,
                    latency_ms=dur_ms,
                    model_name=self.model,
                    interpreted_by="llm",
                )

    def _validate_and_build(
        self,
        parsed: dict[str, Any],
        prompt_tokens: int,
        comp_tokens: int,
        total_tokens: int,
        start_t: float,
        raw_str: str,
    ) -> InterpretationResult:
        targets_raw = parsed.get("targets", [])
        targets = [IntentTarget.model_validate(t) for t in targets_raw]
        dur_ms = (time.perf_counter() - start_t) * 1000.0

        # Estimated cost: $0.15/1M input tokens, $0.60/1M output tokens
        cost = (prompt_tokens * 0.15 + comp_tokens * 0.60) / 1_000_000.0

        return InterpretationResult(
            status=parsed.get("status", "applied"),
            targets=targets,
            clarification_question=parsed.get("clarification_question"),
            clarification_options=parsed.get("clarification_options"),
            rejection_reason=parsed.get("rejection_reason"),
            raw_response=raw_str,
            tokens_used=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=comp_tokens,
            latency_ms=dur_ms,
            cost_usd=cost,
            model_name=self.model,
            interpreted_by="llm",
        )

    def list_models(self) -> list[str]:
        """
        Query GET https://api.groq.com/openai/v1/models to verify available model IDs.
        """
        if not self.api_key:
            return []
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return [m.get("id") for m in data.get("data", []) if m.get("id")]
        except Exception:
            return []
