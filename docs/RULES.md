# PromptCut – Policy Rules

Every non-obvious decision the engine makes is documented here.
Reference these rules in a live review to explain why the code does what it does.

---

## §TimePolicy – How user times are interpreted

**Rule:** User-stated times ("at 1:20", "the last 10 seconds") refer to the
**CURRENT edited timeline** after all active operations have been applied.

**Rationale:** The original timestamps become meaningless once cuts and speed
changes are applied.  A user saying "cut from 1:20 to 1:30" means the segment
that appears at 1:20 on screen **right now**, not at 1:20 in the raw video.

**Config:** `promptcut.config.TIME_POLICY = "current"`.  Changing this
constant to `"original"` switches the entire engine to original-time mode.
Every response payload includes `time_policy` so the caller always knows.

---

## §PartialCaption – Caption with some anchor words cut

**Rule:** When a `CutRange` removes *some but not all* of a caption's anchor
words, the caption survives.  Its timing follows the **surviving** anchor words
(earliest surviving word start → latest surviving word end).  The caption text
is not truncated.  A warning is added to `Timeline.warnings`.  If the
resulting display window is shorter than `CAPTION_MIN_DISPLAY_SECS` (0.7 s),
it is extended to that floor.

**Rationale:** Silently dropping the caption text would be worse than showing
it with slightly wrong timing.  The user can see the warning and decide.

---

## §CaptionFullyCut – Caption with all anchor words cut

**Rule:** When every anchor word of a caption is cut, the caption is moved to
`Timeline.removed_captions` (not `Timeline.captions`) and a warning is added.
The caption is not lost from history – it lives in the removed list and
returns to `captions` if the cut is undone or `restore_op`'d away.

---

## §CaptionMinDisplay – Minimum caption display time

**Rule:** No caption may appear on screen for less than `CAPTION_MIN_DISPLAY_SECS`
(0.7 s).  When the computed display window (word start → word end) is shorter,
the end time is extended to `start + 0.7`.  This applies to both `AddCaption`
and `AutoCaption`.

**Config:** `promptcut.config.CAPTION_MIN_DISPLAY_SECS = 0.7`.

---

## §OverlappingSpeed – Multiple SpeedRange ops on the same word

**Rule:** When two or more `SpeedRange` operations target the same word, the
**last** one in the active log wins for that word (last-write-wins).

**Rationale:** This matches the behaviour of most timeline editors where a
later speed pass overrides an earlier one.  The engine iterates ops in order
and overwrites `speed[word_id]` on each `SpeedRange`.

**Example:**
```
SpeedRange(word_ids=[0,1,2,3], factor=2.0)   → op 0
SpeedRange(word_ids=[2,3],     factor=4.0)   → op 1  (later)
Result: words 0,1 at 2.0×; words 2,3 at 4.0×
```

---

## §Reorder – Words excluded from ReorderSections

**Rule:** `ReorderSections.sections` is an ordered list of word-ID groups.
The output timeline contains **only** the words listed across all sections,
in the order given.  Words present in the transcript but absent from every
section are **excluded** from the output (effectively cut).

**Rationale:** `ReorderSections` is a "describe the whole new structure" op.
Requiring the caller to list all sections explicitly makes the result
unambiguous.

---

## §DuplicateSafety – Sending the same operation twice

**Rule:** The `OperationLog.has_equivalent()` method lets the response layer
detect when an identical op already exists in the active log.  The response
layer should return `Applied` with a "no-op" summary rather than pushing a
duplicate.  Two operations are equivalent if they have the same `op_type` and
identical payload fields (excluding `op_id` and `active`).

---

## §Ambiguity – Phrase matches multiple locations

**Rule:** If `phrase_search` returns ≥ 2 matches for a user-stated phrase, the
response layer MUST return `NeedsClarification` with one option per match.
It must NEVER pick one silently.

---

## §OutOfRange – Time beyond video length

**Rule:** If `time_range_to_word_ids` returns `[]` because the requested time
exceeds `Timeline.total_duration`, the response layer must return `Rejected`
with a human-readable reason including the video length.

---

## §CutEverything – Operation that removes all words

**Rule:** If `engine.apply()` produces an empty `segments` list (all words
cut), it includes `"All words were cut; timeline is empty."` in
`Timeline.warnings`.  The response layer must detect this and return `Rejected`
rather than presenting an empty timeline to the user.

---

## §Determinism – Hash stability

**Rule:** `Timeline.timeline_hash` is a SHA-256 hex string of the canonical
JSON of `segments` and `captions` (keys sorted, floats rounded to
`FLOAT_PRECISION = 6` decimal places, no clock-derived values, no UUIDs).
The hash must be identical on every call to `apply()` with the same `(log,
transcript)` pair on any machine.

**What is NOT in the hash:** `warnings`, `removed_captions`, `total_duration`
(it is derivable from the segments), and `timeline_hash` itself.

---

## §IntentResolutionTolerance – Time Hint Tolerance & Deterministic Tie-Breaker

**Rule:** When resolving user intents targeting a phrase or action with an optional `time_hint`:
1. The resolver compares `time_hint` against the current timeline start time of each candidate occurrence.
2. A candidate is within tolerance if $|t_{\text{candidate}} - t_{\text{hint}}| \le \text{TIME\_HINT\_TOLERANCE\_SECS}$ (where `TIME_HINT_TOLERANCE_SECS = 2.0` seconds).
3. If multiple candidates fall within tolerance, the candidate closest to `time_hint` (minimal absolute distance) is selected.
4. **Deterministic Tie-Breaker:** If two or more candidate occurrences have the exact same distance to `time_hint` (e.g. one candidate at $t = 1.0\text{s}$ and another at $t = 3.0\text{s}$ for `time_hint = 2.0s`), the **earlier occurrence in timeline order** wins.
5. If no candidates fall within tolerance, or if no `time_hint` was provided and multiple matches exist for a destructive edit (`cut`, `speed`), the resolver returns `NeedsClarification` with timestamped options. It must NEVER guess silently.

---

## §CaptionChunking – Pure Partition Rules for Subtitles

**Rule:** Subtitles generated for transcript chunks must follow pure deterministic partition rules:
1. **Sentence Boundary:** Split whenever `word.sentence_end == True`.
2. **Natural Pause:** Split whenever the inter-word silence gap is $\ge 0.3\text{s}$ (`next_word.start - current_word.end >= 0.3`).
3. **Length Ceiling:** Split whenever adding a word would exceed **5 words** OR **30 characters** in the current subtitle line (whichever limit is hit first).
4. **No Repeated Words Heuristic:** The chunker must **never** break lines simply because consecutive words are identical (e.g., "very very good" must remain in a single chunk unless another rule applies).
5. **ASR Without Punctuation (e.g., Vosk):** When `sentence_end` flags are all `False`, the chunker falls back smoothly to the $0.3\text{s}$ pause and 5-word/30-character limits without failing or creating empty chunks.
