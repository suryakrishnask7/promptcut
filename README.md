# PromptCut – Prompt-Based Video Editing Engine

> **IncuBrix Engineering Assessment 2 Submission**  
> Conversational, prompt-based video editing engine with deterministic timeline compilation, real media intake, FFmpeg filtergraph rendering, swappable ASR/diarization adapters, and scripted session runner.

---

## Overview

**PromptCut** (`promptcut`) is a prompt-based video editing engine. When a creator edits a video by conversing with an assistant ("cut the first 30 seconds", "remove filler words", "add a caption 'Welcome!'", "undo", "put back the intro"), every edit shifts the timing that subsequent edits depend on.

The fundamental design principle of **promptcut** is:

> **Edits belong to content, not to clock positions.**  
> A caption attached to "welcome to the channel" stays anchored to those exact words after any preceding words are cut, sped up, moved, or restored.

---

## Key Features

- **Content-Anchored Editing**: Operations anchor to immutable **Word IDs**, not floating timestamps. Captions, mutes, and bleeps track their words through any sequence of cuts, reordering, and speed adjustments.
- **Three-Layer Architecture**:
  1. **Transcript Layer**: Read-only source words with start/end timing, speaker IDs, and confidence scores. Never mutated.
  2. **Operation Log**: Monotonically ordered, append-only history of user intents (`CutRange`, `KeepRange`, `SpeedRange`, `AddCaption`, etc.). Undo/redo moves an execution pointer; `restore_op(id)` selectively toggles any earlier operation.
  3. **Derived Timeline**: Recomputed deterministically on demand by `apply(log, transcript)`. Never stored as mutable state.
- **Real Media Intake & Immutability**:
  - `ffprobe` wrapper extracting duration, fps, resolution, audio presence, and variable-frame-rate flags.
  - Computes SHA-256 of the source file on intake; enforces `check_original_unchanged()` at session completion to guarantee the source media is never modified.
- **Swappable Natural Language Interpreter**:
  - `RuleInterpreter`: deterministic local rule-based instruction parser for exact commands, ranges, undo/redo, style, and renders without API keys.
  - `MockInterpreter`: deterministic test adapter with fixture outputs for CI testing.
  - `GroqInterpreter`: production OpenAI-compatible adapter targeting Groq API (`https://api.groq.com/openai/v1`). Configured via `GROQ_API_KEY` and `GROQ_MODEL` from env (default: `openai/gpt-oss-120b`). Disk cached on `sha256(instruction + canonical_context)` with full token and cost tracking.
- **Swappable Transcription & Diarization**:
  - `FasterWhisperAdapter`: production ASR with word-level timestamps and disk caching keyed by `source_hash + model_name`.
  - `MockTranscriptionAdapter`: zero-API-key testing from pre-recorded JSON transcript fixtures.
  - `SidecarDiarizationAdapter`: loads speaker turn intervals or word-ID maps from JSON sidecars.
- **Tolerance-Based Silence Snapping**:
  - Automatically snaps cut boundaries to natural inter-word silence gaps (within configurable tolerance).
  - Emits descriptive warnings when mid-word cuts cannot snap cleanly.
- **FFmpeg Filtergraph Engine**:
  - Compiles video `trim`/`setpts` and chained audio `atrim`/`atempo` filtergraphs.
  - Burns in stylized ASS subtitles with exact BGR color conversion.
  - Supports fast `preview` mode (480p, disk cached) and frame-accurate `final` production renders.
- **Session Runner & CLI**:
  - Executes sequential multi-step edit scripts from YAML definitions.
  - Manages two-way clarification dialog loops.
  - Emits `results.json`, `timeline.json`, and standard `timeline.otio` for NLE interchange.

---

## Quick Setup (< 10 Commands)

```bash
# 1. Clone repository
git clone <repo-url> && cd promptcut

# 2. Verify Python 3.11+ is installed
python --version

# 3. Create and activate a virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
# source .venv/bin/activate

# 4. Install promptcut in editable mode with dependencies
python -m pip install -e .
python -m pip install pytest pytest-cov

# 5. Run full test suite (159 tests)
python -m pytest

# 6. Run test suite with coverage report (90% overall, 99% core)
python -m pytest --cov=promptcut --cov-report=term-missing

# 7. Generate JSON schemas for all models
python schemas/generate.py
```

---

## CLI Usage

The package provides the `promptcut` CLI tool (also runnable via `python -m promptcut.cli.main`):

### 1. Transcribe Media
Transcribe an input audio/video file and output a standardized Promptcut Transcript JSON:
```bash
# Using mock fixture (offline / test)
promptcut transcribe input.mp4 -o transcript.json --adapter mock

# Using Faster-Whisper with speaker sidecar
promptcut transcribe interview.mp4 -o transcript.json --adapter faster-whisper --sidecar speakers.json
```

