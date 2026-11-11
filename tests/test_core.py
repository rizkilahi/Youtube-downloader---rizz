"""Unit tests for downloader_core (no network)."""
import threading

import pytest

import downloader_core as core
from downloader_core import DownloadOptions


# --- formatting -------------------------------------------------------------

@pytest.mark.parametrize("size,expected", [
    (None, "0 B"), (0, "0 B"), (512, "512.0 B"), (2048, "2.0 KB"),
    (5 * 1024 ** 2, "5.0 MB"), (3 * 1024 ** 3, "3.0 GB"),
])
def test_format_bytes(size, expected):
    assert core.format_bytes(size) == expected


@pytest.mark.parametrize("secs,expected", [
    (None, "--:--"), (-1, "--:--"), (0, "00:00"), (59, "00:59"), (90, "01:30"), (3661, "01:01:01"),
])
def test_format_seconds(secs, expected):
    assert core.format_seconds(secs) == expected


def test_format_hms():
    assert core.format_hms(0) == "00:00:00"
    assert core.format_hms(90) == "00:01:30"
    assert core.format_hms(3725.5) == "01:02:05.500"


# --- time parsing -----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("90", 90), ("1:30", 90), ("01:30", 90), ("1:02:03", 3723), (" 0:05 ", 5), ("1.5", 1.5),
    ("", None), (None, None), ("   ", None),
])
def test_parse_time_valid(text, expected):
    assert core.parse_time(text) == expected


@pytest.mark.parametrize("text", ["abc", "1:2:3:4", "1:75", "-5", "1::2", "1:30x"])
def test_parse_time_invalid(text):
    with pytest.raises(ValueError):
        core.parse_time(text)


def test_normalize_time_always_hms():
    # Regression: "90" used to normalize to "01:30" (MM:SS) rather than HH:MM:SS.
    assert core.normalize_time("90") == "00:01:30"
    assert core.normalize_time("2:05") == "00:02:05"
    assert core.normalize_time("") is None


def test_validate_time_range():
    assert core.validate_time_range("0:10", "0:20") == (10, 20)
    assert core.validate_time_range(None, "20") == (None, 20)
    assert core.validate_time_range("5", None) == (5, None)
    with pytest.raises(ValueError, match="after start"):
        core.validate_time_range("0:20", "0:10")
    with pytest.raises(ValueError):
        core.validate_time_range("0:10", "0:10")


# --- format selector --------------------------------------------------------

def test_format_selector_audio():
    assert core.build_format_selector("audio", "1080p") == "bestaudio/best"


def test_format_selector_auto():
    assert core.build_format_selector("video", "auto") == core.FALLBACK_VIDEO_FORMAT


@pytest.mark.parametrize("res,height", list(core.RESOLUTION_HEIGHTS.items()))
def test_format_selector_every_resolution_is_honoured(res, height):
    # Regression: 1440p/2160p were offered but silently fell back to "auto".
    sel = core.build_format_selector("video", res)
    assert f"height={height}" in sel
    assert sel.endswith(core.FALLBACK_VIDEO_FORMAT)


# --- output dir -------------------------------------------------------------

def test_resolve_output_dir(media_dirs):
    audio, videos = media_dirs
    assert core.resolve_output_dir(DownloadOptions(url="x", mode="video")) == videos
    assert core.resolve_output_dir(DownloadOptions(url="x", mode="audio")) == audio
    pl = core.resolve_output_dir(DownloadOptions(url="x", mode="audio", playlist_folder="My: Mix/2024"))
    assert pl.parent == audio and "/" not in pl.name and ":" not in pl.name


# --- extract_info with a fake YoutubeDL -------------------------------------

class FakeYDL:
    result = None
    last_opts = None

    def __init__(self, opts):
        FakeYDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return FakeYDL.result


def test_extract_info_single_video(monkeypatch):
    FakeYDL.result = {
        "title": "Clip", "duration": 125, "uploader": "Chan", "view_count": 10,
        "webpage_url": "https://youtu.be/abc", "thumbnail": "t.jpg",
        "formats": [{"height": 360}, {"height": 1080}, {"height": None}],
    }
    monkeypatch.setattr(core.yt_dlp, "YoutubeDL", FakeYDL)
    info = core.extract_info("https://youtu.be/abc")
    assert not info.is_playlist
    assert info.duration_str == "02:05"
    assert info.available_resolutions == ["auto", "1080p", "720p", "480p", "360p"]


def test_extract_info_playlist_filters_and_uses_flat_mode(monkeypatch):
    FakeYDL.result = {
        "_type": "playlist", "title": "PL", "uploader": "Chan",
        "entries": [
            {"id": "a1", "title": "One", "duration": 60, "url": "a1",
             "thumbnails": [{"url": "s.jpg"}, {"url": "l.jpg"}]},
            None,
            {"id": "p", "title": "[Private video]"},
            {"id": "d", "title": "[Deleted video]"},
            {"id": "b2", "title": "Two", "duration": None,
             "webpage_url": "https://www.youtube.com/watch?v=b2"},
        ],
    }
    monkeypatch.setattr(core.yt_dlp, "YoutubeDL", FakeYDL)
    info = core.extract_info("https://www.youtube.com/playlist?list=X")
    assert FakeYDL.last_opts["extract_flat"] == "in_playlist"  # fast listing
    assert info.is_playlist and info.playlist_count == 2
    first, second = info.entries
    # A bare id in "url" must be turned into a full watch URL.
    assert first["url"] == "https://www.youtube.com/watch?v=a1"
    assert first["thumbnail"] == "l.jpg"
    assert second["index"] == 5 and second["duration"] == 0
    assert info.duration == 60


