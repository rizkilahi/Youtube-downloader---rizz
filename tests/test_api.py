"""API tests for app.py using FastAPI's TestClient (download engine is mocked)."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as app_module
import downloader_core as core
from downloader_core import DownloadProgress, MediaInfo


@pytest.fixture
def client(media_dirs, monkeypatch):
    app_module.jobs.clear()
    app_module.cancel_events.clear()
    monkeypatch.setattr(core, "open_folder", lambda path: True)
    with TestClient(app_module.app) as c:
        yield c
    app_module.jobs.clear()


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def job_status(client, job_id):
    return next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == job_id)["status"]


# --- basic endpoints ---------------------------------------------------------

def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "YouTube Downloader Pro" in r.text


def test_system_status(client):
    # Regression: this endpoint raised AttributeError (yt_dlp.version is a module).
    r = client.get("/api/system")
    assert r.status_code == 200
    data = r.json()
    assert data["yt_dlp"]["version"] == core.yt_dlp.version.__version__
    assert "1440p" in data["options"]["resolutions"]


def test_no_wildcard_cors(client):
    r = client.options("/api/download", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
    })
    assert r.headers.get("access-control-allow-origin") is None


def test_info_success(client, monkeypatch):
    monkeypatch.setattr(core, "extract_info", lambda url: MediaInfo(
        url=url, title="T", thumbnail="", uploader="U", duration=5, duration_str="00:05",
        views=1, available_resolutions=["auto"]))
    r = client.post("/api/info", json={"url": "https://youtu.be/x"})
    assert r.json()["success"] is True
    assert r.json()["data"]["title"] == "T"


def test_info_error_and_empty(client, monkeypatch):
    def boom(url):
        raise ValueError("Video unavailable")
    monkeypatch.setattr(core, "extract_info", boom)
    assert client.post("/api/info", json={"url": "https://youtu.be/x"}).json() == {
        "success": False, "error": "Video unavailable"}
    assert client.post("/api/info", json={"url": "  "}).status_code == 400


# --- validation --------------------------------------------------------------

@pytest.mark.parametrize("item", [
    {"url": "https://youtu.be/x", "mode": "gif"},
    {"url": "https://youtu.be/x", "resolution": "999p"},
    {"url": "https://youtu.be/x", "mode": "audio", "audio_fmt": "aiff"},
    {"url": "https://youtu.be/x", "start_time": "1:00", "end_time": "0:30"},
    {"url": "https://youtu.be/x", "start_time": "abc"},
    {"url": "file:///etc/passwd"},
    {"url": ""},
])
def test_download_validation(client, item):
    assert client.post("/api/download", json={"items": [item]}).status_code == 422


def test_download_requires_items(client):
    assert client.post("/api/download", json={"items": []}).status_code == 400


# --- job lifecycle -----------------------------------------------------------

def test_download_job_success_and_websocket(client, monkeypatch, media_dirs):
    audio, _ = media_dirs
    received_opts = {}

    def fake_download(options, progress_callback=None, job_id="", cancel_event=None):
        received_opts["o"] = options
        progress_callback(DownloadProgress(job_id, options.title, "downloading", percent=50.0))
        out = audio / "Song_mp3.mp3"
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(core, "perform_download", fake_download)

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        r = client.post("/api/download", json={"items": [{
            "url": "https://youtu.be/x", "title": "Song", "mode": "audio",
            "start_time": " ", "end_time": "0:30"}]})
        assert r.status_code == 200 and r.json()["queued_count"] == 1
        job_id = r.json()["job_ids"][0]

        types = []
        while "finished" not in types:
            msg = ws.receive_json()
            types.append(msg["type"])
        assert "queued" in types and "progress" in types

    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == job_id)
    assert job["status"] == "finished"
    assert job["filename"] == "Song_mp3.mp3"
    assert job["title"] == "Song"  # regression: no longer overwritten by "Media Download"
    # Blank start_time is normalised to None; end-only trimming is allowed.
    assert received_opts["o"].start_time is None and received_opts["o"].end_time == "0:30"


def test_download_job_error(client, monkeypatch):
    def fail(options, **kw):
        raise RuntimeError("Video unavailable")
    monkeypatch.setattr(core, "perform_download", fail)
    job_id = client.post("/api/download", json={"items": [{"url": "https://youtu.be/x"}]}).json()["job_ids"][0]
    assert wait_for(lambda: job_status(client, job_id) == "error")
    job = client.get("/api/jobs").json()["jobs"][0]
    assert job["error_msg"] == "Video unavailable"


def test_cancel_running_job(client, monkeypatch):
    started = threading.Event()

    def slow_download(options, progress_callback=None, job_id="", cancel_event=None):
        started.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        raise core.DownloadCancelled("cancelled")

    monkeypatch.setattr(core, "perform_download", slow_download)
    job_id = client.post("/api/download", json={"items": [{"url": "https://youtu.be/x"}]}).json()["job_ids"][0]
    assert started.wait(5)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert wait_for(lambda: job_status(client, job_id) == "cancelled")
    # Cancelling again is a conflict; unknown jobs are 404.
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_clear_finished_jobs(client, monkeypatch, media_dirs):
    _, videos = media_dirs

    def ok(options, **kw):
        out = videos / "v.mp4"
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(core, "perform_download", ok)
    job_id = client.post("/api/download", json={"items": [{"url": "https://youtu.be/x"}]}).json()["job_ids"][0]
    assert wait_for(lambda: job_status(client, job_id) == "finished")
    r = client.delete("/api/jobs")
    assert r.json()["removed"] == [job_id]
    assert client.get("/api/jobs").json()["jobs"] == []


# --- files & folders ---------------------------------------------------------

def test_history_and_file_serving(client, media_dirs):
    audio, videos = media_dirs
    (videos / "PL").mkdir()
    (videos / "PL" / "a b.mp4").write_bytes(b"video-bytes")
    files = client.get("/api/history").json()["files"]
    assert files[0]["media_rel"] == "PL/a b.mp4"
    r = client.get("/api/files/video/PL/a%20b.mp4")
    assert r.status_code == 200 and r.content == b"video-bytes"
    assert client.get("/api/files/video/missing.mp4").status_code == 404
    assert client.get("/api/files/other/x.mp4").status_code == 404


@pytest.mark.parametrize("path", [
    "..%2F..%2Fapp.py",
    "PL/..%2F..%2F..%2Fapp.py",
    "..%2Fvideos_evil%2Fsecret.mp4",  # regression: str.startswith prefix bypass
])
def test_file_path_traversal_blocked(client, media_dirs, path):
    _, videos = media_dirs
    (videos / "PL").mkdir(exist_ok=True)
    evil = videos.parent / "videos_evil"
    evil.mkdir(exist_ok=True)
    (evil / "secret.mp4").write_bytes(b"secret")
    r = client.get(f"/api/files/video/{path}")
    assert r.status_code in (403, 404)
    assert r.content != b"secret"


def test_delete_file(client, media_dirs):
    audio, _ = media_dirs
    f = audio / "x_mp3.mp3"
    f.write_bytes(b"1")
    assert client.delete("/api/files/audio/x_mp3.mp3").status_code == 200
    assert not f.exists()
    assert client.delete("/api/files/audio/x_mp3.mp3").status_code == 404
    assert client.delete("/api/files/audio/..%2F..%2Fapp.py").status_code in (403, 404)


def test_open_folder_guarded(client, media_dirs):
    audio, _ = media_dirs
    ok = client.post("/api/open-folder", json={"type": "audio", "folder": "PL"})
    assert ok.status_code == 200 and ok.json()["success"]
    # Regression: folder traversal was accepted and opened arbitrary directories.
    assert client.post("/api/open-folder", json={"type": "audio", "folder": "../.."}).status_code == 403
    assert client.post("/api/open-folder", json={"type": "audio", "folder": "C:/Windows"}).status_code == 403
    assert client.post("/api/open-folder", json={"type": "exe"}).status_code == 422