### 2. Inspect Transcript
Inspect word IDs, start/end timestamps, speakers, and confidence scores:
```bash
promptcut inspect transcript.json
```

### 3. Run Edit Session
Execute a scripted multi-step editing session from a YAML file:
```bash
promptcut run-session session.yaml -o out/ --preset final
```

### 4. Launch Local Web UI & REST API
Start the FastAPI server and open the interactive single-page editing dashboard:
```bash
promptcut serve --port 8000
# Open your browser to http://127.0.0.1:8000/ui
```

---

## Standard Error Schema (IncuBrix Integration Standard #5)

All error responses from the API adhere to the standardized JSON schema:

```json
{
  "error": {
    "code": "UPPER_SNAKE_CASE",
    "message": "Human-readable explanation of error",
    "retryable": false,
    "details": {}
  }
}
```

### Registered Error Codes

| Code | HTTP Status | Retryable | Description |
|:---|:---:|:---:|:---|
| `BAD_REQUEST` | 400 | No | Malformed payload or missing required parameters |
| `UNAUTHORIZED` | 401 | No | Authentication required or invalid credentials |
| `FORBIDDEN` | 403 | No | Forbidden access to resource or path outside sandbox |
| `NOT_FOUND` | 404 | No | Requested session, job, word, or asset does not exist |
| `METHOD_NOT_ALLOWED` | 405 | No | HTTP verb not supported on target route |
| `CONFLICT` | 409 | No | Resource state conflict (e.g. session not transcribed) |
| `IDEMPOTENCY_KEY_REUSED` | 409 | No | Same `Idempotency-Key` sent with a different request payload |
| `PAYLOAD_TOO_LARGE` | 413 | No | Input media or file upload exceeds size threshold |
| `VALIDATION_ERROR` | 422 | No | JSON schema validation failure on request body/params |
| `SSRF_ATTEMPT_BLOCKED` | 422 | No | Video URL target resolved to private, loopback, or link-local IP |
| `TOO_MANY_REQUESTS` | 429 | Yes | Rate limit reached; retry after backoff |
| `INTERNAL_SERVER_ERROR` | 500 | Yes | Unhandled internal exception; safe to retry |
| `SERVICE_UNAVAILABLE` | 503 | Yes | External service or dependency temporarily unavailable |

---

## Scripted Session Runner Format

Session definitions are authored in clean YAML:

```yaml
# session.yaml
video: path/to/input.mp4
transcript: path/to/transcript.json   # Optional fixture or omit to transcribe via Whisper
sidecar: path/to/speakers.json       # Optional speaker diarization sidecar
output_video: final_edit.mp4         # Optional custom output name (defaults to rendered.mp4)

steps:
  - say: "add a caption 'Welcome!' when I say welcome to the channel"
  - say: "cut the first 30 seconds"
  - say: "remove the part about pricing"
  - say: "the second one"              # Clarification response to ambiguous hit
  - say: "speed up from 1:00 to 1:30 by 2x"
  - say: "actually, put the intro back"
  - say: "undo"
  - say: "make that caption red"
  - say: "render"
```

### Output Artifacts
When executed, the runner outputs:
- **`out/results.json`**: Chronological log of each step's execution (`step`, `say`, `status`, `operations`, `summary`, `question`, `options`, `timeline_hash`, `duration_s`).
- **`out/timeline.json`**: Complete derived Timeline JSON specification.
- **`out/timeline.otio`**: Standard OpenTimelineIO timeline for DaVinci Resolve, Final Cut Pro, or Premiere.
- **`out/final_edit.mp4`**: Rendered video with frame-accurate audio sync and burned-in subtitles.
- **Source Immutability**: Asserts that `compute_file_sha256(input.mp4)` matches the pre-session intake hash.

---

## Directory Structure

