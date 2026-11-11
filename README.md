# 🎬 YouTube Downloader Pro

A modern, fast, and feature-rich YouTube Downloader powered by **`yt-dlp`** and **FFmpeg**. Features both an interactive **Terminal CLI** and a sleek **Web UI Dashboard** with real-time download progress tracking.

---

## 🌟 Highlights

- **Dual-Mode** (both share one engine, `downloader_core.py`):
  - 🖥️ **Modern Web Dashboard**: Zero-setup single-page app (FastAPI + Tailwind CSS + Lucide Icons) with video previews, real-time WebSocket progress bars, cancel buttons, in-browser media player, file deletion and one-click folder opening.
  - 💻 **CLI**: Interactive prompts *or* fully scriptable flags, with a live progress bar.
- **Video & Audio**:
  - 2160p, 1440p, 1080p, 720p, 480p, 360p or auto, merged to MP4 via FFmpeg.
  - Audio extraction to MP3 (192 kbps), M4A (AAC), Opus, WAV or FLAC.
- **Playlists & Shorts**:
  - Download complete playlists or pick individual videos (web checkboxes, or `--items 1,3,5-7`).
  - Playlists automatically organized into dedicated named folders.
- **Time Clipping / Trimming**:
  - Download only a section (e.g. `00:01:30` to `00:03:00`). Either bound may be left empty: "from 1:30 to the end" or "from the start to 0:45".
- **Subtitles**: fetch and embed English / auto-generated subtitles (a subtitle failure never blocks the video).
- **Robust Fallbacks**: retries plus automatic format fallback for restricted (SABR) streams.

---

## 🚀 Quick Start

### 1. Launch the Web UI

Double-click **`start_web.bat`** on Windows, or run in terminal:

```bash
python app.py                    # opens http://127.0.0.1:8000 in your browser
python app.py --port 9000 --no-browser
```

Alternatively, from the CLI launcher:
```bash
python yt_cli_downloader.py --web
```

---

### 2. Run the CLI

**Interactive** (no arguments):
```bash
python yt_cli_downloader.py
```

**Non-interactive** (handy for scripts):
```bash
# 720p video
python yt_cli_downloader.py "https://www.youtube.com/watch?v=..." --res 720p

# MP3 of a section only
python yt_cli_downloader.py "https://youtu.be/..." --audio mp3 --start 1:30 --end 3:00

# Items 1, 3 and 5–7 of a playlist as M4A, into a custom folder
python yt_cli_downloader.py "https://www.youtube.com/playlist?list=..." --audio m4a --items 1,3,5-7 -o D:/Music
```

| Flag | Meaning |
| --- | --- |
| `--audio [fmt]` | Audio only (`mp3` default; `m4a`, `opus`, `wav`, `flac`) |
| `--res R` | Video resolution (`auto`, `2160p` … `360p`; default `1080p`) |
| `--start T` / `--end T` | Clip range (`SS`, `MM:SS` or `HH:MM:SS`) |
| `--subs` / `--sub-lang xx` | Embed subtitles (video only) |
| `--items 1,3,5-7` | Playlist selection (default: all) |
| `-o DIR` | Custom output folder |
| `--web` | Launch the Web UI |
| `--update` | Upgrade yt-dlp and exit |

Run `--update` whenever YouTube downloads start failing; most breakages are fixed by a newer yt-dlp.

---

## 🔌 HTTP API (used by the Web UI)

| Method & path | Purpose |
| --- | --- |
| `GET /api/system` | FFmpeg / yt-dlp status and supported options |
| `POST /api/info` | Inspect a video or playlist URL |
| `POST /api/download` | Queue one or more downloads (validated: mode, resolution, format, time range) |
| `GET /api/jobs` | Current queue |
| `POST /api/jobs/{id}/cancel` | Cancel a queued or running download |
| `DELETE /api/jobs` | Clear finished / failed / cancelled jobs |
| `GET /api/history` | Files in `audio/` and `videos/` |
| `GET` / `DELETE /api/files/{audio\|video}/{path}` | Stream or delete a downloaded file |
| `POST /api/open-folder` | Open a media folder in the file explorer |
| `WS /ws` | Real-time job updates |

The server only listens on `127.0.0.1`, sends no CORS headers (other websites can't call it), and rejects any path outside `audio/` and `videos/`.

---

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
python -m pytest              # fast offline suite (download engine mocked)
python -m pytest --network    # also downloads a real 19-second video from YouTube
```

---

## 📁 File Structure

```text
Youtube-downloader---rizz/
├── app.py                  # FastAPI server, WebSocket manager & task queue
├── downloader_core.py      # Core download engine, metadata extractor & FFmpeg helper
├── yt_cli_downloader.py    # Command-line front-end (interactive + flags)
├── start_web.bat           # 1-click Windows launcher
├── requirements.txt        # Runtime dependencies
├── requirements-dev.txt    # + pytest / httpx for the test suite
├── tests/                  # pytest suite (core, API, CLI, network integration)
├── templates/
│   └── index.html          # Dark-mode SPA frontend
├── audio/                  # Downloaded audio files
├── videos/                 # Downloaded video files
└── bin/                    # Local FFmpeg binary (auto-downloaded if not in PATH)
```

---

## 📦 Requirements

- **Python**: 3.9+ (Tested on Python 3.14)
- **FFmpeg**: Automatically detected from system PATH or auto-downloaded into `bin/`
- Dependencies listed in `requirements.txt`: `yt-dlp`, `fastapi`, `uvicorn[standard]`, `colorama`
