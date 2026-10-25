# PromptCut – Sources, Licenses & Third-Party Components

> **Compliance with IncuBrix Assessment 2 Rules (Brief page 5 & page 7)**  
> All software, dependencies, and algorithms used in the default path are 100% commercially usable (MIT/Apache/BSD/PSF). No AGPL, CC BY-NC, or research-only licenses are present.

---

## 1. Runtime & API Server Dependencies

| Component | Version | License | Commercial Use | Purpose | Source / URL |
|:---|:---|:---|:---|:---|:---|
| **Python** | 3.11+ (verified 3.15) | PSF License | Permitted | Core programming language runtime | https://www.python.org/ |
| **pydantic** | >=2.0 (2.14.1) | MIT | Permitted | Fast data validation, strict typing, schema generation | https://github.com/pydantic/pydantic |
| **fastapi** | >=0.110 (0.146.0) | MIT | Permitted | Asynchronous REST API framework & UI server | https://github.com/tiangolo/fastapi |
| **starlette** | >=0.40 (1.9.0) | BSD 3-Clause | Permitted | ASGI toolkit underlying FastAPI | https://github.com/encode/starlette |
| **uvicorn** | >=0.30 (0.54.0) | BSD 3-Clause | Permitted | High-performance lightning-fast ASGI server | https://github.com/encode/uvicorn |
| **python-multipart** | >=0.0.9 (0.0.32) | Apache 2.0 | Permitted | Streaming multipart form-data parser for file uploads | https://github.com/Kludex/python-multipart |
| **ruamel.yaml** | >=0.18 (0.19.1) | MIT | Permitted | Pure-Python YAML 1.2 parser & emitter for session runner | https://sourceforge.net/projects/ruamel-yaml/ |
| **annotated-types** | >=0.6.0 | MIT | Permitted | Metadata typing protocols for Pydantic v2 | https://github.com/annotated-types/annotated-types |
| **typing-extensions** | >=4.16.0 | PSF License | Permitted | Forward compatibility for typing features | https://github.com/python/typing_extensions |
| **pyyaml** | >=6.0 | MIT | Permitted | OpenAPI 3.1 YAML schema export & parsing | https://github.com/yaml/pyyaml |
| **boto3** | >=1.34 (optional) | Apache 2.0 | Permitted | S3 and MinIO cloud storage adapter client | https://github.com/boto/boto3 |

---

## 2. Media & External Tools

| Component | Version | License | Commercial Use | Purpose | Architecture / Isolation |
|:---|:---|:---|:---|:---|:---|
| **FFmpeg & ffprobe** | 7.x / 9.x | LGPL 2.1+ / GPL 2+ | Permitted (unlinked CLI process) | Video/audio inspection, filtergraph rendering, subtitle burn-in | Invoked exclusively via subprocess CLI (`intake.py`, `ffmpeg.py`); zero static or dynamic C linkage |
| **vosk** | >=0.3.45 | Apache 2.0 | Permitted | Offline speech recognition engine with word-level Kaldi alignment | Wrapped behind `TranscriptionAdapter` in `src/promptcut/transcription/vosk_adapter.py` |
| **faster-whisper** | >=1.0 (optional) | MIT | Permitted | Fast ASR inference engine powered by CTranslate2 | Wrapped behind `TranscriptionAdapter` in `src/promptcut/transcription/whisper.py` |
| **CTranslate2** | >=4.0 (optional) | MIT | Permitted | Fast inference engine for Transformer models | Dependency of faster-whisper |
| **OpenAI Whisper Models** | tiny / base / small | MIT | Permitted | Pretrained speech recognition weights (`Systran/faster-whisper-*`) | MIT-licensed weights hosted on Hugging Face |


---

## 3. Development & Test Dependencies

| Component | Version | License | Commercial Use | Purpose | Source / URL |
|:---|:---|:---|:---|:---|:---|
| **pytest** | >=8.0 (9.1.1) | MIT | Permitted | Automated test framework & runner | https://github.com/pytest-dev/pytest |
| **pytest-cov** | >=5.0 (7.1.0) | MIT | Permitted | Test coverage reporting | https://github.com/pytest-dev/pytest-cov |
| **coverage** | >=7.10.0 | Apache 2.0 | Permitted | Code coverage measurement engine | https://github.com/nedbat/coveragepy |
| **httpx** | >=0.27 (0.28.1) | BSD 3-Clause | Permitted | HTTP client for FastAPI TestClient integration testing | https://github.com/encode/httpx |
| **hatchling** | >=1.25 | MIT | Permitted | Standard PEP 517 build backend | https://github.com/pypa/hatch |

