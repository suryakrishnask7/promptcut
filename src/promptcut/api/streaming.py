"""
HTTP Range-Request Streaming for Video Scrubbing and Seeking.

Implements RFC 7233 partial content responses (HTTP 206) for MP4 video streaming,
enabling HTML5 <video> players to seek and scrub seamlessly without buffering
the full file on every position change.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from fastapi import Request, Response
from fastapi.responses import FileResponse, StreamingResponse


def stream_video_with_range(
    file_path: str | Path,
    request: Request,
    media_type: str = "video/mp4",
) -> Response:
    """
    Stream a video file supporting HTTP Range requests for seeking.

    Parameters
    ----------
    file_path : str | Path
        Path to the media file on disk.
    request : Request
        Incoming FastAPI/Starlette request containing optional 'Range' header.
    media_type : str
        MIME type of the media (default: "video/mp4").

    Returns
    -------
    Response
        HTTP 206 Partial Content or HTTP 200 FileResponse with Accept-Ranges header.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        return Response(content="File not found", status_code=404)

    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    # If no Range header, return standard full file response with Accept-Ranges
    if not range_header or not range_header.startswith("bytes="):
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )

    # Parse byte range (e.g. "bytes=1048576-" or "bytes=0-1048575")
    range_val = range_header.replace("bytes=", "").strip()
    parts = range_val.split("-")
    start = int(parts[0]) if parts[0] else 0
    end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

    if start >= file_size or end >= file_size or start > end:
        return Response(
            content="Requested range not satisfiable",
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1

    def file_chunk_generator(start_byte: int, total_bytes: int, chunk_size: int = 128 * 1024) -> Generator[bytes, None, None]:
        bytes_remaining = total_bytes
        with open(path, "rb") as f:
            f.seek(start_byte)
            while bytes_remaining > 0:
                read_amount = min(chunk_size, bytes_remaining)
                chunk = f.read(read_amount)
                if not chunk:
                    break
                bytes_remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": media_type,
    }

    return StreamingResponse(
        file_chunk_generator(start, content_length),
        status_code=206,
        headers=headers,
    )
