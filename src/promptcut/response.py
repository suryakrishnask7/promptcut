"""
Three-way response types for the engine API layer.

Every response carries ``time_policy`` so callers always know which coordinate
system was used for any time values in the payload.

Applied          – edit(s) were applied successfully.
NeedsClarification – the instruction was ambiguous (e.g. "pricing" matched 3
                    places); the caller must present the options to the user.
Rejected         – the instruction is impossible or out of range.

Rules
-----
• Ambiguous destructive instruction (phrase matches ≥ 2 locations)
  → NeedsClarification, never guess.
• Out of range / cut-everything / impossible → Rejected.
• Duplicate op (same payload already in active log) → Applied with
  a "no-op" summary (idempotent).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from promptcut.config import TIME_POLICY


class OpSummary(BaseModel):
    """One-line description of a single applied operation."""

    op_id: int
    op_type: str
    description: str


class DurationSummary(BaseModel):
    """Before/after timeline lengths to surface in the UI."""

    before_secs: float
    after_secs: float

    @property
    def delta_secs(self) -> float:
        return self.after_secs - self.before_secs


class Applied(BaseModel):
    """
    Returned when one or more operations were accepted and applied.

    operations   – summaries of every op that was pushed.
    duration     – before/after timeline lengths.
    summary      – human-readable one-liner for the UI.
    time_policy  – always TIME_POLICY; states the coordinate system used.
    warnings     – policy decisions made during apply() (e.g. removed captions).
    """

    operations: list[OpSummary] = Field(default_factory=list)
    duration: DurationSummary
    summary: str
    time_policy: str = Field(default_factory=lambda: TIME_POLICY)
    warnings: list[str] = Field(default_factory=list)


class ClarificationOption(BaseModel):
    """One candidate match for the user to choose from."""

    label: str          # human-readable description of this match
    word_ids: list[int] # the word IDs that would be targeted
    context: str = ""   # surrounding text for UI display


class NeedsClarification(BaseModel):
    """
    Returned when the instruction was ambiguous.

    question – the clarifying question to show the user.
    options  – the candidate matches to choose from.
    time_policy – always TIME_POLICY.
    """

    question: str
    options: list[ClarificationOption]
    time_policy: str = Field(default_factory=lambda: TIME_POLICY)


class Rejected(BaseModel):
    """
    Returned when the instruction cannot be executed.

    reason           – human-readable explanation.
    conflicting_op_id – op_id of an existing op that conflicts, if applicable.
    time_policy      – always TIME_POLICY.
    """

    reason: str
    conflicting_op_id: int | None = None
    time_policy: str = Field(default_factory=lambda: TIME_POLICY)
