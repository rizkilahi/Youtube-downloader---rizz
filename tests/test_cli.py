"""Tests for the CLI front-end (download engine is mocked)."""
import pytest

import downloader_core as core
import yt_cli_downloader as cli
from downloader_core import MediaInfo


@pytest.mark.parametrize("raw,count,expected", [
    ("", 3, [0, 1, 2]),
    ("all", 3, [0, 1, 2]),
    ("1,3", 3, [0, 2]),
    ("3,1,1", 3, [0, 2]),
    ("2-4", 5, [1, 2, 3]),
    ("4-2", 5, [1, 2, 3]),
    ("1, 5-6, 9", 6, [0, 4, 5]),
])
def test_parse_selection(raw, count, expected):
    assert cli.parse_selection(raw, count) == expected


@pytest.mark.parametrize("raw", ["x", "1-a", "9", "0"])
def test_parse_selection_invalid(raw):
    with pytest.raises(ValueError):
        cli.parse_selection(raw, 3)


def test_parser_flags():
    args = cli.build_parser().parse_args(["https://youtu.be/x", "--audio", "--start", "0:10"])
    assert args.audio == "mp3" and args.start == "0:10"
    args = cli.build_parser().parse_args(["https://youtu.be/x", "--audio", "opus", "--res", "720p"])
    assert args.audio == "opus"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["u", "--res", "999p"])


def _playlist():
    return MediaInfo(
        url="pl", title="My PL", thumbnail="", uploader="u", duration=0, duration_str="",
        views=None, is_playlist=True, playlist_count=3,
        entries=[{"url": f"https://youtu.be/{i}", "title": f"T{i}", "duration_str": "00:01"}
                 for i in range(1, 4)],
        available_resolutions=list(core.RESOLUTIONS),
    )


def test_non_interactive_playlist_selection(monkeypatch, media_dirs, capsys):
    calls = []
    monkeypatch.setattr(core, "extract_info", lambda url: _playlist())
    monkeypatch.setattr(core, "perform_download",
                        lambda opts, progress_callback=None: calls.append(opts) or core.AUDIO_DIR / "f")
    args = cli.build_parser().parse_args(["pl", "--audio", "m4a", "--items", "1,3", "--end", "0:20"])
    assert cli.run(args) == 0
    assert [c.url for c in calls] == ["https://youtu.be/1", "https://youtu.be/3"]
    assert all(c.mode == "audio" and c.audio_fmt == "m4a" and c.playlist_folder == "My PL"
               and c.end_time == "0:20" for c in calls)


def test_non_interactive_reports_failures(monkeypatch, media_dirs):
    monkeypatch.setattr(core, "extract_info", lambda url: _playlist())

    def flaky(opts, progress_callback=None):
        if opts.url.endswith("2"):
            raise RuntimeError("boom")
        return core.VIDEOS_DIR / "ok.mp4"

    monkeypatch.setattr(core, "perform_download", flaky)
    args = cli.build_parser().parse_args(["pl", "--res", "480p"])
    assert cli.run(args) == 1


def test_invalid_time_range_rejected(monkeypatch, media_dirs):
    monkeypatch.setattr(core, "extract_info", lambda url: _playlist())
    monkeypatch.setattr(core, "perform_download", lambda *a, **k: pytest.fail("should not download"))
    args = cli.build_parser().parse_args(["pl", "--start", "1:00", "--end", "0:10"])
    assert cli.run(args) == 2


def test_interactive_single_video(monkeypatch, media_dirs):
    info = MediaInfo(url="https://youtu.be/v", title="Vid", thumbnail="", uploader="u",
                     duration=60, duration_str="01:00", views=1,
                     available_resolutions=["auto", "720p", "480p"])
    monkeypatch.setattr(core, "extract_info", lambda url: info)
    calls = []
    monkeypatch.setattr(core, "perform_download",
                        lambda opts, progress_callback=None: calls.append(opts) or core.VIDEOS_DIR / "v.mp4")
    answers = iter([
        "https://youtu.be/v",  # URL
        "1",                   # Video
        "2",                   # 720p
        "2",                   # specific range
        "bad",                 # invalid start -> re-asked
        "0:05",                # start
        "0:15",                # end
    ])
    args = cli.build_parser().parse_args([])
    assert cli.run(args, input_fn=lambda prompt: next(answers)) == 0
    (opts,) = calls
    assert (opts.mode, opts.resolution, opts.start_time, opts.end_time) == ("video", "720p", "0:05", "0:15")
