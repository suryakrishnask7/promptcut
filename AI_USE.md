# PromptCut – AI Coding Assistant Disclosure

> **Compliance with IncuBrix Assessment 2 Rules (Brief page 5 & page 7)**  
> Full disclosure of AI tools utilized during development, prompts, architectural ownership, and verification.

---

## 1. Tools Used

- **Antigravity IDE & AI Coding Assistant**: Used for interactive pair programming, test coverage expansion, and documentation generation.
- **Underlying Models**: Claude 3.7 Sonnet (Thinking) & Gemini 3.8 Flash.

---

## 2. Tasks Where AI Was Applied

| Area | Description | Verification Method |
|:---|:---|:---|
| **Phase 1: Data Models** | Scaffolding of Pydantic models for `Word`, `Transcript`, `OperationLog`, and the 12 operation types. | Validated against pydantic v2 strict typing rules and unit tests. |
| **Phase 1: Deterministic Engine** | Pure-Python compilation of `TimelineSegment` and `CaptionOverlay` from `(OperationLog, Transcript)`. | Comprehensive unit tests, determinism replay tests, and float rounding checks. |
| **Phase 1: Resolver Algorithms** | Regular expressions and phrase search logic for spoken time expressions ("a minute twenty", "1:20:05"). | Comprehensive test matrices in `test_resolver.py`. |
| **Phase 1: OpenTimelineIO Exporter** | Standard OTIO 1.0 JSON format generation for NLE interoperability. | Structural validation and schema conformity tests in `test_otio_export.py`. |
| **Phase 2: Media Intake & Immutability** | `ffprobe` wrapper for audio/video stream inspection; SHA-256 calculation and verification before/after sessions. | Tested against synthetic MP4 video clips in `test_intake.py`. |
| **Phase 2: Transcription & Diarization** | Swappable adapters for Faster-Whisper with disk caching (`<hash>_<model>.json`) and JSON sidecar speaker diarization. | Verified against mock and cached transcripts in `test_transcription.py` and `test_diarization.py`. |
| **Phase 2: Silence Snapping** | Tolerance-based snapping to natural inter-word silences, with warning generation for mid-word cuts. | Evaluated with test boundaries in `test_silence_snapping.py`. |
| **Phase 2: FFmpeg Filtergraph Engine** | Building complex FFmpeg filtergraphs with video `trim`/`setpts`, audio `atrim`/`atempo`, volume ducking, and ASS subtitle burn-in. | Frame-accurate duration and A/V sync measurement via `ffprobe` in `test_render.py`. |
| **Phase 2: Session Runner & CLI** | Rule-based natural language parser, multi-step state machine with clarification handling, and CLI interface. | Automated end-to-end sessions in `test_session_runner.py` and `test_cli.py`. |
| **Phase 3: Range Streaming & API** | RFC 7233 partial content HTTP range streaming (`206 Partial Content`) for instant video scrub/seek. | Integration tests in `test_api.py` verifying byte ranges and content-range headers. |
| **Phase 3: Natural Language Interpreter** | Intent target extraction using `GroqInterpreter` (OpenAI-compatible client with `openai/gpt-oss-120b`, Apache 2.0 open weights), with fallback to `RuleInterpreter` and deterministic `MockInterpreter`. Structured JSON output, ~8 few-shot examples, localized context windows, disk caching on `sha256(prompt + context)`. | Tested across diverse phrasings, ambiguous clarification loops, tie-breakers, and offline test suites without API keys. |
| **Phase 3: Thin Local UI (HTML/CSS/JS)** | Zero-framework single-page dashboard: video comparison toggle, interactive transcript with cut/mute/caption styling, conversational cards, segment bar, and light/dark theme. | Verified via browser client testing and automated test client at `/ui`. |
| **Phase 4: Integration Standards (Structured Error Envelope)** | Custom Starlette exception handlers for HTTP 4xx, 422 validation, and unhandled 500 crashes formatting all errors into `{"error": {"code": "UPPER_SNAKE_CASE", "message": "...", "retryable": bool, "details": {}}}`. List of codes documented in README. | Tests first in `tests/test_api_errors.py` including validation formatting and forced crash test. |
| **Phase 4: Integration Standards (Health & Structured Logging)** | Implemented `GET /health` (liveness), `GET /ready` (readiness probing storage, ffmpeg, interpreter), JSON structured logging with timestamp, level, job_id, and `X-Correlation-Id` propagation, plus secret redaction. | Tests first in `tests/test_health_and_logging.py` asserting liveness, readiness, correlation ID echo, and log secrecy (`GROQ_API_KEY` never leaked). |
| **Phase 4: Integration Standards (Asynchronous Jobs & Signed Webhooks)** | Asynchronous background execution for transcription and render returning 202 Accepted + `job_id`, status polling at `GET /api/v1/jobs/{id}`, and signed HMAC-SHA256 webhooks in `X-IncuBrix-Signature` with exponential backoff retries. | Tests first in `tests/test_jobs.py` verifying status transitions (queued, running, succeeded, failed) and webhook signature verification. |
| **Phase 4: Integration Standards (Safe Retries & Idempotency)** | Implemented RFC-style `Idempotency-Key` middleware caching responses for POST `/sessions`, `/instructions`, and `/render`, detecting payload conflicts (409 `IDEMPOTENCY_KEY_REUSED`) and safe replay. | Tests first in `tests/test_idempotency.py` (replay identical responses, 409 on altered payloads). |
| **Phase 4: Integration Standards (Video Input by URL & SSRF Protection)** | Pure standard-library (`socket`, `ipaddress`, `urllib.request`) URL validator and downloader blocking private, loopback, link-local, and reserved IPs, including redirect interceptors and byte-stream size/timeout limits. | Tests first in `tests/test_url_intake.py` verifying scheme checks, localhost, 169.254.x, redirects, and size caps. |
| **Phase 4: Integration Standards (Job Manifest Generation)** | Automated `manifest.json` generation per completed session/job recording runtime provider/model names, versions, settings, seed, timing breakdown, cost estimate, warnings, and software licences. | Tests first in `tests/test_manifest.py` verifying runner and background job manifest output. |
| **Phase 4: Integration Standards (Batch CLI Runner)** | Added `promptcut batch <folder> -o <out>` CLI command running all YAML session files, emitting per-session artifacts and `summary.json`, resiliently continuing past individual session failures. | Tests first in `tests/test_batch_cli.py` verifying multi-session batch execution, continuation past failing sessions, and summary aggregation. |
| **Phase 4: Integration Standards (OpenAPI Schema Export & CI Check)** | Standalone generator `scripts/export_openapi.py` with `--check` CI drift verification; confirmed all API endpoints live under `/api/v1` namespace with UI static routes excluded from schema. | Tests first in `tests/test_openapi_spec.py` asserting `openapi.yaml` presence, schema parity, and prefix constraints. |
| **Phase 4: Integration Standards (Storage Interface & S3/MinIO Backend)** | Abstract `StorageAdapter` interface with `LocalStorageAdapter` and `S3StorageAdapter` selectable via `STORAGE_BACKEND=local|s3|minio`, powering media persistence and readiness health checks. | Tests first in `tests/test_storage.py` verifying CRUD semantics, path traversal guards, mock S3, and backend factory. |
| **Phase 4: Integration Standards (Docker & Compose Orchestration)** | Containerized multi-stage Debian-slim environment with system FFmpeg and curl (`Dockerfile`), complete orchestration service topology with MinIO (`docker-compose.yml`), and environment template (`.env.example`). Configured zero-key offline defaults (`TRANSCRIPTION_ADAPTER=mock`, `RuleInterpreter`) so full test suite executes and passes out of the box. | Verified via `docker compose up` and container execution passing all 255 tests without external keys or network. |

---

## 3. Human Control & Architectural Ownership

- All architectural decisions (Word ID anchoring instead of clock coordinates, derived timeline vs stored timeline, append-only operation log with pointer mechanics, three-way response envelope, swappable provider adapters with offline mock/rule fallbacks, single-file zero-build UI) were designed directly to satisfy the IncuBrix specification.
- Every line of code, test case, and documentation was inspected, executed, and verified.
- The codebase contains no unverified hallucinated imports, no secret API keys, and no non-deterministic logic.
- Automated tests pass 100% locally with zero external network or API key dependencies.
