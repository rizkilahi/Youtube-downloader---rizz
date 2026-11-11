#!/usr/bin/env python3
"""
downloader_core.py
Shared core downloader engine for YouTube Downloader (yt-dlp + FFmpeg).
Powers both the CLI (yt_cli_downloader.py) and the Web UI (app.py).
"""
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, sanitize_filename

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
BIN_DIR = BASE_DIR / "bin"
AUDIO_DIR = BASE_DIR / "audio"
VIDEOS_DIR = BASE_DIR / "videos"

AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

MODES = ("video", "audio")
RESOLUTION_HEIGHTS: Dict[str, int] = {
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}
RESOLUTIONS: Tuple[str, ...] = ("auto", *RESOLUTION_HEIGHTS)
AUDIO_FORMATS: Tuple[str, ...] = ("mp3", "m4a", "opus", "wav", "flac")
MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".wav", ".flac"}
UNAVAILABLE_TITLES = {"[Private video]", "[Deleted video]"}

# Used when the precise resolution chain fails (SABR / missing formats).
FALLBACK_VIDEO_FORMAT = "bv*+ba/b"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

__all__ = [
    "AUDIO_DIR", "AUDIO_FORMATS", "BASE_DIR", "DownloadCancelled", "DownloadOptions",
    "DownloadProgress", "MediaInfo", "MODES", "RESOLUTIONS", "VIDEOS_DIR",
    "build_format_selector", "ensure_ffmpeg", "extract_info", "find_ffmpeg",
    "format_bytes", "format_hms", "format_seconds", "list_downloaded_media",
    "normalize_time", "open_folder", "parse_time", "perform_download",
    "update_yt_dlp", "validate_time_range",
]

# ---------------------------------------------------------------------------
# Dependency verification and system helpers
# ---------------------------------------------------------------------------

_ffmpeg_lock = threading.Lock()
_ffmpeg_cache: Optional[str] = None


def find_ffmpeg() -> Optional[str]:
    """Return the path of an available ffmpeg binary without installing anything."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    local = BIN_DIR / ("ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg")
    if local.exists():
        return str(local)
    return None


def _install_ffmpeg() -> Optional[str]:
    system = platform.system()
    if system == "Linux" and shutil.which("apt-get"):
        subprocess.check_call(["sudo", "apt-get", "update", "-y"])
        subprocess.check_call(["sudo", "apt-get", "install", "-y", "ffmpeg"])
        return shutil.which("ffmpeg")
    if system == "Darwin" and shutil.which("brew"):
        subprocess.check_call(["brew", "install", "ffmpeg"])
        return shutil.which("ffmpeg")
    if system == "Windows":
        BIN_DIR.mkdir(exist_ok=True)
        exe = BIN_DIR / "ffmpeg.exe"
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "ffmpeg.zip"
            urllib.request.urlretrieve(url, archive)  # stream to disk, not RAM
            with zipfile.ZipFile(archive) as zf:
                member = next(m for m in zf.namelist() if m.endswith("/bin/ffmpeg.exe"))
                with zf.open(member) as src, open(exe, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return str(exe)
    return None


def ensure_ffmpeg() -> str:
    """Find ffmpeg, installing a static build if needed. Result is cached."""
    global _ffmpeg_cache
    with _ffmpeg_lock:
        if _ffmpeg_cache:
            return _ffmpeg_cache
        path = find_ffmpeg()
        if not path:
            try:
                path = _install_ffmpeg()
            except Exception as exc:  # network / permission issues
                print(f"Warning: Failed to auto-install FFmpeg: {exc}", file=sys.stderr)
        if path:
            bin_dir = str(Path(path).parent)
            if bin_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            _ffmpeg_cache = path
            return path
        return "ffmpeg"


def make_console_safe() -> None:
    """
    Stop emoji / non-Latin titles from crashing print() when stdout is not UTF-8
    (legacy Windows code pages, or output redirected to a file).
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def update_yt_dlp() -> bool:
    """Upgrade yt-dlp to the latest version via pip."""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def format_bytes(size: Optional[float]) -> str:
    """Format bytes to human-readable string (KB, MB, GB)."""
    if size is None or size <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_seconds(seconds: Optional[float]) -> str:
    """Format seconds into HH:MM:SS or MM:SS (for display)."""
    if seconds is None or seconds < 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_hms(seconds: float) -> str:
    """Format seconds as HH:MM:SS (with .mmm when fractional), as ffmpeg expects."""
    whole = int(seconds)
    frac = seconds - whole
    m, s = divmod(whole, 60)
    h, m = divmod(m, 60)
    out = f"{h:02d}:{m:02d}:{s:02d}"
    if frac > 1e-9:
        out += f".{int(round(frac * 1000)):03d}"
    return out


