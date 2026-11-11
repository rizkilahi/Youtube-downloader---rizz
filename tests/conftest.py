import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import downloader_core as core  # noqa: E402


def pytest_addoption(parser):
    parser.addoption(
        "--network", action="store_true", default=False,
        help="Run tests that download real media from YouTube",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--network"):
        return
    skip = pytest.mark.skip(reason="needs --network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def media_dirs(tmp_path, monkeypatch):
    """Point the core's audio/videos folders at a temp dir so tests never touch real files."""
    audio = tmp_path / "audio"
    videos = tmp_path / "videos"
    audio.mkdir()
    videos.mkdir()
    monkeypatch.setattr(core, "BASE_DIR", tmp_path)
    monkeypatch.setattr(core, "AUDIO_DIR", audio)
    monkeypatch.setattr(core, "VIDEOS_DIR", videos)
    return audio, videos
