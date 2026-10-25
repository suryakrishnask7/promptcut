# PromptCut – Design Notes

## How edits stay attached to content

Every edit in **promptcut** anchors to **word IDs**, not timestamps.

### The three-layer model

```
┌──────────────────────────────────────────────────────────┐
│  Transcript (immutable)                                  │
│  Words: id=0 "Hey" 0.00–0.25 s                          │
│         id=1 "pricing" 81.05–81.38 s                    │
│         …                                                │
└──────────────────────┬───────────────────────────────────┘
                       │  never modified
                       ▼
┌──────────────────────────────────────────────────────────┐
│  OperationLog (append-only)                              │
│  [0] CutRange(word_ids=[0,1,2,3,4])   active=True       │
│  [1] AddCaption(word_ids=[42],…)      active=True       │
│  [2] SpeedRange(word_ids=[42], 1.5)   active=True       │
│      pointer = 3                                         │
└──────────────────────┬───────────────────────────────────┘
                       │  engine.apply()
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Timeline (derived, never stored)                        │
│  segments: [seg0: words 5–41, seg1: words 42–44, …]     │
│  captions: [cap0: word 42, 1.23–1.93 s]                 │
│  timeline_hash: "a3f9…"                                  │
└──────────────────────────────────────────────────────────┘
```

### Why word IDs, not timestamps?

Timestamps change every time you make a cut or speed change.
A caption anchored to "1:20" would drift or become invalid after a 10-second
cut earlier in the video.  A caption anchored to **word 42** ("pricing") always
finds its word regardless of how many cuts or reorders precede it.

### Undo / redo / restore

The `OperationLog` is append-only.  The **pointer** marks how many ops are
"in scope".  `undo()` moves the pointer back; `redo()` moves it forward.
A new `push()` truncates everything past the pointer (clears redo).

`restore_op(op_id)` toggles the `active` flag of one specific op **without**
touching the pointer or any other op.  This lets you surgically deactivate an
old cut and see its words reappear in the timeline, while keeping all later
edits intact.

### Timeline derivation

`engine.apply(log, transcript)` is a **pure function**: same inputs → same
output, always.  It walks the active ops in order, maintains word-state tables
(cut, speed, mute, bleep), then builds `TimelineSegment` objects back-to-back.
Captions resolve against the surviving word set and are extended to the 0.7 s
display floor if needed.

The `timeline_hash` is a SHA-256 of the canonical JSON of all segments and
captions (sorted keys, floats rounded to 6 decimal places, no clock values).

### Time policy

User times ("at 1:20") refer to the **current edited timeline** by default
(`TIME_POLICY = "current"`).  This constant lives in `config.py` and appears
in every response so the caller always knows which coordinate system was used.

### What this schema is NOT

- The Timeline is never persisted as source of truth.
- Word IDs are never re-assigned or recycled.
- The Transcript is never mutated by any edit.
