"""
End-to-end tests that really download from YouTube.
Run with:  python -m pytest --network
"""
import shutil
import subprocess
import threading

import pytest

import downloader_core as core
from downloader_core import DownloadOptions

pytestmark = pytest.mark.network

# "Me at the zoo" – 19 seconds, the first video on YouTube; stable and tiny.
URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def duration_of(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        pytest.skip("ffprobe not available")
    out = subprocess.check_output([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(path)], text=True)
    return float(out.strip())


def test_extract_info_real():
    info = core.extract_info(URL)
    assert info.title == "Me at the zoo"
    assert 15 <= info.duration <= 25
    assert not info.is_playlist


def test_audio_download_with_trim(tmp_path):
    path = core.perform_download(DownloadOptions(
        url=URL, mode="audio", audio_fmt="m4a", start_time="5", end_time="0:10", output_dir=tmp_path))
    assert path.suffix == ".m4a" and path.exists()
    assert abs(duration_of(path) - 5) < 0.6


def test_video_download_end_only_trim(tmp_path):
    path = core.perform_download(DownloadOptions(
        url=URL, mode="video", resolution="360p", end_time="8", output_dir=tmp_path))
    assert path.suffix == ".mp4"
    assert abs(duration_of(path) - 8) < 1.0


def test_cancel_cleans_up(tmp_path):
    ev = threading.Event()

    def on_progress(p):
        if p.status == "downloading":
            ev.set()

    with pytest.raises(core.DownloadCancelled):
        core.perform_download(DownloadOptions(url=URL, mode="video", resolution="auto",
                                              output_dir=tmp_path), on_progress, cancel_event=ev)
    assert not any(p.suffix in (".part", ".ytdl") for p in tmp_path.rglob("*"))