```
promptcut/
├── pyproject.toml              # Build backend, CLI console_scripts, dependencies
├── README.md                   # System documentation & usage guide
├── REPORT.md                   # Engineering assessment report & metrics
├── SOURCES.md                  # Licenses, third-party libraries, benchmarks
├── AI_USE.md                   # Full disclosure of AI assistant usage
├── docs/
│   ├── DESIGN.md               # Architectural design notes & three-layer model
│   └── RULES.md                # Policy rules (captions, silence, time policy, etc.)
├── schemas/
│   ├── generate.py             # Script to export JSON Schema for all models
│   └── out/                    # 21 generated JSON Schema files
├── src/
│   └── promptcut/
│       ├── __init__.py         # Package exports
│       ├── config.py           # Engine configuration & constants
│       ├── engine.py           # Core apply() function & timeline compilation
│       ├── resolver.py         # Text/time resolvers & phrase search
│       ├── response.py         # Applied / NeedsClarification / Rejected models
│       ├── otio_export.py      # OpenTimelineIO export converter
│       ├── api/
│       │   ├── __init__.py
│       │   ├── app.py          # FastAPI application & UI mounting
│       │   ├── routes.py       # REST API v1 endpoints
│       │   └── streaming.py    # HTTP Range video streaming
│       ├── ui/
│       │   └── index.html      # Self-contained local single-page UI
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py         # CLI implementation (transcribe, inspect, run-session, serve)
│       ├── diarization/
│       │   ├── __init__.py
│       │   └── sidecar.py      # Speaker sidecar reader & DiarizationAdapter
│       ├── media/
│       │   ├── __init__.py
│       │   ├── intake.py       # ffprobe wrapper, SHA-256 intake & immutability
│       │   └── silence_snapping.py # Tolerance-based silence boundary snapping
│       ├── models/
│       │   ├── transcript.py   # Word & Transcript models
│       │   ├── operations.py   # All edit operations discriminated union
│       │   ├── timeline.py     # TimelineSegment, CaptionOverlay, Timeline
│       │   └── operation_log.py# OperationLog state machine (undo/redo/restore)
│       ├── render/
│       │   ├── __init__.py
│       │   ├── ass.py          # Advanced SubStation Alpha subtitle generator
│       │   └── ffmpeg.py       # Filtergraph builder, preview cache & render
│       ├── session/
│       │   ├── __init__.py
│       │   ├── parser.py       # Rule-based natural language instruction parser
│       │   └── runner.py       # YAML session runner & artifact generator
│       └── transcription/
│           ├── __init__.py
│           ├── base.py         # TranscriptionAdapter ABC
│           ├── cache.py        # Disk caching by source_hash + model_name
│           ├── mock.py         # MockTranscriptionAdapter for fixtures
│           └── whisper.py      # FasterWhisperAdapter
└── tests/
    ├── conftest.py             # Pytest fixtures
    ├── test_api.py             # FastAPI REST endpoints & UI tests
    ├── test_cli.py             # CLI command tests
    ├── test_diarization.py     # Speaker sidecar tests
    ├── test_engine.py          # Deterministic engine tests
    ├── test_intake.py          # Media intake and SHA-256 immutability tests
    ├── test_log.py             # OperationLog undo/redo/restore tests
    ├── test_models.py          # Pydantic v2 data models validation tests
    ├── test_otio_export.py     # OpenTimelineIO export tests
    ├── test_render.py          # FFmpeg rendering, ASS burn-in, sync tests
    ├── test_resolver.py        # Phrase search and time expression tests
    ├── test_response.py        # Response envelope tests
    ├── test_session_runner.py  # End-to-end scripted session tests
    ├── test_silence_snapping.py# Silence boundary snapping tests
    ├── test_spec_worked_example.py # Exact 8-step worked example test
    ├── test_transcription.py   # Faster-Whisper and mock transcription tests
    ├── test_worked_example.py  # Deterministic replay and edge case tests
    └── fixtures/
        ├── single_speaker.py   # 3-minute single-speaker transcript
        └── two_speaker.py      # 90-second interview transcript
```

---

## Supported Operations

| Operation | Description | Target |
|:---|:---|:---|
| `CutRange` | Cut specified words from playback | `word_ids` |
| `KeepRange` | Keep only specified words; cut remainder | `word_ids` |
| `ReorderSections` | Reorder sections into explicit sequence | `sections: list[list[int]]` |
| `SpeedRange` | Adjust playback speed (e.g. 1.5×, 2.0×) | `word_ids`, `factor` |
| `MuteWords` | Mute audio; retain video frames | `word_ids` |
| `BleepWords` | Replace audio with bleep tone | `word_ids` |
| `AddCaption` | Add text overlay anchored to words | `word_ids`, `text`, `color` |
| `AutoCaption` | Auto-generate caption from transcript words | `word_ids` |
| `SetCaptionStyle` | Update color or size of caption | `target_caption_op_id`, `color` |
| `RemoveFillers` | Clean out filler words ("um", "uh", "like") | `filler_words` |
| `RemoveSilences` | Compress gaps > `min_silence_secs` | `min_silence_secs`, `max_gap_secs` |
| `AddMusicDucked` | Background music ducked under speech | `word_ids`, `track_ref`, `duck_level_db` |
| `undo` / `redo` | Monotonic undo/redo pointer traversal | OperationLog |
| `restore_op` | Selectively toggle any earlier operation | `op_id` |

---

## Verification & Test Results

- **Suite**: **164 automated tests** covering data models, engine compilation, state machine mechanics, phrase matching, time parsing, silence snapping, FFmpeg rendering, scripted sessions, and REST API/UI.
- **Coverage**: High test coverage across the full system (98%+ on core engine).
- **Execution Speed**: Entire test suite executes in **~2.6 seconds**.
- **Determinism**: 100% stable timeline hash across replay runs.
- **Media Immutability**: All source media verified byte-for-byte unchanged via SHA-256 verification.