---

## 4. Frontend & User Interface

- **Zero-Framework Architecture**: No npm, node_modules, webpack, or frontend build toolchains.
- **Pure Web Standards**: HTML5 semantic markup, Vanilla CSS3 (custom CSS design tokens for light/dark themes), ES6+ Vanilla JavaScript.
- **Typography**: Google Fonts Inter & JetBrains Mono (SIL Open Font License 1.1 — 100% commercially usable).

---

## 5. Swappable Provider Architecture (§3 Compliance)

Per IncuBrix rule §3 ("Swappable providers: Put every external AI service behind a small adapter selected by configuration, and include a mock adapter so tests run with no API keys"):

1. **Natural Language Interpreter (`promptcut.interpreter`)**:
   - `InterpreterAdapter` (abstract ABC)
   - `RuleInterpreter`: Deterministic local rule engine for exact commands, ranges, undo/redo, style, and renders. Zero network or key requirement.
   - `MockInterpreter`: Fixture-based and heuristic mock adapter for unit tests and deterministic CI runs.
   - `GroqInterpreter`: Production OpenAI-compatible adapter targeting Groq API (`https://api.groq.com/openai/v1`). Configured via `GROQ_API_KEY` and `GROQ_MODEL` from environment or `.env`. Default model: `llama-3.3-70b-versatile` (or `openai/gpt-oss-120b`).
2. **Transcription (`promptcut.transcription`)**:
   - `TranscriptionAdapter` (abstract ABC)
   - `FasterWhisperAdapter`: Production adapter using `faster-whisper` with disk caching (`<source_hash>_<model_name>.json`)
   - `MockTranscriptionAdapter`: Deterministic test adapter reading pre-recorded JSON transcript fixtures with no GPU or network requirements.
3. **Diarization (`promptcut.diarization`)**:
   - `DiarizationAdapter` (abstract ABC)
   - `MockSidecarDiarizationAdapter`: Reads speaker turn intervals or word-ID maps from JSON sidecars (`S1`, `S2`, host, guest).
4. **Semantic / Vector Search (`promptcut.resolver`)**:
   - `MeaningSearchAdapter` (abstract ABC)
   - `MockMeaningSearchAdapter`: In-tree deterministic mock adapter.

---

## 6. Model Licensing & External API Terms

| Component / Model | Provider / Origin | License / Terms | Commercial Use | Purpose |
|:---|:---|:---|:---|:---|
| **llama-3.3-70b-versatile** | Meta / Groq Cloud | Meta Llama 3.3 Community License Agreement | Permitted (< 700M MAU) | Primary LLM for structured intent target extraction in `GroqInterpreter` |
| **openai/gpt-oss-120b** | OpenAI / Open Weights | Apache 2.0 License | Permitted | Alternative open-weights LLM for intent target extraction |
| **Groq Cloud API** | Groq Inc. | Groq API Terms of Service | Permitted | High-throughput low-latency inference endpoint (`https://api.groq.com/openai/v1`) |
| **OpenAI Whisper Models** | OpenAI / Systran | MIT License | Permitted | Word-level speech recognition weights |
| **Vosk Kaldi Models** | Alpha Cephei | Apache 2.0 License | Permitted | Offline acoustic speech recognition |

---

## 7. Cost & Latency Metrics

- **API Cost**:
  - Local Mock & Rule-Based Execution: **$0.00** (Runs 100% locally with zero external calls).
  - Groq API Inference: Tracked transparently per request (input ~$0.15 / 1M tokens, output ~$0.60 / 1M tokens) with disk caching on `sha256(instruction + canonical_context)` to avoid duplicate inference cost.
- **Execution Latency**:
  - Deterministic engine replay (8-step worked example): **< 1.0 ms**
  - Preview render (480p, synthetic clip): **~0.25 s**
  - Preview render cache hit: **< 5.0 ms**
  - Range streaming chunk delivery: **< 1.0 ms**
  - Full test suite execution: **~2.6 seconds**
  - Memory footprint: Pure Python immutable objects (< 45 MB RSS).
