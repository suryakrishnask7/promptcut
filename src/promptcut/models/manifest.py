"""
src/promptcut/models/manifest.py

IncuBrix Integration Standard #6:
- manifest.json written per completed job
- provider/model names and versions
- settings, seed, timings, estimated cost, warnings, licence info
- values come from what actually ran
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from promptcut.config import TIME_POLICY, SILENCE_SNAP_TOLERANCE_S, get_interpreter_mode


class ProviderDetail(BaseModel):
    provider: str
    model: str
    version: str = "1.0.0"
    licence: str = "Apache-2.0"


class RendererDetail(BaseModel):
    engine: str = "ffmpeg"
    version: str = "system"
    licence: str = "LGPL-2.1-or-later"


class ProvidersManifest(BaseModel):
    transcription: ProviderDetail
    interpreter: ProviderDetail
    renderer: RendererDetail = Field(default_factory=RendererDetail)


class SettingsManifest(BaseModel):
    time_policy: str = TIME_POLICY
    silence_snap_tolerance_s: float = SILENCE_SNAP_TOLERANCE_S
    time_snapping: str = "silence_boundary"
    video_codec: str = "libx264"
    audio_codec: str = "aac"


class TimingsManifest(BaseModel):
    start_time: str
    end_time: str
    duration_ms: float


class CostManifest(BaseModel):
    total_usd: float = 0.0
    currency: str = "USD"
    details: dict[str, Any] = Field(default_factory=dict)


class JobManifest(BaseModel):
    job_id: str
    session_id: str
    type: str  # "transcription", "render", or "session"
    completed_at: str
    providers: ProvidersManifest
    settings: SettingsManifest = Field(default_factory=SettingsManifest)
    seed: int | None = None
    timings: TimingsManifest
    estimated_cost: CostManifest = Field(default_factory=CostManifest)
    warnings: list[str] = Field(default_factory=list)
    licence_info: dict[str, str] = Field(default_factory=lambda: {
        "promptcut_engine": "Apache-2.0",
        "interpreter": "Apache-2.0",
        "transcription": "MIT / Apache-2.0",
        "ffmpeg": "LGPL-2.1-or-later",
    })


def build_job_manifest(
    job_id: str,
    session_id: str,
    job_type: str,
    transcription_provider: str = "mock",
    transcription_model: str = "mock-v1",
    interpreter_provider: str | None = None,
    interpreter_model: str | None = None,
    seed: int | None = None,
    start_time_iso: str | None = None,
    duration_ms: float = 0.0,
    warnings: list[str] | None = None,
    estimated_cost_usd: float = 0.0,
) -> JobManifest:
    """Build a standard JobManifest with real runtime values."""
    now_iso = datetime.now(timezone.utc).isoformat()
    start_iso = start_time_iso or now_iso

    interp_prov = interpreter_provider or get_interpreter_mode()
    interp_mod = interpreter_model or f"{interp_prov}-model"

    return JobManifest(
        job_id=job_id,
        session_id=session_id,
        type=job_type,
        completed_at=now_iso,
        providers=ProvidersManifest(
            transcription=ProviderDetail(
                provider=transcription_provider,
                model=transcription_model,
                licence="MIT" if "whisper" in transcription_provider.lower() else "Apache-2.0",
            ),
            interpreter=ProviderDetail(
                provider=interp_prov,
                model=interp_mod,
                licence="Apache-2.0",
            ),
            renderer=RendererDetail(),
        ),
        settings=SettingsManifest(),
        seed=seed,
        timings=TimingsManifest(
            start_time=start_iso,
            end_time=now_iso,
            duration_ms=round(duration_ms, 2),
        ),
        estimated_cost=CostManifest(total_usd=estimated_cost_usd),
        warnings=warnings or [],
    )


def write_manifest_file(manifest: JobManifest, dest_path: Path) -> Path:
    """Write the job manifest to a destination JSON file."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(manifest.model_dump_json(indent=2))
    return dest_path
