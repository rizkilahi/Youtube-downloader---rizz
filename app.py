#!/usr/bin/env python3
"""
app.py
FastAPI web server & background queue for YouTube Downloader.
Provides REST endpoints, WebSocket for real-time progress, and serves modern SPA.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

import uvicorn
import yt_dlp
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

import downloader_core as core
from downloader_core import DownloadCancelled, DownloadOptions, DownloadProgress

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

TEMPLATES_DIR = core.BASE_DIR / "templates"
INDEX_HTML = TEMPLATES_DIR / "index.html"

ACTIVE_STATUSES = {"queued", "downloading", "processing"}
TERMINAL_STATUSES = {"finished", "error", "cancelled"}

executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dl_worker")
jobs_lock = threading.Lock()
jobs: Dict[str, Dict[str, Any]] = {}
cancel_events: Dict[str, threading.Event] = {}
active_connections: Set[WebSocket] = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()
    yield
    for ev in list(cancel_events.values()):
        ev.set()


# No CORS middleware on purpose: the UI is served from the same origin, and allowing
# every origin would let any website drive this local API.
app = FastAPI(title="YouTube Downloader Pro", version="2.1.0", lifespan=lifespan)


class ConnectionManager:
    @staticmethod
    async def connect(websocket: WebSocket) -> None:
        await websocket.accept()
        active_connections.add(websocket)
        with jobs_lock:
            all_jobs = [j.copy() for j in jobs.values()]
        await websocket.send_json({"type": "init", "jobs": all_jobs})

    @staticmethod
    def disconnect(websocket: WebSocket) -> None:
        active_connections.discard(websocket)

    @staticmethod
    def broadcast_sync(message: Dict[str, Any]) -> None:
        """Thread-safe broadcast, callable from worker threads and the event loop."""
        if not active_connections or not main_loop or main_loop.is_closed():
            return

        async def _send() -> None:
            for ws in list(active_connections):
                try:
                    await ws.send_json(message)
                except Exception:
                    active_connections.discard(ws)

        asyncio.run_coroutine_threadsafe(_send(), main_loop)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class UrlRequest(BaseModel):
    url: str


class DownloadItem(BaseModel):
    url: str = Field(min_length=1)
    title: Optional[str] = None
    mode: Literal["video", "audio"] = "video"
    resolution: str = "1080p"
    audio_fmt: str = "mp3"
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    subtitles: bool = False
    subtitle_lang: str = "en"
    playlist_folder: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @field_validator("resolution")
    @classmethod
    def _check_resolution(cls, v: str) -> str:
        if v not in core.RESOLUTIONS:
            raise ValueError(f"Resolution must be one of {', '.join(core.RESOLUTIONS)}")
        return v

    @field_validator("audio_fmt")
    @classmethod
    def _check_audio_fmt(cls, v: str) -> str:
        if v not in core.AUDIO_FORMATS:
            raise ValueError(f"Audio format must be one of {', '.join(core.AUDIO_FORMATS)}")
        return v

    @model_validator(mode="after")
    def _check_times(self) -> "DownloadItem":
        core.validate_time_range(self.start_time, self.end_time)  # raises ValueError -> 422
        self.start_time = (self.start_time or "").strip() or None
        self.end_time = (self.end_time or "").strip() or None
        return self


class BatchDownloadRequest(BaseModel):
    items: List[DownloadItem]


class OpenFolderRequest(BaseModel):
    type: Literal["video", "audio"] = "video"
    folder: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def media_base(media_type: str) -> Path:
    if media_type == "audio":
        return core.AUDIO_DIR
    if media_type in ("video", "videos"):
        return core.VIDEOS_DIR
    raise HTTPException(status_code=404, detail="Unknown media type")


def safe_media_path(media_type: str, relative: Optional[str]) -> Path:
    """Resolve a path inside the media directory, rejecting traversal attempts."""
    base = media_base(media_type).resolve()
    target = (base / relative).resolve() if relative else base
    if not target.is_relative_to(base):
        raise HTTPException(status_code=403, detail="Forbidden")
    return target


def _update_job(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return None
        job.update(fields)
        return job.copy()


# ---------------------------------------------------------------------------
# Background download worker
# ---------------------------------------------------------------------------

def run_download_job(job_id: str, item: DownloadItem) -> None:
    """Worker task executed in the thread pool."""
    cancel_event = cancel_events.get(job_id) or threading.Event()

    def on_progress(p: DownloadProgress) -> None:
        if p.status in TERMINAL_STATUSES:
            return  # final state is published below with extra fields
        updated = _update_job(
            job_id,
            title=p.title,
            status=p.status,
            percent=p.percent,
            downloaded_bytes=p.downloaded_bytes,
            total_bytes=p.total_bytes,
            speed_str=p.speed_str,
            eta_str=p.eta_str,
        )
        if updated:
            ConnectionManager.broadcast_sync({"type": "progress", "job": updated})

    options = DownloadOptions(
        url=item.url,
        title=item.title,
        mode=item.mode,
        resolution=item.resolution,
        audio_fmt=item.audio_fmt,
        start_time=item.start_time,
        end_time=item.end_time,
        subtitles=item.subtitles,
        subtitle_lang=item.subtitle_lang,
        playlist_folder=item.playlist_folder,
    )

    try:
        if cancel_event.is_set():
            raise DownloadCancelled("Download cancelled by user")
        output_file = core.perform_download(
            options=options,
            progress_callback=on_progress,
            job_id=job_id,
            cancel_event=cancel_event,
        )
        final = _update_job(
            job_id,
            status="finished",
            percent=100.0,
            speed_str="",
            eta_str="",
            output_path=str(output_file),
            filename=output_file.name,
            finished_at=time.time(),
        )
        msg_type = "finished"
    except DownloadCancelled:
        final = _update_job(
            job_id, status="cancelled", error_msg="Cancelled", speed_str="", eta_str="",
            finished_at=time.time(),
        )
        msg_type = "cancelled"
    except Exception as exc:
        final = _update_job(
            job_id, status="error", error_msg=str(exc), speed_str="", eta_str="",
            finished_at=time.time(),
        )
        msg_type = "error"
    finally:
        cancel_events.pop(job_id, None)

    if final:
        ConnectionManager.broadcast_sync({"type": msg_type, "job": final})


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def serve_index():
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=404, detail="Frontend template not found")
    return FileResponse(INDEX_HTML, media_type="text/html")


@app.get("/api/system")
async def get_system_status():
    ffmpeg_path = await asyncio.to_thread(core.find_ffmpeg)
    return {
        "ffmpeg": {"available": bool(ffmpeg_path), "path": ffmpeg_path},
        "yt_dlp": {"version": yt_dlp.version.__version__},
        "directories": {"audio": str(core.AUDIO_DIR), "videos": str(core.VIDEOS_DIR)},
        "options": {
            "resolutions": list(core.RESOLUTIONS),
            "audio_formats": list(core.AUDIO_FORMATS),
        },
    }


@app.post("/api/info")
async def fetch_info(req: UrlRequest):
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")
    try:
        info = await asyncio.to_thread(core.extract_info, url)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {
        "success": True,
        "data": {
            "url": info.url,
            "title": info.title,
            "thumbnail": info.thumbnail,
            "uploader": info.uploader,
            "duration": info.duration,
            "duration_str": info.duration_str,
            "views": info.views,
            "is_playlist": info.is_playlist,
            "playlist_count": info.playlist_count,
            "entries": info.entries,
            "available_resolutions": info.available_resolutions,
        },
    }


@app.post("/api/download")
async def queue_downloads(req: BatchDownloadRequest):
    if not req.items:
        raise HTTPException(status_code=400, detail="No items to download")

    queued: List[Dict[str, Any]] = []
    for item in req.items:
        job_id = uuid.uuid4().hex[:8]
        job_data = {
            "id": job_id,
            "title": item.title or "Initializing...",
            "url": item.url,
            "mode": item.mode,
            "resolution": item.resolution,
            "audio_fmt": item.audio_fmt,
            "start_time": item.start_time,
            "end_time": item.end_time,
            "playlist_folder": item.playlist_folder,
            "status": "queued",
            "percent": 0.0,
            "speed_str": "",
            "eta_str": "",
            "error_msg": "",
            "output_path": None,
            "filename": None,
            "created_at": time.time(),
            "finished_at": None,
        }
        with jobs_lock:
            jobs[job_id] = job_data
            cancel_events[job_id] = threading.Event()
            queued.append(job_data.copy())
        executor.submit(run_download_job, job_id, item)

    ConnectionManager.broadcast_sync({"type": "queued", "jobs": queued})
    return {"success": True, "queued_count": len(queued), "job_ids": [j["id"] for j in queued]}


@app.get("/api/jobs")
async def get_jobs():
    with jobs_lock:
        return {"jobs": [j.copy() for j in jobs.values()]}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] not in ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail=f"Job already {job['status']}")
        event = cancel_events.get(job_id)
    if event:
        event.set()
    return {"success": True, "job_id": job_id}


@app.delete("/api/jobs")
async def clear_finished_jobs():
    with jobs_lock:
        removed = [jid for jid, j in jobs.items() if j["status"] in TERMINAL_STATUSES]
        for jid in removed:
            jobs.pop(jid, None)
    ConnectionManager.broadcast_sync({"type": "cleared", "job_ids": removed})
    return {"success": True, "removed": removed}


@app.get("/api/history")
async def get_history():
    files = await asyncio.to_thread(core.list_downloaded_media)
    return {"files": files}


@app.post("/api/open-folder")
async def api_open_folder(req: OpenFolderRequest):
    target = safe_media_path(req.type, req.folder)
    if target.exists() and not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a folder")
    success = await asyncio.to_thread(core.open_folder, target)
    return {"success": success, "path": str(target)}


@app.get("/api/files/{media_type}/{filename:path}")
async def serve_file(media_type: str, filename: str):
    file_path = safe_media_path(media_type, filename)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=file_path.name)


@app.delete("/api/files/{media_type}/{filename:path}")
async def delete_file(media_type: str, filename: str):
    file_path = safe_media_path(media_type, filename)
    if not file_path.is_file() or file_path.suffix.lower() not in core.MEDIA_EXTENSIONS:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        file_path.unlink()
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"Could not delete file: {e}")
    return {"success": True, "deleted": file_path.name}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ConnectionManager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        ConnectionManager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Server starter & browser launch
# ---------------------------------------------------------------------------

def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) != 0


def find_free_port(start_port: int = 8000, host: str = "127.0.0.1") -> int:
    for port in range(start_port, min(start_port + 100, 65535)):
        if is_port_free(port, host):
            return port
    return start_port


def open_browser(url: str, delay: float = 1.0) -> None:
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"Could not open browser automatically: {e}")


def start_server(host: str = "127.0.0.1", port: int = 8000, open_in_browser: bool = True) -> None:
    """Launch the Web UI server and (optionally) open the browser."""
    free_port = find_free_port(port, host)
    url = f"http://{host}:{free_port}"

    core.make_console_safe()
    print(f"\n🚀 Starting YouTube Downloader Web UI on {url}")
    print("💡 Press Ctrl+C in terminal to stop the server\n")

    # Find/install ffmpeg up front so the first download doesn't stall.
    threading.Thread(target=core.ensure_ffmpeg, daemon=True).start()
    if open_in_browser:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    uvicorn.run(app, host=host, port=free_port, log_level="info")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="YouTube Downloader Pro – Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")
    args = parser.parse_args()
    start_server(args.host, args.port, open_in_browser=not args.no_browser)