_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")


def parse_time(value: Optional[str]) -> Optional[float]:
    """
    Parse "SS", "MM:SS" or "HH:MM:SS" (optionally with fractional seconds) into seconds.
    Returns None for empty input; raises ValueError for malformed input.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split(":")]
    if len(parts) > 3 or any(not _NUM_RE.match(p) for p in parts):
        raise ValueError(f"Invalid time '{text}'. Use SS, MM:SS or HH:MM:SS.")
    numbers = [float(p) for p in parts]
    if any(n >= 60 for n in numbers[1:]):
        raise ValueError(f"Invalid time '{text}': minutes/seconds must be below 60.")
    total = 0.0
    for n in numbers:
        total = total * 60 + n
    return total


def normalize_time(value: Optional[str]) -> Optional[str]:
    """Normalize a time string to HH:MM:SS. Raises ValueError when malformed."""
    seconds = parse_time(value)
    return None if seconds is None else format_hms(seconds)


def validate_time_range(
    start: Optional[str], end: Optional[str]
) -> Tuple[Optional[float], Optional[float]]:
    """Parse and validate a clip range. Either bound may be omitted."""
    start_s = parse_time(start)
    end_s = parse_time(end)
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise ValueError("End time must be after start time.")
    if end_s is not None and end_s <= 0:
        raise ValueError("End time must be greater than zero.")
    return start_s, end_s


def open_folder(folder_path: Path | str) -> bool:
    """Open folder in OS file explorer."""
    path = Path(folder_path).resolve()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return True
    except Exception as e:
        print(f"Failed to open folder: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class MediaInfo:
    url: str
    title: str
    thumbnail: str
    uploader: str
    duration: int
    duration_str: str
    views: Optional[int]
    is_playlist: bool = False
    playlist_count: int = 0
    entries: List[Dict[str, Any]] = field(default_factory=list)
    available_resolutions: List[str] = field(default_factory=list)


@dataclass
class DownloadOptions:
    url: str
    mode: str = "video"  # "video" or "audio"
    resolution: str = "1080p"  # see RESOLUTIONS
    audio_fmt: str = "mp3"  # see AUDIO_FORMATS
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    subtitles: bool = False
    subtitle_lang: str = "en"
    playlist_folder: Optional[str] = None
    output_dir: Optional[Path] = None
    title: Optional[str] = None  # known title, shown before yt-dlp reports one


@dataclass
class DownloadProgress:
    job_id: str
    title: str
    status: str  # 'queued', 'downloading', 'processing', 'finished', 'error', 'cancelled'
    percent: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_str: str = ""
    eta_str: str = ""
    error_msg: str = ""
    output_path: Optional[str] = None


class _YdlLogger:
    """Silences yt-dlp console output while remembering error messages."""

    def __init__(self) -> None:
        self.errors: List[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.errors.append(msg)


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def _entry_url(entry: Dict[str, Any]) -> Optional[str]:
    url = entry.get("webpage_url")
    if not url:
        raw = entry.get("url")
        if raw and str(raw).startswith("http"):
            url = raw
    if not url and entry.get("id"):
        url = f"https://www.youtube.com/watch?v={entry['id']}"
    return url


def _entry_thumbnail(entry: Dict[str, Any]) -> str:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    return thumbs[-1].get("url", "") if thumbs else ""


def extract_info(url: str, fetch_playlist_items: bool = False) -> MediaInfo:
    """
    Extract metadata for a video or playlist.

    Playlists are listed with flat extraction (fast) unless fetch_playlist_items=True,
    which resolves every entry fully (slow, but yields complete metadata).
    """
    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "logger": _YdlLogger(),
        "extract_flat": False if fetch_playlist_items else "in_playlist",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        errors = ydl_opts["logger"].errors
        detail = errors[-1] if errors else "Could not retrieve information for this URL."
        raise ValueError(detail.replace("ERROR: ", ""))

    if info.get("_type") == "playlist" or "entries" in info:
        valid_entries = []
        for i, entry in enumerate(info.get("entries") or []):
            if not entry or not entry.get("title") or entry["title"] in UNAVAILABLE_TITLES:
                continue
            entry_url = _entry_url(entry)
            if not entry_url:
                continue
            duration = entry.get("duration") or 0
            valid_entries.append({
                "index": i + 1,
                "id": entry.get("id"),
                "title": entry["title"],
                "duration": duration,
                "duration_str": format_seconds(duration),
                "url": entry_url,
                "thumbnail": _entry_thumbnail(entry),
                "uploader": entry.get("uploader") or entry.get("channel") or "Unknown",
            })

        total_duration = int(sum(e["duration"] for e in valid_entries))
        return MediaInfo(
            url=url,
            title=info.get("title") or "YouTube Playlist",
            thumbnail=_entry_thumbnail(info) or (valid_entries[0]["thumbnail"] if valid_entries else ""),
            uploader=info.get("uploader") or info.get("channel") or "Various Artists",
            duration=total_duration,
            duration_str=format_seconds(total_duration),
            views=None,
            is_playlist=True,
            playlist_count=len(valid_entries),
            entries=valid_entries,
            available_resolutions=list(RESOLUTIONS),
        )

    # Single video
    heights = {f.get("height") for f in info.get("formats") or [] if f.get("height")}
    max_height = max(heights) if heights else 0
    available = ["auto"] + [r for r, h in RESOLUTION_HEIGHTS.items() if max_height >= h]

    duration = info.get("duration") or 0
    return MediaInfo(
        url=info.get("webpage_url") or url,
        title=info.get("title") or "Unknown Title",
        thumbnail=_entry_thumbnail(info),
        uploader=info.get("uploader") or info.get("channel") or "Unknown",
        duration=int(duration),
        duration_str=format_seconds(duration),
        views=info.get("view_count"),
        is_playlist=False,
        playlist_count=0,
        entries=[],
        available_resolutions=available,
    )


# ---------------------------------------------------------------------------
# Download logic
# ---------------------------------------------------------------------------

def build_format_selector(mode: str, resolution: str) -> str:
    """Return the yt-dlp format selector for a mode/resolution combination."""
    if mode == "audio":
        return "bestaudio/best"
    h = RESOLUTION_HEIGHTS.get(resolution)
    if not h:
        return FALLBACK_VIDEO_FORMAT
    return (
        f"bv*[height={h}][vcodec~='^(avc1|h264)']+ba[ext=m4a]/"
        f"bv*[height={h}]+ba/"
        f"bv*[height<={h}][vcodec~='^(avc1|h264)']+ba/"
        f"bv*[height<={h}]+ba/"
        f"b[height<={h}]/"
        f"{FALLBACK_VIDEO_FORMAT}"
    )


def resolve_output_dir(options: DownloadOptions) -> Path:
    if options.output_dir:
        return Path(options.output_dir)
    base = AUDIO_DIR if options.mode == "audio" else VIDEOS_DIR
    if options.playlist_folder:
        return base / sanitize_filename(options.playlist_folder, restricted=True)
    return base


def _time_tag(seconds: Optional[float], default: str) -> str:
    return default if seconds is None else format_hms(seconds).replace(":", "").replace(".", "")


def _trim_media(
    ffmpeg_exe: str, src: Path, start: Optional[float], end: Optional[float], is_audio: bool
) -> bool:
    """Trim src in place. Tries a fast stream copy, then falls back to re-encoding."""
    tmp = src.with_name(f"{src.stem}.clip.tmp{src.suffix}")
    seek: List[str] = []
    if start is not None:
        seek += ["-ss", format_hms(start)]
    if end is not None:
        seek += ["-to", format_hms(end)]
    base = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", *seek, "-i", str(src)]

    if is_audio:
        attempts = [
            base + ["-map", "0:a", "-c", "copy", str(tmp)],
            base + ["-map", "0:a", str(tmp)],  # let ffmpeg choose the encoder by extension
        ]
    else:
        attempts = [
            base + ["-map", "0", "-c", "copy", str(tmp)],
            base + ["-map", "0:v?", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast",
                    "-crf", "18", "-c:a", "aac", str(tmp)],
        ]

    for cmd in attempts:
        rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc == 0 and tmp.exists() and tmp.stat().st_size > 1024:
            os.replace(tmp, src)
            return True
    if tmp.exists():
        tmp.unlink()
    return False


def _locate_output(info: Dict[str, Any], ydl: yt_dlp.YoutubeDL, out_dir: Path) -> Optional[Path]:
    """Find the final file produced by yt-dlp (after merging/post-processing)."""
    for rd in reversed(info.get("requested_downloads") or []):
        fp = rd.get("filepath")
        if fp and Path(fp).exists():
            return Path(fp)
    if info.get("filepath") and Path(info["filepath"]).exists():
        return Path(info["filepath"])
    # Fallback: glob on the expected stem (escaped — titles may contain glob chars).
    stem = Path(ydl.prepare_filename(info)).stem
    pattern = glob.escape(str(out_dir / stem)) + ".*"
    candidates = [
        Path(p) for p in glob.glob(pattern)
        if Path(p).suffix.lower() in MEDIA_EXTENSIONS and ".clip.tmp" not in p
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def perform_download(
    options: DownloadOptions,
    progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    job_id: str = "cli_task",
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """
    Download a single video or audio track and return the path of the final file.

    Raises ValueError for invalid options, DownloadCancelled when cancel_event is set,
    and RuntimeError when the download fails.
    """
    if options.mode not in MODES:
        raise ValueError(f"Invalid mode '{options.mode}'.")
    is_audio = options.mode == "audio"
    if is_audio and options.audio_fmt not in AUDIO_FORMATS:
        raise ValueError(f"Unsupported audio format '{options.audio_fmt}'.")
    if not is_audio and options.resolution not in RESOLUTIONS:
        raise ValueError(f"Unsupported resolution '{options.resolution}'.")
    start_s, end_s = validate_time_range(options.start_time, options.end_time)
    trim = start_s is not None or end_s is not None

    out_dir = resolve_output_dir(options)
    out_dir.mkdir(parents=True, exist_ok=True)

    quality_tag = options.audio_fmt if is_audio else options.resolution
    if trim:
        quality_tag += f"_{_time_tag(start_s, 'start')}-{_time_tag(end_s, 'end')}"

    progress = DownloadProgress(
        job_id=job_id, title=options.title or "Media Download", status="queued"
    )
    temp_files: set[str] = set()

    def emit() -> None:
        if progress_callback:
            progress_callback(progress)

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Download cancelled by user")

    def fail(status: str, message: str) -> None:
        progress.status = status
        progress.error_msg = message
        emit()

    emit()
    check_cancel()
    ffmpeg_exe = ensure_ffmpeg()

    def progress_hook(d: Dict[str, Any]) -> None:
        check_cancel()
        title = (d.get("info_dict") or {}).get("title")
        if title:
            progress.title = title
        for key in ("tmpfilename", "filename"):
            if d.get(key):
                temp_files.add(d[key])

        if d.get("status") == "downloading":
            progress.status = "downloading"
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            progress.downloaded_bytes = downloaded
            progress.total_bytes = total
            if total > 0:
                progress.percent = min(round(downloaded / total * 100, 1), 100.0)
            if d.get("speed"):
                progress.speed_str = f"{format_bytes(d['speed'])}/s"
            if d.get("eta") is not None:
                progress.eta_str = format_seconds(d["eta"])
            emit()
        elif d.get("status") == "finished":
            progress.status = "processing"
            progress.percent = 100.0
            progress.eta_str = ""
            emit()

    def postprocessor_hook(d: Dict[str, Any]) -> None:
        check_cancel()
        if d.get("status") == "started" and progress.status != "processing":
            progress.status = "processing"
            emit()

    logger = _YdlLogger()
    ydl_opts: Dict[str, Any] = {
        "ffmpeg_location": ffmpeg_exe,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": logger,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "user_agent": USER_AGENT,
        "extractor_retries": 3,
        "fragment_retries": 3,
        "retry_sleep_functions": {"http": lambda n: 2 ** n},
        "restrictfilenames": True,
        "windowsfilenames": True,
        "outtmpl": str(out_dir / f"%(title)s_{quality_tag}.%(ext)s"),
        "format": build_format_selector(options.mode, options.resolution),
    }

    if is_audio:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": options.audio_fmt,
            "preferredquality": "192",
        }]
    else:
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]

    if options.subtitles:
        ydl_opts["writesubtitles"] = True
        ydl_opts["writeautomaticsub"] = True
        ydl_opts["subtitleslangs"] = list(dict.fromkeys([options.subtitle_lang, "en"]))
        if not is_audio:
            ydl_opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

    def run(opts: Dict[str, Any]) -> Tuple[Dict[str, Any], yt_dlp.YoutubeDL]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(options.url, download=True)
        if not info:
            raise DownloadError(logger.errors[-1] if logger.errors else "No information returned")
        if info.get("_type") == "playlist":
            raise ValueError("Playlist URLs must be expanded into individual videos first.")
        return info, ydl

    def cleanup_temp() -> None:
        for name in temp_files:
            for path in (Path(name), Path(f"{name}.part"), Path(f"{name}.ytdl")):
                if path.suffix in (".part", ".ytdl") and path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass

    try:
        try:
            info, ydl = run(ydl_opts)
        except DownloadError as first_err:
            message = str(first_err).lower()
            retry_opts = dict(ydl_opts)
            if options.subtitles and "subtitle" in message:
                # Subtitle fetch failures (e.g. HTTP 429) should not kill the media download.
                for key in ("writesubtitles", "writeautomaticsub", "subtitleslangs"):
                    retry_opts.pop(key, None)
                retry_opts["postprocessors"] = [
                    pp for pp in ydl_opts["postprocessors"] if pp["key"] != "FFmpegEmbedSubtitle"
                ]
            elif not is_audio and ("format" in message or "sabr" in message):
                retry_opts["format"] = FALLBACK_VIDEO_FORMAT
            else:
                raise
            check_cancel()
            info, ydl = run(retry_opts)
    except DownloadCancelled:
        cleanup_temp()
        fail("cancelled", "Download cancelled by user")
        raise
    except DownloadError as exc:
        cleanup_temp()
        message = str(exc).replace("ERROR: ", "").strip()
        fail("error", f"Download failed: {message}")
        raise RuntimeError(message) from exc
    except ValueError as exc:
        fail("error", str(exc))
        raise

    final_file = _locate_output(info, ydl, out_dir)
    if not final_file:
        fail("error", "Output file not found after download.")
        raise FileNotFoundError("Downloaded file could not be located.")

    if trim:
        progress.status = "processing"
        emit()
        if not _trim_media(ffmpeg_exe, final_file, start_s, end_s, is_audio):
            fail("error", "FFmpeg could not trim the requested section.")
            raise RuntimeError("FFmpeg could not trim the requested section.")

    progress.status = "finished"
    progress.percent = 100.0
    progress.output_path = str(final_file)
    emit()
    return final_file


# ---------------------------------------------------------------------------
# File explorer / history helper
# ---------------------------------------------------------------------------

def list_downloaded_media() -> List[Dict[str, Any]]:
    """Scan audio/ and videos/ directories for downloaded files."""
    results = []
    for media_dir, media_type in [(AUDIO_DIR, "audio"), (VIDEOS_DIR, "video")]:
        if not media_dir.exists():
            continue
        for file in media_dir.rglob("*"):
            if not file.is_file() or file.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if ".clip.tmp" in file.name:
                continue
            stat = file.stat()
            results.append({
                "name": file.name,
                "rel_path": file.relative_to(BASE_DIR).as_posix() if file.is_relative_to(BASE_DIR) else file.name,
                "media_rel": file.relative_to(media_dir).as_posix(),
                "full_path": str(file),
                "media_type": media_type,
                "size_bytes": stat.st_size,
                "size_str": format_bytes(stat.st_size),
                "modified_time": stat.st_mtime,
                "folder": file.parent.relative_to(media_dir).as_posix() if file.parent != media_dir else None,
            })

    results.sort(key=lambda x: x["modified_time"], reverse=True)
    return results
