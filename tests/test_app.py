import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_DATA", str(tmp_path))
    import clipstudio.app as module

    importlib.reload(module)
    with TestClient(module.app) as client:
        yield client


def test_invalid_source_does_not_start_job(client):
    response = client.post(
        "/api/jobs", json={"source_type": "path", "source": "missing.mp4"}
    )
    assert response.status_code == 400
    assert client.get("/api/jobs").json()["jobs"] == []


def test_rejects_local_network_url(client):
    response = client.post(
        "/api/jobs", json={"source_type": "url", "source": "http://127.0.0.1/private"}
    )
    assert response.status_code == 400


def test_unknown_job_and_traversal_not_accessible(client):
    assert client.get("/api/jobs/invalid").status_code == 404
    assert client.get("/api/jobs/invalid/files/secret.txt").status_code == 404


def test_export_rejects_out_of_bounds_clips(client):
    from clipstudio import app as module

    job = module.store.create("test")
    module.store.update(job["id"], status="ready", duration=20, source_path="test.mp4")
    response = client.post(
        "/api/jobs/" + job["id"] + "/export", json={"clips": [{"start": 10, "end": 30}]}
    )
    assert response.status_code == 400


def test_health_and_ui(client):
    assert client.get("/api/health").json()["ffmpeg"] is True
    response = client.get("/")
    assert response.status_code == 200
    assert "MasterClip" in response.text


def test_host_guard_blocks_remote_browser_requests(client):
    assert client.get("/api/jobs", headers={"host": "evil.example"}).status_code == 400


def test_custom_moment_can_export_when_detection_found_nothing(client, monkeypatch):
    from clipstudio import app as module

    job = module.store.create("silent")
    module.store.update(
        job["id"], status="ready", duration=60, source_path="test.mp4", candidates=[]
    )
    monkeypatch.setattr(module.pool, "submit", lambda *args: None)
    response = client.post(
        "/api/jobs/" + job["id"] + "/export", json={"clips": [{"start": 5, "end": 20}]}
    )
    assert response.status_code == 202


def test_transcript_upload_rejects_cues_past_video_end(client):
    from clipstudio import app as module

    job = module.store.create("test")
    module.store.update(job["id"], status="ready", duration=20)
    response = client.post(
        "/api/jobs/" + job["id"] + "/transcript",
        files={
            "file": (
                "captions.json",
                '[{"start":1,"end":30,"text":"hello"}]',
                "application/json",
            )
        },
    )
    assert response.status_code == 400
