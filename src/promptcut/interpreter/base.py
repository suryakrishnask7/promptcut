"""
Base data models and abstract adapter for Promptcut Natural Language Interpretation.

Defines the contract for transforming natural language edit prompts into
structured intent targets (never raw word IDs), supporting multi-turn clarification,
token/cost tracking, latency measurement, and disk caching.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class IntentTarget(BaseModel):
    """
    Extracted user intent target.

    Crucially, models output intent targets and semantic properties (query phrase,
    occurrence, time hint, speaker, etc.), NEVER word IDs. Word IDs are resolved
    deterministically by the engine resolver.
    """

    action: str = Field(
        ...,
        description=(
            "Action type: 'cut', 'keep', 'speed', 'caption', 'set_caption_style', "
            "'bleep', 'bleep_only', 'unbleep', "
            "'remove_fillers', 'remove_silences', 'set_default_font_step', 'undo', 'redo', 'restore', "
            "'render', 'clarification_answer', 'correct_word', 'rejected'"
        ),
    )
    query: str | None = Field(
        default=None,
        description="Target phrase or keyword in transcript, e.g. 'nexus', 'pricing'",
    )
    occurrence: int | str | None = Field(
        default=None,
        description="1-based occurrence index (1, 2) or 'first', 'second', 'all'",
    )
    time_hint: float | None = Field(
        default=None,
        description="Optional time hint in seconds (e.g. 2.67)",
    )
    time_basis: str = Field(
        default="current",
        description="Time coordinate basis: 'current' (default) or 'original'",
    )
    speaker: str | None = Field(
        default=None,
        description="Optional speaker identifier (e.g. 'S1', 'guest')",
    )
    factor: float | None = Field(
        default=None,
        description="Speed multiplication factor (e.g. 1.5, 2.0)",
    )
    text: str | None = Field(
        default=None,
        description="Text content for caption or word correction",
    )
    font_size: int | None = Field(
        default=None,
        description="Caption font size in pt",
    )
    font_size_delta: int | None = Field(
        default=None,
        description="Relative caption font size change in pt (+4, -4)",
    )
    font_style: str | None = Field(
        default=None,
        description="Caption font style ('normal', 'bold', 'italic')",
    )
    color: str | None = Field(
        default=None,
        description="Caption color name or hex code",
    )
    position: str | None = Field(
        default=None,
        description="Caption position ('bottom', 'middle', 'top')",
    )
    start_time: float | None = Field(
        default=None,
        description="Explicit start time in seconds",
    )
    end_time: float | None = Field(
        default=None,
        description="Explicit end time in seconds",
    )
    target_op_id: int | None = Field(
        default=None,
        description="Target operation ID for restore operation",
    )


class InterpreterContext(BaseModel):
    """
    Context sent to the natural language interpreter.

    To protect privacy and minimize token latency, localized transcript windows
    around mentioned phrases/times are provided, NEVER full transcripts and NEVER media.
    """

    duration: float = 0.0
    time_policy: str = "current"
    log_summary: list[dict[str, Any]] = Field(default_factory=list)  # last 6 log items
    chat_turns: list[dict[str, Any]] = Field(default_factory=list)  # last 4 chat turns
    transcript_snippets: list[str] = Field(default_factory=list)  # localized snippets
    pending_clarification: dict[str, Any] | None = None


class InterpretationResult(BaseModel):
    """
    Result returned by an InterpreterAdapter.
    """

    status: str = Field(
        ...,
        description="Status: 'applied', 'needs_clarification', 'rejected', 'undo', 'redo', 'restore', 'render'",
    )
    targets: list[IntentTarget] = Field(
        default_factory=list,
        description="Extracted intent targets",
    )
    clarification_question: str | None = None
    clarification_options: list[str] | None = None
    rejection_reason: str | None = None
    raw_response: str | None = None
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    model_name: str | None = None
    cached: bool = False
    interpreted_by: str = "rule"


class InterpreterAdapter(ABC):
    """
    Abstract swappable adapter for natural language intent interpretation.
    """

    @abstractmethod
    def interpret(self, text: str, context: InterpreterContext) -> InterpretationResult:
        """
        Parse user natural language prompt in context into proposed intent targets
        or a clarification request.
        """
        ...
