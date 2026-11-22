import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_DATA", str(tmp_path))
    import clipstudio.app as module

    importlib.reload(module)
    with TestClient(module.app) as value:
        yield value


def test_product_queues_full_pipeline_with_captions_and_horizontal_defaults(
    client, tmp_path, monkeypatch
):
    from clipstudio import app as module

    path = tmp_path / "video.mp4"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(module.pool, "submit", lambda *args: None)
    response = client.post(
        "/api/projects",
        json={"source_type": "path", "source": str(path), "format": "horizontal"},
    )
    assert response.status_code == 202
    job = client.get("/api/jobs/" + response.json()["id"]).json()
    assert job["auto_export"]["mode"] == "highlights"
    assert job["auto_export"]["burn_captions"] is True
    assert job["auto_export"]["target_seconds"] == 210


def test_vertical_project_and_explicit_caption_off(client, tmp_path, monkeypatch):
    from clipstudio import app as module

    path = tmp_path / "video.mp4"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(module.pool, "submit", lambda *args: None)
    response = client.post(
        "/api/projects",
        json={
            "source_type": "path",
            "source": str(path),
            "format": "vertical",
            "captions": False,
        },
    )
    assert response.status_code == 202
    assert response.json()["auto_export"]["mode"] == "shorts"
    assert response.json()["auto_export"]["layout"] == "center"
    assert response.json()["auto_export"]["burn_captions"] is False


def test_caption_correction_discards_stale_word_times(client):
    from clipstudio import app as module

    job = module.store.create("video", duration=60, status="ready")
    original = [
        {
            "start": 5,
            "end": 7,
            "text": "wrong words",
            "words": [
                {"start": 5, "end": 6, "text": "wrong"},
                {"start": 6, "end": 7, "text": "words"},
            ],
        }
    ]
    (module.store.folder(job["id"]) / "transcript.json").write_text(
        json.dumps(original)
    )
    response = client.put(
        "/api/jobs/" + job["id"] + "/captions",
        json={"segments": [{"start": 5, "end": 7, "text": "Correct caption"}]},
    )
    assert response.status_code == 200
    cue = client.get("/api/jobs/" + job["id"] + "/captions").json()["segments"][0]
    assert cue["text"] == "Correct caption"
    assert not cue.get("words")


def test_caption_correction_cannot_extend_past_video(client):
    from clipstudio import app as module

    job = module.store.create("video", duration=10, status="ready")
    response = client.put(
        "/api/jobs/" + job["id"] + "/captions",
        json={"segments": [{"start": 9, "end": 20, "text": "Outside video"}]},
    )
    assert response.status_code == 400


def test_discovery_validation_and_unknown_search(client):
    assert client.post("/api/discover", json={"topic": " "}).status_code == 422
    assert client.get("/api/discover/missing").status_code == 404


def test_finished_stream_selection_requires_url(client, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fixture")
    response = client.post(
        "/api/projects",
        json={"source_type": "path", "source": str(path), "from_discovery": True},
    )
    assert response.status_code == 400


def test_long_recording_can_rebuild_more_than_200_detected_moments(client, monkeypatch):
    from clipstudio import app as module

    job = module.store.create(
        "Long stream", duration=10000, status="ready", source_path="video.mp4"
    )
    monkeypatch.setattr(module.pool, "submit", lambda *args: None)
    response = client.post(
        "/api/jobs/" + job["id"] + "/export",
        json={"clips": [{"start": i * 30, "end": i * 30 + 10} for i in range(201)]},
    )
    assert response.status_code == 202
