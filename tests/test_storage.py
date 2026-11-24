import json

import pytest

from clipstudio import storage
from clipstudio.jobs import JobStore
from clipstudio.sources import friendly_error


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_LOGS", str(tmp_path / "cfg"))
    return tmp_path


def test_default_has_no_folder(cfg):
    assert storage.chosen_folder() is None
    assert storage.work_root(cfg / "data") == cfg / "data"


def test_set_folder_moves_work_and_remembers_old(cfg):
    target = cfg / "D" / "MasterClip"
    storage.set_folder(str(target), cfg / "data")
    assert storage.chosen_folder() == target
    assert storage.work_root(cfg / "data") == target / storage.WORK_NAME
    assert cfg / "data" in storage.old_work_roots()
    storage.set_folder(None, target / storage.WORK_NAME)
    assert storage.chosen_folder() is None


def test_relative_or_missing_drive_refused(cfg):
    with pytest.raises(ValueError):
        storage.check_writable("relative\\folder")
    with pytest.raises(ValueError):
        storage.check_writable("")


def test_ensure_room(cfg, monkeypatch):
    monkeypatch.setattr(storage, "free_bytes", lambda f: 500 * 1024**2)
    with pytest.raises(ValueError, match="almost full"):
        storage.ensure_room(cfg)
    monkeypatch.setattr(storage, "free_bytes", lambda f: 50 * 1024**3)
    storage.ensure_room(cfg)


def test_jobstore_new_jobs_go_to_work_root_and_old_jobs_stay(tmp_path):
    root, work = tmp_path / "data", tmp_path / "work"
    store = JobStore(root)
    old = store.create("old")
    store.set_work_root(work)
    new = store.create("new")
    assert store.folder(new["id"]).parent == work
    assert store.folder(old["id"]).parent == root
    again = JobStore(root, work, [root])
    assert set(again.jobs) == {old["id"], new["id"]}
    assert json.loads((work / new["id"] / "job.json").read_text(encoding="utf-8"))["title"] == "new"


def test_disk_full_message():
    log = "ERROR: unable to write data: [Errno 28] No space left on device"
    assert "disk is full" in friendly_error(log, "fallback")


def test_unknown_error_shows_reason_without_links():
    log = "ERROR: [youtube] abc: Something odd https://secret.example/x?sig=1 happened"
    msg = friendly_error(log, "We couldn't get this video.")
    assert msg.startswith("We couldn't get this video.")
    assert "Something odd" in msg and "secret.example" not in msg
