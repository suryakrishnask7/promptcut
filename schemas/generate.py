"""
schemas/generate.py  –  export JSON Schemas for all promptcut models.

Run:  uv run python schemas/generate.py

Outputs one .json file per model to schemas/out/.
"""

import json
import pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)


def export(model_class, filename: str):
    schema = model_class.model_json_schema()
    path = OUT / filename
    path.write_text(json.dumps(schema, indent=2))
    print(f"  wrote {path}")


if __name__ == "__main__":
    # Must import after setting up sys.path
    import sys
    sys.path.insert(0, str(HERE.parent / "src"))

    from promptcut.models.transcript import Word, Transcript
    from promptcut.models.operations import (
        CutRange, KeepRange, ReorderSections, SpeedRange,
        MuteWords, BleepWords, AddCaption, SetCaptionStyle, AutoCaption,
        RemoveFillers, RemoveSilences, AddMusicDucked, ZoomSpeaker,
    )
    from promptcut.models.operation_log import OperationLog
    from promptcut.models.timeline import TimelineSegment, CaptionOverlay, Timeline
    from promptcut.response import Applied, NeedsClarification, Rejected

    models = [
        (Word,             "Word.json"),
        (Transcript,       "Transcript.json"),
        (CutRange,         "CutRange.json"),
        (KeepRange,        "KeepRange.json"),
        (ReorderSections,  "ReorderSections.json"),
        (SpeedRange,       "SpeedRange.json"),
        (ZoomSpeaker,      "ZoomSpeaker.json"),
        (MuteWords,        "MuteWords.json"),
        (BleepWords,       "BleepWords.json"),
        (AddCaption,       "AddCaption.json"),
        (SetCaptionStyle,  "SetCaptionStyle.json"),
        (AutoCaption,      "AutoCaption.json"),
        (RemoveFillers,    "RemoveFillers.json"),
        (RemoveSilences,   "RemoveSilences.json"),
        (AddMusicDucked,   "AddMusicDucked.json"),
        (OperationLog,     "OperationLog.json"),
        (TimelineSegment,  "TimelineSegment.json"),
        (CaptionOverlay,   "CaptionOverlay.json"),
        (Timeline,         "Timeline.json"),
        (Applied,          "Applied.json"),
        (NeedsClarification, "NeedsClarification.json"),
        (Rejected,         "Rejected.json"),
    ]

    print("Exporting JSON Schemas to schemas/out/ …")
    for model_cls, fname in models:
        export(model_cls, fname)
    print("Done.")