def test_extract_info_failure_raises(monkeypatch):
    FakeYDL.result = None
    monkeypatch.setattr(core.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(ValueError):
        core.extract_info("https://youtu.be/nope")


# --- perform_download validation & cancellation ------------------------------

def test_perform_download_rejects_bad_options(media_dirs):
    with pytest.raises(ValueError):
        core.perform_download(DownloadOptions(url="x", mode="gif"))
    with pytest.raises(ValueError):
        core.perform_download(DownloadOptions(url="x", mode="audio", audio_fmt="aiff"))
    with pytest.raises(ValueError):
        core.perform_download(DownloadOptions(url="x", resolution="999p"))
    with pytest.raises(ValueError):
        core.perform_download(DownloadOptions(url="x", start_time="0:30", end_time="0:10"))


def test_perform_download_cancelled_before_start(media_dirs, monkeypatch):
    monkeypatch.setattr(core, "ensure_ffmpeg", lambda: pytest.fail("should not reach ffmpeg"))
    ev = threading.Event()
    ev.set()
    with pytest.raises(core.DownloadCancelled):
        core.perform_download(DownloadOptions(url="x"), cancel_event=ev)


def test_perform_download_falls_back_on_format_error(media_dirs, monkeypatch):
    """Regression: with ignoreerrors=True the format fallback could never trigger."""
    audio, videos = media_dirs
    calls = []

    class FailingThenOk(FakeYDL):
        def extract_info(self, url, download=False):
            calls.append(self.opts["format"])
            if len(calls) == 1:
                raise core.DownloadError("ERROR: Requested format is not available")
            out = videos / "Clip_720p.mp4"
            out.write_bytes(b"x" * 2048)
            return {"title": "Clip", "requested_downloads": [{"filepath": str(out)}]}

        def __init__(self, opts):
            self.opts = opts

    monkeypatch.setattr(core.yt_dlp, "YoutubeDL", FailingThenOk)
    monkeypatch.setattr(core, "ensure_ffmpeg", lambda: "ffmpeg")
    statuses = []
    path = core.perform_download(
        DownloadOptions(url="https://youtu.be/x", resolution="720p", title="Clip"),
        progress_callback=lambda p: statuses.append(p.status),
    )
    assert path.name == "Clip_720p.mp4"
    assert calls[0] != calls[1] and calls[1] == core.FALLBACK_VIDEO_FORMAT
    assert statuses[0] == "queued" and statuses[-1] == "finished"


def test_perform_download_reports_error(media_dirs, monkeypatch):
    class AlwaysFails(FakeYDL):
        def extract_info(self, url, download=False):
            raise core.DownloadError("ERROR: Video unavailable")

    monkeypatch.setattr(core.yt_dlp, "YoutubeDL", AlwaysFails)
    monkeypatch.setattr(core, "ensure_ffmpeg", lambda: "ffmpeg")
    seen = []
    with pytest.raises(RuntimeError, match="Video unavailable"):
        core.perform_download(DownloadOptions(url="x", mode="audio"),
                              progress_callback=lambda p: seen.append((p.status, p.error_msg)))
    assert seen[-1][0] == "error" and "Video unavailable" in seen[-1][1]


# --- console safety ----------------------------------------------------------

def test_make_console_safe_on_cp1252(tmp_path):
    """Regression: the server crashed on startup printing emoji to a cp1252 stdout."""
    import os
    import subprocess
    import sys

    code = "import downloader_core as c; c.make_console_safe(); print('\\U0001f680 ok')"
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    out = subprocess.run([sys.executable, "-c", code], cwd=core.BASE_DIR, env=env,
                         capture_output=True)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    assert b"ok" in out.stdout


# --- history ----------------------------------------------------------------

def test_list_downloaded_media(media_dirs):
    audio, videos = media_dirs
    (audio / "song_mp3.mp3").write_bytes(b"a" * 10)
    (audio / "My_Playlist").mkdir()
    (audio / "My_Playlist" / "track.m4a").write_bytes(b"b" * 20)
    (videos / "clip.mp4").write_bytes(b"c" * 30)
    (videos / "notes.txt").write_text("ignored")
    (videos / "clip.clip.tmp.mp4").write_bytes(b"tmp")

    files = core.list_downloaded_media()
    by_name = {f["name"]: f for f in files}
    assert set(by_name) == {"song_mp3.mp3", "track.m4a", "clip.mp4"}
    assert by_name["track.m4a"]["folder"] == "My_Playlist"
    assert by_name["track.m4a"]["media_rel"] == "My_Playlist/track.m4a"
    assert by_name["clip.mp4"]["media_type"] == "video"
    assert by_name["song_mp3.mp3"]["folder"] is None
