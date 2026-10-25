"""
FastAPI Application Entry Point for PromptCut.

Mounts API v1 routes and serves the local Single-Page UI at /ui.
The ui/ directory layout:
  index.html          → GET /ui
  app.js, api.js, …   → GET /ui/<name>.js
  components/*.js     → GET /ui/components/<name>.js
  static/app.css      → GET /ui/static/app.css
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

import uuid
from promptcut.api.errors import register_error_handlers
from promptcut.api.logging_config import (
    configure_structured_logging,
    set_correlation_id,
    set_job_id,
)
from promptcut.api.routes import router as api_v1_router
from promptcut.media.intake import find_executable
from promptcut.config import SESSIONS_DIR, get_interpreter_mode

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
INDEX_HTML = UI_DIR / "index.html"

# Ensure the browser gets correct MIME types for ES modules
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def create_app() -> FastAPI:
    """Create and configure the FastAPI web application."""
    app = FastAPI(
        title="PromptCut – Prompt-Based Video Editing Engine",
        description="Backend API and local interactive UI for conversational video editing.",
        version="0.3.0",
    )

    # Initialize structured JSON logging
    configure_structured_logging()

    # Enable CORS for local development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # IncuBrix Integration Standard #8: Correlation ID middleware
    @app.middleware("http")
    async def correlation_id_middleware(request, call_next):
        corr_id = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex
        set_correlation_id(corr_id)
        request.state.correlation_id = corr_id

        # Also extract job_id if present in URL path
        path_parts = request.url.path.split("/")
        if "jobs" in path_parts:
            idx = path_parts.index("jobs")
            if idx + 1 < len(path_parts):
                set_job_id(path_parts[idx + 1])
                request.state.job_id = path_parts[idx + 1]

        response = await call_next(request)
        response.headers["X-Correlation-Id"] = corr_id
        return response

    # IncuBrix Integration Standard #4: Idempotency middleware for safe retries
    @app.middleware("http")
    async def idempotency_mw(request, call_next):
        from promptcut.api.idempotency import idempotency_middleware
        return await idempotency_middleware(request, call_next)

    # Register IncuBrix Integration Standard #5 structured error handlers
    register_error_handlers(app)

    # ── IncuBrix Integration Standard #8: Health & Readiness Endpoints ────────
    @app.get("/health")
    def health_check() -> dict[str, str]:
        """Liveness endpoint."""
        return {"status": "ok", "service": "promptcut"}

    @app.get("/ready")
    def ready_check(response: Response) -> dict[str, Any]:
        """Readiness endpoint verifying storage, ffmpeg, and interpreter."""
        # 1. Check storage writable
        try:
            from promptcut.storage import get_storage_adapter
            adapter = get_storage_adapter()
            storage_ok = adapter.is_ready()
            storage_detail = "ok" if storage_ok else "storage backend check failed"
        except Exception as e:
            storage_ok = False
            storage_detail = str(e)

        # 2. Check ffmpeg/ffprobe binary presence
        try:
            ff_bin = find_executable("ffmpeg")
            fp_bin = find_executable("ffprobe")
            ffmpeg_ok = bool(ff_bin and fp_bin)
            ffmpeg_detail = "ok" if ffmpeg_ok else "ffmpeg or ffprobe missing"
        except Exception as e:
            ffmpeg_ok = False
            ffmpeg_detail = str(e)

        # 3. Check interpreter configuration
        try:
            mode = get_interpreter_mode()
            interpreter_ok = True
            interpreter_detail = f"configured ({mode})"
        except Exception as e:
            interpreter_ok = False
            interpreter_detail = str(e)

        is_ready = storage_ok and ffmpeg_ok and interpreter_ok
        status_str = "ready" if is_ready else "not_ready"

        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return {
            "status": status_str,
            "checks": {
                "storage": storage_detail,
                "ffmpeg": ffmpeg_detail,
                "interpreter": interpreter_detail,
            },
        }

    # Mount API v1 router
    app.include_router(api_v1_router)

    # Debug endpoint to verify forced 500 crash handling in test suite
    @app.get("/api/v1/debug/forced-crash")
    def forced_crash() -> None:
        raise RuntimeError("Forced deliberate crash for 500 catch-all error testing")

    # ── UI routes ──────────────────────────────────────────────────────────

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/ui")

    NO_CACHE_HEADERS = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    def serve_ui() -> FileResponse:
        """Serve the SPA entry point."""
        if not INDEX_HTML.is_file():
            return FileResponse(Path(__file__).resolve(), status_code=404)
        return FileResponse(INDEX_HTML, media_type="text/html", headers=NO_CACHE_HEADERS)

    def _serve_file_from_ui(file_path: str) -> FileResponse:
        requested = (UI_DIR / file_path).resolve()
        ui_root = UI_DIR.resolve()
        if not str(requested).startswith(str(ui_root)):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not requested.is_file():
            raise HTTPException(status_code=404, detail=f"UI asset not found: {file_path}")
        media_type, _ = mimetypes.guess_type(str(requested))
        return FileResponse(
            requested,
            media_type=media_type or "application/octet-stream",
            headers=NO_CACHE_HEADERS,
        )

    @app.get("/ui/{file_path:path}", include_in_schema=False)
    def serve_ui_file(file_path: str) -> FileResponse:
        """Serve JS modules, CSS, and other static assets from /ui/."""
        return _serve_file_from_ui(file_path)

    # Root fallbacks for browsers that resolve relative paths against /
    @app.get("/static/{file_path:path}", include_in_schema=False)
    def serve_root_static(file_path: str) -> FileResponse:
        return _serve_file_from_ui(f"static/{file_path}")

    @app.get("/{filename}.js", include_in_schema=False)
    def serve_root_js(filename: str) -> FileResponse:
        return _serve_file_from_ui(f"{filename}.js")

    return app


app = create_app()
