"""
promptcut – Prompt-Based Video Editing Engine, deterministic core.

Public surface re-exported here so callers only need:
    from promptcut import OperationLog, Transcript, apply, ...
"""

from promptcut.config import TIME_POLICY, CAPTION_MIN_DISPLAY_SECS, FLOAT_PRECISION
from promptcut.models.transcript import Word, Transcript
from promptcut.models.operations import (
    CutRange,
    KeepRange,
    ReorderSections,
    SpeedRange,
    MuteWords,
    BleepWords,
    UnbleepWords,
    AddCaption,
    SetCaptionStyle,
    AutoCaption,
    MaskCaptions,
    UnmaskCaptions,
    RemoveFillers,
    RemoveSilences,
    AddMusicDucked,
    Operation,
)
from promptcut.models.operation_log import OperationLog
from promptcut.models.timeline import TimelineSegment, CaptionOverlay, Timeline
from promptcut.engine import apply
from promptcut.otio_export import export_otio, to_otio_dict
from promptcut.resolver import (
    phrase_search,
    parse_time_expression,
    time_range_to_word_ids,
    MeaningSearchAdapter,
    MockMeaningSearchAdapter,
)
from promptcut.response import Applied, NeedsClarification, Rejected

__all__ = [
    # config
    "TIME_POLICY",
    "CAPTION_MIN_DISPLAY_SECS",
    "FLOAT_PRECISION",
    # models
    "Word",
    "Transcript",
    "CutRange",
    "KeepRange",
    "ReorderSections",
    "SpeedRange",
    "MuteWords",
    "BleepWords",
    "UnbleepWords",
    "AddCaption",
    "SetCaptionStyle",
    "AutoCaption",
    "MaskCaptions",
    "UnmaskCaptions",
    "RemoveFillers",
    "RemoveSilences",
    "AddMusicDucked",
    "Operation",
    "OperationLog",
    "TimelineSegment",
    "CaptionOverlay",
    "Timeline",
    # engine
    "apply",
    # otio
    "to_otio_dict",
    "export_otio",
    # resolver
    "phrase_search",
    "parse_time_expression",
    "time_range_to_word_ids",
    "MeaningSearchAdapter",
    "MockMeaningSearchAdapter",
    # responses
    "Applied",
    "NeedsClarification",
    "Rejected",
]
